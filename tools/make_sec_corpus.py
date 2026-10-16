"""Download and sample the fixed SEC 8-K workload inputs."""

import argparse
from pathlib import Path

from quail_b.sec import build_sec_corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("results/sec"))
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    directory = build_sec_corpus(args.output_dir, source_dir=args.source_dir)
    print(f"[sec] input table and manifest saved in {directory}")


if __name__ == "__main__":
    main()
