"""Tests for the tool that publishes the corpora with the CUAD tables."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quail_b.data import (
    CLAUSE_TYPE_SCHEMA,
    CONTRACT_SCHEMA,
    DATA_SEED,
    GROUND_TRUTH_ROOT,
    SOURCE_REVISIONS,
    corpus_identity,
)
from tools.make_cuad_labels import (
    BASE_CORPORA,
    upload,
    write_collections,
    write_corpus,
)

SF = 0.1
BASE_ID = BASE_CORPORA[SF]
BASE_TABLES = {"reviews": pa.table({"id": ["r0", "r1"], "body": ["a", "b"]})}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _published(root: Path) -> str:
    """A base corpus with one active collection; returns its pointer."""
    corpus = root / GROUND_TRUTH_ROOT / "corpora" / BASE_ID
    identity = corpus_identity(BASE_TABLES, SF, DATA_SEED, SOURCE_REVISIONS)
    _write(corpus / "manifest.json", {
        **identity, "columns": {"reviews": ["id", "body"]}})
    _write(root / GROUND_TRUTH_ROOT / "label_sets" / "imdb" / "f" / "ls_new"
           / "manifest.json", {"predicate": {"kind": "filter",
                                             "left_table": "reviews"}})
    reused = {"source_corpus_id": "c_x", "source_collection_id": "gt_x"}
    _write(root / GROUND_TRUTH_ROOT / "collections" / "gt_base" / "manifest.json",
           {"status": "complete", "collection_id": "gt_base",
            "corpus_id": BASE_ID, "scale_factor": SF,
            "label_sets": {"old": "ls_old", "new": "ls_new"},
            "reused_label_sets": {"old": reused},
            "summary": {"model": "qwen3-32b-fp8"}})
    pointer = f"{GROUND_TRUTH_ROOT}/corpora/{BASE_ID}/active_collection.raw-v1.json"
    _write(root / pointer, {"collection_id": "gt_base"})
    return pointer


def _built(directory: Path) -> dict[str, pa.Table]:
    directory.mkdir()
    tables = {
        "contracts": CONTRACT_SCHEMA.empty_table(),
        "clause_types": pa.table({"id": ["ty0"], "name": ["Audit Rights"],
                                  "question": ["q"]}, schema=CLAUSE_TYPE_SCHEMA),
    }
    for name, table in tables.items():
        pq.write_table(table, directory / f"{name}.parquet")
    return tables


def test_write_corpus_adds_the_cuad_tables_to_the_base_corpus(tmp_path):
    published = tmp_path / "published"
    _published(published)
    tables = _built(tmp_path / "built")
    out = tmp_path / "out"

    corpus = write_corpus(out, BASE_ID, tmp_path / "built",
                          published_root=published)

    expected = corpus_identity({**BASE_TABLES, **tables}, SF, DATA_SEED,
                               SOURCE_REVISIONS)
    assert corpus["corpus_id"] == expected["corpus_id"]
    assert corpus["tables"] == expected["tables"]
    assert set(corpus["columns"]) == {"reviews", "contracts", "clause_types"}
    directory = out / GROUND_TRUTH_ROOT / "corpora" / corpus["corpus_id"]
    # the base tables are copied inside the bucket by `upload`
    assert {path.name for path in directory.iterdir()} == {
        "manifest.json", "contracts.parquet", "clause_types.parquet"}


def test_write_collections_reuses_the_base_label_sets(tmp_path):
    published = tmp_path / "published"
    pointer = _published(published)
    _built(tmp_path / "built")
    out = tmp_path / "out"
    corpus = write_corpus(out, BASE_ID, tmp_path / "built",
                          published_root=published)
    added = {"quailb.cuad.contract.x": "ls_x"}

    written = write_collections(out, BASE_ID, corpus, added,
                                published_root=published)

    new_pointer = pointer.replace(BASE_ID, corpus["corpus_id"])
    assert list(written) == [new_pointer]
    collection_id = written[new_pointer]
    manifest = json.loads((out / GROUND_TRUTH_ROOT / "collections"
                           / collection_id / "manifest.json").read_text())
    assert manifest["corpus_id"] == corpus["corpus_id"]
    assert manifest["label_sets"] == {
        "new": "ls_new", "old": "ls_old", **added}
    base = json.loads((published / GROUND_TRUTH_ROOT / "corpora" / BASE_ID
                       / "manifest.json").read_text())
    assert manifest["reused_label_sets"] == {
        "new": {"required_tables": ["reviews"],
                "source_collection_id": "gt_base",
                "source_corpus_id": BASE_ID,
                "verified_table_manifests": {
                    "reviews": base["tables"]["reviews"]}},
        "old": {"source_corpus_id": "c_x", "source_collection_id": "gt_x"},
    }
    assert json.loads((out / new_pointer).read_text()) == {
        "collection_id": collection_id}
    again = write_collections(tmp_path / "again", BASE_ID, corpus, added,
                              published_root=published)
    assert again == written


class FakeClient:
    def __init__(self):
        self.calls = []

    def upload_file(self, path, bucket, key):
        self.calls.append(("upload", bucket, key))

    def copy_object(self, Bucket, Key, CopySource):  # noqa: N803
        self.calls.append(("copy", Bucket, Key, CopySource["Key"]))


def test_upload_copies_base_tables_and_sends_pointers_last(tmp_path):
    published = tmp_path / "published"
    _published(published)
    _built(tmp_path / "built")
    out = tmp_path / "out"
    corpus = write_corpus(out, BASE_ID, tmp_path / "built",
                          published_root=published)
    write_collections(out, BASE_ID, corpus, {"k": "ls_k"},
                      published_root=published)
    client = FakeClient()

    keys = upload(out, "bucket", client=client)

    prefix = f"{GROUND_TRUTH_ROOT}/corpora"
    assert client.calls[0] == (
        "copy", "bucket", f"{prefix}/{corpus['corpus_id']}/reviews.parquet",
        f"{prefix}/{BASE_ID}/reviews.parquet")
    uploaded = [call[2] for call in client.calls if call[0] == "upload"]
    files = {path.relative_to(out).as_posix() for path in out.rglob("*")
             if path.is_file()}
    assert set(uploaded) == files
    assert Path(uploaded[-1]).name.startswith("active_collection.")
    assert keys == [call[2] for call in client.calls]
