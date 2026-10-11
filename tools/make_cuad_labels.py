"""Publish the CUAD reference labels.

    uv run python tools/make_cuad_labels.py <root> [--upload]

The CUAD labels come from the dataset's own annotations, not from a
judge model: a category's Yes or No answer in master_clauses.csv
labels its filter and the join's clause type, the normalized
governing law names the classification's label, and the spans marked
in CUAD_v1.json are an extraction's answers.

For each published scale factor, the tool writes under root the files
that add the CUAD label sets to the published ground truth: one label
set per CUAD predicate, with ids from the published corpus; one new
collection per active collection of that corpus, holding the old
label sets and the CUAD ones; and the pointers naming the new
collections. The corpus files are not written, because the benchmark
builds the CUAD tables from their sources. With `--upload`, the files
are copied to the public bucket with the AWS credentials of the
machine running the command, and the pointers are read back from the
bucket to confirm the upload.
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

from quail_b import data, prompts
from quail_b._files import PUBLIC_BUCKET, _list_files
from quail_b.data import GROUND_TRUTH_ROOT, PUBLISHED_CORPORA, SOURCE_REVISIONS
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
        The new collection id by pointer path in the bucket.
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
        written[pointer] = collection_id
    return written


def upload(root: Path, bucket: str = PUBLIC_BUCKET, client=None) -> list[str]:
    """Copy every file under root to the bucket; returns the keys written.

    Args:
        root: Directory in the bucket's layout.
        bucket: The destination bucket.
        client: A boto3 S3 client; None for one with the machine's
            AWS credentials.
    """
    if client is None:
        import boto3

        client = boto3.client("s3")
    keys = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            key = path.relative_to(root).as_posix()
            client.upload_file(str(path), bucket, key)
            keys.append(key)
    return keys


def check_published(expected: dict) -> None:
    """Raise unless the bucket's pointers name these collections.

    Args:
        expected: The collection id by pointer path, as
            `extend_collections` returns them.
    """
    for pointer, collection_id in expected.items():
        published = _read_json(None, pointer)["collection_id"]
        if published != collection_id:
            raise RuntimeError(
                f"{pointer} names {published}, expected {collection_id}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--upload", action="store_true",
                        help="copy the written files to the public bucket")
    arguments = parser.parse_args(argv)
    source = {data.cuad_title_key(contract["title"]): contract
              for contract in data.cuad_source()}
    csv_rows = _csv_rows()
    collections = {}
    for scale_factor, corpus_id in sorted(PUBLISHED_CORPORA.items()):
        with tempfile.TemporaryDirectory() as built:
            data._build_cuad(Path(built), scale_factor, force=True)
            tables = {name: pq.read_table(Path(built) / f"{name}.parquet")
                      for name in ("contracts", "clause_types")}
        corpus = _read_json(
            None, f"{GROUND_TRUTH_ROOT}/corpora/{corpus_id}/manifest.json")
        label_sets = write_label_sets(
            arguments.root, corpus_id, corpus["corpus_full_hash"], tables,
            csv_rows, source)
        collections.update(extend_collections(
            arguments.root, scale_factor, label_sets))
    result = {"root": str(arguments.root), "collections": collections}
    if arguments.upload:
        result["uploaded"] = len(upload(arguments.root))
        check_published(collections)
        result["bucket"] = PUBLIC_BUCKET
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
