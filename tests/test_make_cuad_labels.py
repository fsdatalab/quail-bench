"""Tests for the CUAD labels tool's published-collection mode."""

import json
from pathlib import Path

from quail_b.data import GROUND_TRUTH_ROOT, PUBLISHED_CORPORA
from tools.make_cuad_labels import extend_collections, upload

SF = 0.1
CORPUS_ID = PUBLISHED_CORPORA[SF]


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _published(root: Path) -> dict[str, str]:
    """A published corpus with two active collections; returns their ids."""
    corpus = root / GROUND_TRUTH_ROOT / "corpora" / CORPUS_ID
    _write(corpus / "manifest.json", {
        "corpus_id": CORPUS_ID, "corpus_full_hash": "f" * 64,
        "scale_factor": SF, "tables": {}})
    old = {}
    for pointer, sets in (("active_collection.json", {"a": "ls_a"}),
                          ("active_collection.raw-v1.json",
                           {"a": "ls_a", "b": "ls_b"})):
        collection_id = f"gt_{pointer}"
        _write(root / GROUND_TRUTH_ROOT / "collections" / collection_id
               / "manifest.json", {
                   "status": "complete", "collection_id": collection_id,
                   "corpus_id": CORPUS_ID, "scale_factor": SF,
                   "corpus_manifest": "/results/corpus/manifest.json",
                   "label_sets": sets,
                   "reused_label_sets": {"a": {"source_corpus_id": "c_x"}}})
        _write(corpus / pointer, {"collection_id": collection_id})
        old[f"{GROUND_TRUTH_ROOT}/corpora/{CORPUS_ID}/{pointer}"] = collection_id
    return old


def test_extend_collections_adds_label_sets_to_each_active_collection(tmp_path):
    published = tmp_path / "published"
    old = _published(published)
    out = tmp_path / "out"
    added = {"quailb.cuad.contract.x": "ls_x"}

    written = extend_collections(out, SF, added, published_root=published)

    assert set(written) == set(old)
    assert len(set(written.values())) == 2
    for pointer, collection_id in written.items():
        assert collection_id.startswith("gt_")
        manifest = json.loads(
            (out / GROUND_TRUTH_ROOT / "collections" / collection_id
             / "manifest.json").read_text())
        old_manifest = json.loads(
            (published / GROUND_TRUTH_ROOT / "collections" / old[pointer]
             / "manifest.json").read_text())
        assert manifest["label_sets"] == {**old_manifest["label_sets"], **added}
        assert manifest["status"] == "complete"
        assert manifest["corpus_id"] == CORPUS_ID
        assert manifest["reused_label_sets"] == old_manifest["reused_label_sets"]
        assert manifest["summary"]["extends_collection"] == old[pointer]
        assert json.loads((out / pointer).read_text()) == {
            "collection_id": collection_id}
    # The corpus files stay as published: only pointers are written there.
    corpus_files = {path.name for path in
                    (out / GROUND_TRUTH_ROOT / "corpora" / CORPUS_ID).iterdir()}
    assert corpus_files == {Path(pointer).name for pointer in old}


def test_extend_collections_is_deterministic(tmp_path):
    published = tmp_path / "published"
    _published(published)
    added = {"quailb.cuad.contract.x": "ls_x"}
    first = extend_collections(tmp_path / "one", SF, added,
                               published_root=published)
    second = extend_collections(tmp_path / "two", SF, added,
                                published_root=published)
    assert first == second


class FakeClient:
    def __init__(self):
        self.uploads = []

    def upload_file(self, path, bucket, key):
        self.uploads.append((Path(path).read_bytes(), bucket, key))


def test_upload_sends_every_file_under_root_with_its_bucket_key(tmp_path):
    published = tmp_path / "published"
    _published(published)
    out = tmp_path / "out"
    extend_collections(out, SF, {"k": "ls_k"}, published_root=published)
    client = FakeClient()

    keys = upload(out, "bucket", client=client)

    expected = sorted(path.relative_to(out).as_posix() for path in out.rglob("*")
                      if path.is_file())
    assert keys == expected
    assert [key for _, _, key in client.uploads] == expected
    assert {bucket for _, bucket, _ in client.uploads} == {"bucket"}
    assert all(content == (out / key).read_bytes()
               for content, _, key in client.uploads)
