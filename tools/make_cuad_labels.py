"""Write the CUAD reference labels as a local label collection.

    uv run python tools/make_cuad_labels.py <root> [--scale-factor 0.1]

The CUAD labels come from the dataset's own annotations, not from a
judge model: a category's Yes or No answer in master_clauses.csv
labels its filter and the join's clause type, the normalized
governing law names the classification's label, and the spans marked
in CUAD_v1.json are an extraction's answers. The tool builds the
contracts and clause_types tables for the scale factor, writes them
under the published corpus id so the benchmark accepts the root as a
mirror, and writes one label set per CUAD predicate and a collection
naming them all. Run the CUAD queries with `quail_b.run(..., root=<root>)`.
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
from quail_b.data import (
    CORPUS_COLUMNS,
    DATA_SEED,
    GROUND_TRUTH_ROOT,
    PUBLISHED_CORPORA,
    SOURCE_REVISIONS,
    corpus_identity,
)
from quail_b.labels import label_columns
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
    label_sets = {}
    for spec in CUAD_PREDICATES:
        ids = label_set_identity(spec, corpus_id, identity["corpus_full_hash"])
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
            "corpus_full_hash": identity["corpus_full_hash"],
            "rows": len(rows), "source_rows": {"cuad_annotation": len(rows)},
            "predicate": predicate, "predicate_payload": predicate_payload(spec),
            "sources": ids["sources"],
        })
        label_sets[spec.key] = label_set_id
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


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--scale-factor", type=float, default=0.1)
    arguments = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as built:
        data._build_cuad(Path(built), arguments.scale_factor, force=True)
        tables = {name: pq.read_table(Path(built) / f"{name}.parquet")
                  for name in ("contracts", "clause_types")}
    source = {data.cuad_title_key(contract["title"]): contract
              for contract in data.cuad_source()}
    collection_id = write_collection(arguments.root, arguments.scale_factor,
                                     tables, _csv_rows(), source)
    print(json.dumps({"root": str(arguments.root),
                      "scale_factor": arguments.scale_factor,
                      "contracts": tables["contracts"].num_rows,
                      "collection_id": collection_id}))


if __name__ == "__main__":
    main()
