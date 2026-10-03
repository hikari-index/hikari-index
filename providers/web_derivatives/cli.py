"""CLI: encode the AVIF derivative ladder for a prepared candidate bundle.

    python -m web_derivatives.cli --bundle <prepared-bundle-dir> --out <dir>
        [--candidate ID ...] [--ffprobe PATH [--verify-sample N | --verify-all]]

Runs on the host venv — pure Python over published bundle artifacts, no raw
source, no model, no container (the same surface as annotation fusion).

With --ffprobe, emitted files are checked against the CICP declaration the
accepted encode was verified to write (full-range, sRGB transfer, bt709
primaries). Any mismatch is a hard failure.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

from web_derivatives.ladder import EXPECTED_CICP, DerivativeError, build_for_bundle


def probe_cicp(ffprobe: str, path: Path) -> dict:
    out = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries",
            "stream=color_range,color_space,color_transfer,color_primaries",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)["streams"][0]


def verify(ffprobe: str, out_dir: Path, manifest: dict, sample: int | None) -> int:
    files = [
        out_dir / entry["candidate_id"] / tier["file"]
        for entry in manifest["candidates"]
        for tier in entry["tiers"]
    ]
    if sample is not None and sample < len(files):
        files = random.sample(files, sample)
    bad = 0
    for path in files:
        declared = probe_cicp(ffprobe, path)
        mismatches = {
            key: (declared.get(key), expected)
            for key, expected in EXPECTED_CICP.items()
            if declared.get(key) != expected
        }
        if mismatches:
            bad += 1
            print(f"CICP MISMATCH {path}: {mismatches}", file=sys.stderr)
    print(f"verified CICP on {len(files)} files, {bad} mismatches")
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--candidate", action="append", dest="candidates")
    parser.add_argument("--surplus-dir", type=Path, default=None,
                        help="pipeline surplus folder to read a wanted candidate "
                             "from when the bundle manifest does not list it")
    parser.add_argument("--ffprobe")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--verify-sample", type=int, default=None)
    group.add_argument("--verify-all", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest = build_for_bundle(args.bundle, args.out, args.candidates,
                                    surplus_dir=args.surplus_dir)
    except DerivativeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    tiers = [t for e in manifest["candidates"] for t in e["tiers"]]
    total = sum(t["bytes"] for t in tiers)
    print(
        f"{len(manifest['candidates'])} candidates, {len(tiers)} files, "
        f"{total / 1024:.0f} KiB [{manifest['derivative_policy']}]"
    )

    if args.ffprobe:
        sample = None if args.verify_all else (args.verify_sample or 8)
        if verify(args.ffprobe, args.out, manifest, sample):
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
