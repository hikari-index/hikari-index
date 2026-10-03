"""Hash a source file: the way Shoko records it, to check that the file a
stage is about to open is the file that was onboarded, or with sha256, to
give a file added without Shoko its fingerprint (ADR-0014).

    python -m hikari_worker.hashfile <sha1|ed2k|md5|crc32|sha256> <path>

Prints progress lines to stderr and the digest on stdout (upper-case hex,
as Shoko writes it; two lines when ED2K has two conventions for the
size). Runs as its own process because ED2K needs MD4, which OpenSSL 3
keeps in its legacy module: the caller
switches that module on for this process only (openssl-legacy.cnf beside
this file), so the extraction and everything else keep the system setup.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import zlib

# ED2K: MD4 of each 9,728,000-byte chunk, then MD4 of the chunk digests
# (a file of one chunk or less is just the MD4 of its bytes).
ED2K_CHUNK = 9_728_000
BLOCK = 8 << 20
PROGRESS_SECONDS = 30.0


def _read(f, size: int):
    while True:
        data = f.read(size)
        if not data:
            return
        yield data


def ed2k(path: str, tick=lambda n: None) -> set[str]:
    """Every value Shoko could have recorded for this file, as a set. A
    file whose size is an exact multiple of the chunk has two conventions
    in the wild (with or without a trailing empty chunk's digest), so both
    are returned for it; for any other size there is one."""
    digests = []
    total = 0
    with open(path, "rb") as f:
        for chunk in _read(f, ED2K_CHUNK):
            digests.append(hashlib.new("md4", chunk).digest())
            total += len(chunk)
            tick(total)
    empty = hashlib.new("md4", b"").digest()
    if not digests:
        return {empty.hex().upper()}
    plain = digests[0] if len(digests) == 1 else hashlib.new("md4", b"".join(digests)).digest()
    out = {plain.hex().upper()}
    if total % ED2K_CHUNK == 0:
        out.add(hashlib.new("md4", b"".join(digests) + empty).hexdigest().upper())
    return out


def digest(algo: str, path: str, tick=lambda n: None) -> set[str]:
    if algo == "ed2k":
        return ed2k(path, tick)
    total = 0
    if algo == "crc32":
        crc = 0
        with open(path, "rb") as f:
            for data in _read(f, BLOCK):
                crc = zlib.crc32(data, crc)
                total += len(data)
                tick(total)
        return {f"{crc:08X}"}
    if algo in ("sha1", "md5", "sha256"):
        h = hashlib.new(algo)
        with open(path, "rb") as f:
            for data in _read(f, BLOCK):
                h.update(data)
                total += len(data)
                tick(total)
        return {h.hexdigest().upper()}
    raise ValueError(f"no way to compute a {algo} hash")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    algo, path = argv
    try:
        size = os.stat(path).st_size
    except OSError:
        size = None
    state = {"next": time.monotonic() + PROGRESS_SECONDS}

    def tick(done: int) -> None:
        now = time.monotonic()
        if now >= state["next"]:
            state["next"] = now + PROGRESS_SECONDS
            of = f" of {size / 2**30:.1f}" if size else ""
            print(f"hashing the file ({algo}): {done / 2**30:.1f}{of} GiB", file=sys.stderr, flush=True)

    for value in sorted(digest(algo, path, tick)):
        print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
