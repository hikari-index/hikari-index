"""Perceptual hash dedup for candidate frames."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Dict, List, Tuple


def compute_phash_bytes(png_bytes: bytes, hash_size: int = 8) -> str:
    from PIL import Image
    import imagehash
    img = Image.open(io.BytesIO(png_bytes))
    return str(imagehash.phash(img, hash_size=hash_size))


@dataclass
class DedupFamily:
    family_id: str
    candidate_ids: List[str]
    phash: str


def _hex_to_hash(hex_str: str):
    import imagehash
    return imagehash.hex_to_hash(hex_str)


def group_dedup_families(
    candidates_with_hashes: List[Tuple[str, str]],
    threshold: int = 5,
) -> List[DedupFamily]:
    if not candidates_with_hashes:
        return []
    n = len(candidates_with_hashes)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            h1 = _hex_to_hash(candidates_with_hashes[i][1])
            h2 = _hex_to_hash(candidates_with_hashes[j][1])
            if abs(h1 - h2) <= threshold:
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        r = find(i)
        groups.setdefault(r, []).append(i)

    families: List[DedupFamily] = []
    fam_idx = 0
    for _root, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        fam_idx += 1
        family_id = f"fam-{fam_idx:03d}"
        ids = [candidates_with_hashes[m][0] for m in members]
        phash = candidates_with_hashes[members[0]][1]
        families.append(DedupFamily(family_id=family_id, candidate_ids=ids, phash=phash))
    return families
