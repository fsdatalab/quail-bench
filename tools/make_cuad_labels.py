"""Write the CUAD reference labels as a label collection.

    uv run python tools/make_cuad_labels.py <root> [--scale-factor 0.1]
    uv run python tools/make_cuad_labels.py <root> --extend-published [--upload]

The CUAD labels come from the dataset's own annotations, not from a
judge model: a category's Yes or No answer in master_clauses.csv
labels its filter and the join's clause type, the normalized
governing law names the classification's label, and the spans marked
in CUAD_v1.json are an extraction's answers.

The first form writes a local mirror: the contracts and clause_types
tables under the published corpus id, so the benchmark accepts the
root, one label set per CUAD predicate, and a collection naming only
them. Run the CUAD queries against it with `quail_b.run(..., root=<root>)`.

The second form writes the files that add the CUAD label sets to the
published ground truth: label sets whose ids come from the published
corpus, one new collection per active collection of that corpus with
the CUAD label sets added, and the pointers naming the new
collections. The corpus files are not written; the benchmark builds
the CUAD tables from their sources. With `--upload`, the files are
copied to the public bucket with the AWS credentials of the machine
running the command. Afterwards, remove `labels_pending` from the
CUAD entries of the workload catalog.
"""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download
from pyarrow import fs

from quail_b import data, prompts
from quail_b._files import PUBLIC_BUCKET, _list_files
from quail_b.data import (
    CORPUS_COLUMNS,
    DATA_SEED,
    GROUND_TRUTH_ROOT,
    PUBLISHED_CORPORA,
    SOURCE_REVISIONS,
    corpus_identity,
)
from quail_b.labels import _read_json, label_columns
from quail_b.predicates import (
    CLASSIFY_PREDICATES,
    EXTRACT_PREDICATES,
    PREDICATES,
    label_set_identity,
    predicate_payload,
)
from quail_b.run import _write_json

CUAD_PREDICATES = tuple(
    spec for spec in PREDICATES + CLASSIFY_PREDICATES + EXTRACT_PREDICATES
    if spec.workload == "cuad")
CATEGORY_OF = {spec.template: category
               for category in prompts.CUAD_QUESTIONS
               for spec in CUAD_PREDICATES
               if spec.template in (prompts.cuad_filter(category)
                                    if category not in data.CUAD_FIELD_CATEGORIES
                                    else (), prompts.CUAD_QUESTIONS[category])}


def _csv_rows() -> dict[str, dict]:
    """Return the master clauses rows by the contract title key."""
    path = hf_hub_download(data.CUAD_REPO, data.CUAD_CSV, repo_type="dataset",
                           revision=SOURCE_REVISIONS[data.CUAD_REPO])
    with open(path, encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    return {data.cuad_title_key(row["Filename"].rsplit(".", 1)[0]): row
            for row in rows}


def _yes(row: dict, category: str) -> bool:
    return row[f"{category}-Answer"].strip() == "Yes"


def governing_law_label(answer: str) -> str:
    """Return the classification label of a normalized governing law."""
    answer = answer.strip()
    if not answer:
        return "not stated"
    if answer in prompts.CUAD_GOVERNING_LAW_LABELS:
        return answer
    return "another state or country"


def _spans(contract: dict, category: str) -> list[str]:
    """Return the distinct marked spans of one category, in order."""
    for qa in contract["paragraphs"][0]["qas"]:
        if qa["question"].split('"')[1] == category:
            return list(dict.fromkeys(answer["text"] for answer in qa["answers"]))
    raise KeyError(category)


def label_rows(spec, contracts: pa.Table, clause_types: pa.Table,
               csv_rows: dict, source: dict) -> list[tuple]:
    """Return one predicate's label rows: (left_id, right_id, answer)."""
    ids = contracts.column("id").to_pylist()
    titles = contracts.column("title").to_pylist()
    rows = []
    for row_id, title in zip(ids, titles):
        key = data.cuad_title_key(title)
        csv_row = csv_rows[key]
        if spec.kind == "filter":
            rows.append((row_id, None, _yes(csv_row, CATEGORY_OF[spec.template])))
        elif spec.kind == "join":
            for type_id, name in zip(clause_types.column("id").to_pylist(),
                                     clause_types.column("name").to_pylist()):
                rows.append((row_id, type_id, _yes(csv_row, name)))
        elif spec.kind == "classify":
            rows.append((row_id, None,
                         governing_law_label(csv_row["Governing Law-Answer"])))
        else:
            rows.append((row_id, None,
                         _spans(source[key], CATEGORY_OF[spec.template])))
    return rows


def write_label_sets(root: Path, corpus_id: str, corpus_full_hash: str,
                     tables: dict, csv_rows: dict, source: dict) -> dict:
    """Write one label set per CUAD predicate under root.

    Returns:
        The label set id of each predicate, by predicate key.
    """
    label_sets = {}
    for spec in CUAD_PREDICATES:
        ids = label_set_identity(spec, corpus_id, corpus_full_hash)
        label_set_id = ids["label_set_id"]
        rows = label_rows(spec, tables["contracts"], tables["clause_types"],
                          csv_rows, source)
        column, *_ = label_columns(spec.kind)[2:]
        values = [answer for _, _, answer in rows]
        table = pa.table({
            "predicate_key": [spec.key] * len(rows),
            "label_set_id": [label_set_id] * len(rows),
            "left_id": pa.array([left for left, _, _ in rows], pa.string()),
            "right_id": pa.array([right for _, right, _ in rows], pa.string()),
            column: pa.array(values, pa.list_(pa.string())
                             if spec.kind == "extract" else None),
        })
        directory = (root / GROUND_TRUTH_ROOT / "label_sets" / spec.workload
                     / spec.slug / label_set_id)
        directory.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, directory / "labels.parquet")
        predicate = {
            "key": spec.key, "kind": spec.kind, "template": spec.template,
            "left_table": spec.left_table, "left_column": spec.left_column,
            "right_table": spec.right_table, "right_column": spec.right_column,
        }
        if spec.kind == "classify":
            predicate["labels"] = list(spec.labels)
        _write_json(directory / "manifest.json", {
            "status": "complete", "label_set_id": label_set_id,
            "corpus_id": corpus_id,
            "corpus_full_hash": corpus_full_hash,
            "rows": len(rows), "source_rows": {"cuad_annotation": len(rows)},
            "predicate": predicate, "predicate_payload": predicate_payload(spec),
            "sources": ids["sources"],
        })
        label_sets[spec.key] = label_set_id
    return label_sets


def write_collection(root: Path, scale_factor: float, tables: dict,
                     csv_rows: dict, source: dict) -> str:
    """Write the corpus mirror, the label sets, and the collection.

    Returns:
        The collection id.
    """
    corpus_id = PUBLISHED_CORPORA[scale_factor]
    identity = corpus_identity(tables, scale_factor, DATA_SEED,
                               SOURCE_REVISIONS)
    corpus = root / GROUND_TRUTH_ROOT / "corpora" / corpus_id
    corpus.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        pq.write_table(table, corpus / f"{name}.parquet")
    manifest = {**identity, "corpus_id": corpus_id,
                "columns": {name: list(CORPUS_COLUMNS[name]) for name in tables}}
    _write_json(corpus / "manifest.json", manifest)
    label_sets = write_label_sets(root, corpus_id, identity["corpus_full_hash"],
                                  tables, csv_rows, source)
    payload = {"schema_version": 1, "benchmark": "quailb",
               "scale_factor": scale_factor, "corpus_id": corpus_id,
               "workload": "cuad", "label_sets": label_sets}
    collection_id = f"gtw_{data._full_hash(payload)[:32]}"
    collection = root / GROUND_TRUTH_ROOT / "collections" / collection_id
    collection.mkdir(parents=True, exist_ok=True)
    _write_json(collection / "manifest.json", {
        **payload, "status": "complete", "collection_id": collection_id,
        "summary": {"model": None, "source": "cuad_annotation"}})
    _write_json(corpus / "active_collection.json",
                {"collection_id": collection_id})
    return collection_id


def extend_collections(root: Path, scale_factor: float, label_sets: dict,
                       published_root=None) -> dict:
    """Write one collection per active collection, with these label sets added.

    Each new collection keeps the label sets and reuse records of the
    active collection it extends, and the pointer that named the old
    collection is rewritten under root to name the new one.

    Args:
        root: Directory that receives the files, in the bucket's layout.
        scale_factor: The published corpus to extend.
        label_sets: The label set id to add, by predicate key.
        published_root: Where the published files are read from; None
            for the public bucket.

    Returns:
        The new collection id by pointer file name.
    """
    corpus_id = PUBLISHED_CORPORA[scale_factor]
    corpus_prefix = f"{GROUND_TRUTH_ROOT}/corpora/{corpus_id}"
    corpus = _read_json(published_root, f"{corpus_prefix}/manifest.json")
    pointers = [path for path in _list_files(published_root, corpus_prefix)
                if Path(path).name.startswith("active_collection.")]
    if not pointers:
        raise FileNotFoundError(f"{corpus_id} has no active collection")
    written = {}
    for pointer in pointers:
        old_id = _read_json(published_root, pointer)["collection_id"]
        old = _read_json(
            published_root,
            f"{GROUND_TRUTH_ROOT}/collections/{old_id}/manifest.json")
        if old.get("status") != "complete" or old.get("corpus_id") != corpus_id:
            raise ValueError(f"active collection {old_id} is not complete "
                             f"for corpus {corpus_id}")
        payload = {
            "schema_version": 1, "benchmark": "quailb",
            "scale_factor": corpus["scale_factor"], "corpus_id": corpus_id,
            "label_sets": dict(sorted({**old["label_sets"], **label_sets}.items())),
        }
        full = data._full_hash(payload)
        collection_id = f"gt_{full[:32]}"
        directory = root / GROUND_TRUTH_ROOT / "collections" / collection_id
        directory.mkdir(parents=True, exist_ok=True)
        summary = {"model": None, "source": "cuad_annotation",
                   "extends_collection": old_id,
                   "added_predicates": sorted(label_sets)}
        _write_json(directory / "manifest.json", {
            **payload, "collection_id": collection_id,
            "collection_full_hash": full, "status": "complete",
            "corpus_manifest": old.get("corpus_manifest"),
            "reused_label_sets": old.get("reused_label_sets", {}),
            "summary": summary})
        _write_json(directory / "summary.json", summary)
        pointer_path = root / pointer
        pointer_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(pointer_path, {"collection_id": collection_id})
        written[Path(pointer).name] = collection_id
    return written


def upload(root: Path, destination: str = PUBLIC_BUCKET,
           filesystem=None) -> None:
    """Copy the ground truth files under root to the bucket.

    Args:
        root: Directory in the bucket's layout.
        destination: Bucket name, or a directory on `filesystem`.
        filesystem: The destination filesystem; None for S3 with the
            machine's AWS credentials.
    """
    if filesystem is None:
        filesystem = fs.S3FileSystem()
    fs.copy_files(str(root / GROUND_TRUTH_ROOT), f"{destination}/{GROUND_TRUTH_ROOT}",
                  source_filesystem=fs.LocalFileSystem(),
                  destination_filesystem=filesystem)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--scale-factor", type=float, default=0.1)
    parser.add_argument("--extend-published", action="store_true",
                        help="add the label sets to the published collections")
    parser.add_argument("--upload", action="store_true",
                        help="copy the written files to the public bucket")
    arguments = parser.parse_args(argv)
    if arguments.upload and not arguments.extend_published:
        parser.error("--upload needs --extend-published")
    with tempfile.TemporaryDirectory() as built:
        data._build_cuad(Path(built), arguments.scale_factor, force=True)
        tables = {name: pq.read_table(Path(built) / f"{name}.parquet")
                  for name in ("contracts", "clause_types")}
    source = {data.cuad_title_key(contract["title"]): contract
              for contract in data.cuad_source()}
    result = {"root": str(arguments.root),
              "scale_factor": arguments.scale_factor,
              "contracts": tables["contracts"].num_rows}
    if arguments.extend_published:
        corpus_id = PUBLISHED_CORPORA[arguments.scale_factor]
        corpus = _read_json(
            None, f"{GROUND_TRUTH_ROOT}/corpora/{corpus_id}/manifest.json")
        label_sets = write_label_sets(
            arguments.root, corpus_id, corpus["corpus_full_hash"], tables,
            _csv_rows(), source)
        result["collections"] = extend_collections(
            arguments.root, arguments.scale_factor, label_sets)
        if arguments.upload:
            upload(arguments.root)
            result["uploaded_to"] = PUBLIC_BUCKET
    else:
        result["collection_id"] = write_collection(
            arguments.root, arguments.scale_factor, tables, _csv_rows(),
            source)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
