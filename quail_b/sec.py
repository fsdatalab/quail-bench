"""Download, build, and validate the fixed SEC 8-K sample."""

import hashlib
import heapq
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from quail_b._files import GROUND_TRUTH_ROOT, _cached_file, _location, _read_bytes
from quail_b.data import DATA_SEED, corpus_identity

SEC_REPO = "TeraflopAI/SEC-EDGAR"
SEC_REVISION = "43de32c4c3cd4a56c26757345a4ec6db04fccda0"
SEC_DOCUMENTS = 100_000
SEC_SOURCE_FILES = 217
SEC_TABLE = "sec_filings"
SEC_CORPUS_ID = "c_efb9fde11a07a180f072cc379485c0dc"
SEC_SCHEMA = pa.schema([
    ("id", pa.string()),
    ("text", pa.string()),
    ("filing_date", pa.string()),
    ("filing_month", pa.string()),
])
SEC_SOURCE_COLUMNS = ("metadata_accession-number", "metadata_filing-date", "text")
# The event queries read long, recent filings: about 2,400 to 22,000 Qwen3
# tokens, filed in 2023 or later.
SEC_LONG_MIN_CHARS = 9_600
SEC_LONG_MAX_CHARS = 90_000
SEC_EVENT_FIRST_MONTH = "2023-01"


def _sample_spec() -> dict:
    return {
        "dataset": SEC_REPO,
        "revision": SEC_REVISION,
        "source_glob": "8-K/*.parquet",
        "documents": SEC_DOCUMENTS,
        "seed": DATA_SEED,
        "selection": "lowest_sha256_seed_accession_v1",
        "duplicates": "first_valid_row_in_sorted_source_files",
        "text": "upstream_text_unchanged_nonempty",
    }


def _priority(accession: str) -> int:
    payload = f"{DATA_SEED}:{accession}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


def _filing_row(source: dict) -> dict | None:
    accession = source["metadata_accession-number"]
    text = source["text"]
    date = source["metadata_filing-date"]
    if (not isinstance(accession, str)
            or re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) is None
            or not isinstance(text, str) or not text.strip()
            or not isinstance(date, str)
            or re.fullmatch(r"\d{8}", date) is None):
        return None
    try:
        filed = datetime.strptime(date, "%Y%m%d").date().isoformat()
    except ValueError:
        return None
    return {"id": accession, "text": text, "filing_date": filed,
            "filing_month": filed[:7]}


def _sample_filings(paths) -> tuple[pa.Table, dict]:
    """Keep the lowest accession hashes, independent of download timing."""
    heap = []
    seen = set()
    counts = {"source_rows": 0, "invalid_rows": 0, "duplicate_rows": 0}
    for index, path in enumerate(sorted(paths), 1):
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=512,
                                           columns=list(SEC_SOURCE_COLUMNS)):
            for source in batch.to_pylist():
                counts["source_rows"] += 1
                row = _filing_row(source)
                if row is None:
                    counts["invalid_rows"] += 1
                    continue
                accession = row["id"]
                if accession in seen:
                    counts["duplicate_rows"] += 1
                    continue
                seen.add(accession)
                priority = _priority(accession)
                item = (-priority, accession, row)
                if len(heap) < SEC_DOCUMENTS:
                    heapq.heappush(heap, item)
                elif priority < -heap[0][0]:
                    heapq.heapreplace(heap, item)
        print(f"[sec] sampled {index} source files, {len(seen)} eligible filings",
              flush=True)
    if len(heap) != SEC_DOCUMENTS:
        raise ValueError(
            f"only {len(heap)} eligible SEC filings; expected {SEC_DOCUMENTS}")
    rows = [item[2] for item in sorted(heap, key=lambda item: item[1])]
    counts["eligible_filings"] = len(seen)
    return pa.Table.from_pylist(rows, schema=SEC_SCHEMA), counts


def sec_identity(table: pa.Table) -> dict:
    """Hash the complete input table and its fixed source revision."""
    return corpus_identity({SEC_TABLE: table}, 1.0, DATA_SEED,
                           {SEC_REPO: SEC_REVISION})


def _download_sec_source(source_dir: Path) -> list[Path]:
    """Cache only the input columns, using Parquet HTTP range reads."""
    from huggingface_hub import HfApi, HfFileSystem

    info = HfApi(token=False).dataset_info(SEC_REPO, revision=SEC_REVISION)
    names = sorted(item.rfilename for item in info.siblings
                   if item.rfilename.startswith("8-K/")
                   and item.rfilename.endswith(".parquet"))
    if len(names) != SEC_SOURCE_FILES:
        raise ValueError(f"expected {SEC_SOURCE_FILES} SEC source files")
    directory = source_dir / SEC_REVISION
    directory.mkdir(parents=True, exist_ok=True)

    def download(name):
        path = directory / Path(name).name
        if path.exists():
            return path
        filesystem = HfFileSystem(token=False)
        remote = f"datasets/{SEC_REPO}@{SEC_REVISION}/{name}"
        # The raw content column duplicates the parsed text and is not an input.
        with filesystem.open(remote, "rb", cache_type="none") as source:
            table = pq.ParquetFile(source).read(columns=list(SEC_SOURCE_COLUMNS))
        temporary = path.with_suffix(".parquet.tmp")
        pq.write_table(table, temporary, compression="zstd")
        temporary.replace(path)
        print(f"[sec] cached input columns from {name}", flush=True)
        return path

    with ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(download, names))


def build_sec_corpus(data_dir, *, source_dir=None) -> Path:
    """Download the pinned 8-K files and save exactly 100,000 unique filings.

    Args:
        data_dir: Directory for the input Parquet file and manifest.
        source_dir: Download directory, or a sibling of data_dir named sec-source.
            Required columns are cached by source revision for repeat builds.
    """
    directory = Path(data_dir)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        load_sec_table(directory)
        return directory
    source_dir = (Path(source_dir) if source_dir is not None
                  else directory.parent / "sec-source")
    paths = _download_sec_source(source_dir)
    table, counts = _sample_filings(paths)
    manifest = {
        **sec_identity(table), "sample": _sample_spec(), "source_counts": counts,
        "source_files": [path.name for path in paths],
        "columns": {SEC_TABLE: SEC_SCHEMA.names},
        "reference_labels": "not_published",
    }
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{SEC_TABLE}.parquet.tmp"
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(directory / f"{SEC_TABLE}.parquet")
    temporary = directory / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temporary.replace(manifest_path)
    return directory


def load_sec_table(data_dir=None, *, root=None) -> pa.Table:
    """Load the fixed SEC sample and verify its manifest and contents.

    Args:
        data_dir: Local input directory, or None to download the published sample.
        root: Published data root, or None for the public S3 bucket.
            Downloads use the standard QUAIL-B cache.
    """
    if data_dir is not None:
        directory = Path(data_dir)
        manifest = json.loads((directory / "manifest.json").read_text())
    else:
        prefix = f"{GROUND_TRUTH_ROOT}/corpora/{SEC_CORPUS_ID}"
        manifest = json.loads(_read_bytes(root, f"{prefix}/manifest.json"))
        if manifest.get("corpus_id") != SEC_CORPUS_ID:
            raise ValueError("published SEC manifest has the wrong corpus ID")
    if manifest.get("sample") != _sample_spec():
        raise ValueError("SEC manifest does not describe the fixed 100,000 sample")
    if data_dir is not None:
        table = pq.read_table(directory / f"{SEC_TABLE}.parquet")
    else:
        path = f"{prefix}/{SEC_TABLE}.parquet"
        cached = _cached_file(root, path)
        filesystem, _, source = _location(root, path)
        table = (pq.read_table(cached) if cached is not None else
                 pq.read_table(source, filesystem=filesystem))
    if table.schema != SEC_SCHEMA or table.num_rows != SEC_DOCUMENTS:
        raise ValueError("SEC input has the wrong schema or document count")
    if any(column.null_count for column in table.columns):
        raise ValueError("SEC input contains null fields")
    ids = table.column("id").to_pylist()
    if len(set(ids)) != len(ids) or ids != sorted(ids):
        raise ValueError("SEC accession IDs must be unique and sorted")
    identity = sec_identity(table)
    if any(manifest.get(key) != value for key, value in identity.items()):
        raise ValueError("SEC input does not match its corpus manifest")
    return table


def sec_engine_table(table: pa.Table) -> pa.Table:
    """Return the table that engines read: NFC text and its length.

    Some filings hold characters that NFC changes, such as U+037E, which
    becomes ";". Tokenizers such as Qwen3's apply NFC, so an engine that
    copies answers from token bytes needs text that NFC does not change.
    `text_chars` is the length of the normalized text in Unicode
    characters. Both are computed when the sample loads, so the published
    sample and its identity do not change.
    """
    text = pc.utf8_normalize(table.column("text"), "NFC")
    table = table.set_column(table.schema.get_field_index("text"), "text", text)
    return table.append_column(pa.field("text_chars", pa.int32()),
                               pc.utf8_length(text).cast(pa.int32()))
