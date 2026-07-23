"""Generate and replay the deterministic Phase 0 document-relation corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.document_relation_dataset.generator import generate_dataset
from scripts.document_relation_dataset.report import build_coverage_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".generated" / "document_relation_phase0",
    )
    parser.add_argument("--pages-per-type", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260722)
    parser.add_argument(
        "--mineru-url",
        default="",
        help="Optional explicit MinerU base URL. Empty means deterministic raw replay only.",
    )
    parser.add_argument("--skip-replay", action="store_true")
    args = parser.parse_args()

    manifest = generate_dataset(
        args.output,
        pages_per_type=args.pages_per_type,
        seed=args.seed,
    )
    if not args.skip_replay:
        from scripts.document_relation_dataset.replay import replay_manifest

        replay_manifest(args.output, manifest, mineru_url=args.mineru_url)
    report = build_coverage_report(args.output, manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["engineering_baseline_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
