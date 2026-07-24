"""Generate and replay the deterministic DCP-0 document-profile corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.document_profile_dataset.generator import generate_dataset
from scripts.document_profile_dataset.replay import replay_manifest
from scripts.document_profile_dataset.report import build_coverage_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / ".generated"
        / "document_profile_dcp0",
    )
    parser.add_argument("--seed", type=int, default=20260723)
    parser.add_argument("--skip-replay", action="store_true")
    args = parser.parse_args()

    manifest = generate_dataset(args.output, seed=args.seed)
    if not args.skip_replay:
        replay_manifest(args.output, manifest)
    report = build_coverage_report(args.output, manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["engineering_baseline_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
