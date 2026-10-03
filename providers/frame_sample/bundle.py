"""What happens to a bundle once its frames are extracted.

Three steps, each a plain function:

``check``            the bundle on disk is whole: every listed frame is
                     there, inside the bundle, with the size and checksum
                     the list gives, and the list itself matches READY.json.
``publish``          copy it under ``prepared/`` in a folder a person can
                     read (date, title, episode) with frames named by their
                     place and time, and write the list, READY.json and a
                     README beside them.
``describe_colors``  run the palette tool over every published frame and
                     write one descriptor per frame, with a list of them.

These are the checks that protect data: a half-copied or changed frame is
caught here, and again by whoever reads the bundle later over a network
share. Nothing here judges which fields a bundle may carry. A field this
code does not read is none of its business; readers take what they need
and ignore the rest, so adding or renaming a field never needs every stage
rebuilt.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

PALETTE_BIN = Path(os.environ.get("HIKARI_PALETTE_BIN", "/opt/hikari/bin/palette-descriptor"))
#: The palette tool exits non-zero with this on a frame with no colour to
#: describe (pure black, white or gray). That is an answer, not a failure.
PALETTE_ABSTENTION = "no usable palette colors found after black/white filtering"
DESCRIPTOR_SCHEMA = "hikari-color-descriptor/1"
RESULT_SCHEMA_VERSION = "1.1"


class BundleError(RuntimeError):
    """The bundle on disk is not what its own list says it is."""


#: Bundle and frame ids become file and folder names in every later stage
#: (`<frame id>.descriptor.json`, `results/completed/<bundle id>/`), so an
#: id must be one plain name: no slash, no "..", nothing a path could hide in.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def safe_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise BundleError(f"{what} {value!r} cannot be used as a file name "
                          "(letters, digits, dot, dash and underscore only)")
    return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from the PNG's own header."""
    with open(path, "rb") as source:
        head = source.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise BundleError(f"{path.name} is not a PNG file")
    return struct.unpack(">II", head[16:24])


def _frame_path(bundle_root: Path, name: str) -> Path:
    """A frame's path, refused unless it is a real file inside the bundle."""
    path = bundle_root / name
    if path.is_symlink() or not path.is_file():
        raise BundleError(f"frame {name} is missing from the bundle (or is a link, not a file)")
    if bundle_root.resolve() not in path.resolve().parents:
        raise BundleError(f"frame {name} points outside the bundle")
    return path


def check(bundle_root: Path) -> tuple[dict, str]:
    """Returns the bundle's list and its checksum, or raises BundleError
    saying what is wrong in plain words."""
    manifest_path = bundle_root / "manifest.json"
    ready_path = bundle_root / "READY.json"
    if not manifest_path.is_file() or not ready_path.is_file():
        raise BundleError(f"the bundle at {bundle_root} has no manifest.json or READY.json: it was not finished")
    manifest_sha = sha256_file(manifest_path)
    manifest = json.loads(manifest_path.read_bytes())
    ready = json.loads(ready_path.read_bytes())
    if ready.get("manifest_sha256") != manifest_sha:
        raise BundleError("the bundle's list was changed after the bundle was finished "
                          "(its checksum is not the one READY.json recorded)")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise BundleError("the bundle lists no frames")
    safe_name(manifest.get("bundle_id"), "the bundle id")
    for c in candidates:
        safe_name(c.get("candidate_id"), "the frame id")
    ids = [c.get("candidate_id") for c in candidates]
    names = [c.get("artifact_name") for c in candidates]
    if len(set(ids)) != len(ids) or len(set(names)) != len(names):
        raise BundleError("the bundle lists a frame id or a file name twice")
    for c in candidates:
        cid, name = c["candidate_id"], str(c["artifact_name"])
        path = _frame_path(bundle_root, name)
        size = path.stat().st_size
        if size != c["bytes"]:
            raise BundleError(f"frame {cid}: the file is {size} bytes but the list says {c['bytes']}; "
                              "the bundle was changed or only partly written")
        if sha256_file(path) != c["sha256"]:
            raise BundleError(f"frame {cid}: the file's checksum is not the one in the list; "
                              "the bundle was changed or damaged")
        if c.get("intended_pts") != c.get("observed_pts"):
            raise BundleError(f"frame {cid}: extracted at timestamp {c.get('observed_pts')}, "
                              f"asked for {c.get('intended_pts')}")
        if "width" in c and "height" in c and png_size(path) != (c["width"], c["height"]):
            raise BundleError(f"frame {cid}: the image is {png_size(path)}, the list says "
                              f"{(c['width'], c['height'])}")
    return manifest, manifest_sha


# ---- names a person can read

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def slugify(text: str) -> str:
    """A title as one folder-name segment: nothing a file system refuses,
    spaces to dashes, at most 60 characters, never ending in a dot, dash or
    space (Windows cannot hold such a name, and over a share the folder
    then appears under a substitute one)."""
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "-", text)
    result = re.sub(r"\s+", "-", result)
    result = re.sub(r"-{2,}", "-", result)[:60]
    result = result.strip("-. ")
    if result.upper() in _WINDOWS_RESERVED:
        result += "-x"
    return result


def timestamp_name(pts: int, time_base: str) -> str:
    num, _, den = time_base.partition("/")
    total_ms = pts * int(num) * 1000 // int(den)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02d}h{minutes:02d}m{seconds:02d}s{ms:03d}"


def entry_label(identity: dict) -> str:
    if identity.get("entry_type") == "movie":
        return "MOVIE"
    episode = int(identity.get("episode") or 0)
    if identity.get("season") is not None:
        return f"S{int(identity['season']):02d}E{episode:02d}"
    return f"E{episode:03d}"


def publish(bundle_root: Path, output_root: Path, bundle_digest: Callable[[str], str]) -> Path:
    """Copies a finished bundle to ``prepared/<date>--<title>--<entry>[--
    <episode title>]`` and returns that folder."""
    manifest = json.loads((bundle_root / "manifest.json").read_bytes())
    identity = manifest.get("source_identity") or {}
    entry = entry_label(identity)
    name = "--".join(part for part in (
        re.sub(r"[-:]", "", str(manifest.get("created_at", ""))),
        slugify(str(identity.get("series_title", ""))),
        entry,
        slugify(str(identity.get("episode_title") or "")),
    ) if part)
    prepared_root = output_root / "prepared"
    prepared_root.mkdir(parents=True, exist_ok=True)
    prepared = prepared_root / name
    if prepared.exists():
        prepared = prepared_root / f"{name}--f{identity.get('file_id', 'x')}"
    (prepared / "candidates").mkdir(parents=True)

    lines = []
    for index, candidate in enumerate(manifest["candidates"], start=1):
        stem = f"c{index:04d}--{timestamp_name(candidate['intended_pts'], candidate['time_base'])}"
        source = _frame_path(bundle_root, str(candidate["artifact_name"]))
        shutil.copyfile(source, prepared / "candidates" / f"{stem}.png")
        candidate["artifact_name"] = f"candidates/{stem}.png"
        lines.append(stem)

    manifest_bytes = _json_bytes(manifest)
    (prepared / "manifest.json").write_bytes(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    digest = bundle_digest(manifest_sha)
    (prepared / "READY.json").write_bytes(_json_bytes({
        "schema_version": manifest.get("schema_version"),
        "state": "ready",
        "bundle_id": manifest.get("bundle_id"),
        "manifest_sha256": manifest_sha,
        "bundle_digest": digest,
        "completion_id": f"ready-{manifest_sha}",
    }))
    readme = [
        f"Title: {identity.get('series_title', '')}",
        f"Entry: {entry}",
        f"Type: {identity.get('entry_type', '')}",
        f"Source filename: {identity.get('source_filename', '')}",
        f"Prepared at: {manifest.get('created_at', '')}",
        f"Series ID: {identity.get('series_id', '')}",
        f"File ID: {identity.get('file_id', '')}",
        f"Episode IDs: {json.dumps(identity.get('episode_ids', []), separators=(',', ':'))}",
        f"Work ID: {identity.get('work_id', '')}",
        f"Bundle digest: {digest}",
        "",
        "Candidates:",
        *(f"  {stem}" for stem in lines),
        "",
        "This directory is regenerable output. Nothing in it is a master.",
        "",
    ]
    (prepared / "README.txt").write_text("\n".join(readme), encoding="utf-8", newline="\n")
    return prepared


# ---- the palette step

def palette_identity(palette_bin: Path = PALETTE_BIN) -> tuple[str, str]:
    """(name, version) as the palette tool itself reports them."""
    said = subprocess.run([str(palette_bin), "--identity"], capture_output=True, text=True, timeout=30).stdout.strip()
    name, _, version = said.rpartition("-")
    if not name or not version:
        raise BundleError(f"the palette tool at {palette_bin} did not say what it is ({said!r})")
    return name, version


def describe_colors(prepared: Path, results_root: Path, tool: dict, preprocessing: str,
                    palette_bin: Path = PALETTE_BIN, log: Optional[Callable[[str], None]] = None) -> Path:
    """One colour descriptor per published frame, under
    ``results/completed/<bundle id>/``: ``artifacts/<frame id>.descriptor.
    json``, ``result-manifest.json`` listing them with checksums, and
    ``RESULT_READY.json``. Built in a staging folder and renamed into place,
    so a reader never sees half of it. A result already there for this
    bundle is kept as it is. ``tool`` is {version, digest} of whatever ran
    this, recorded with the result."""
    manifest, manifest_sha = check(prepared)
    ready = json.loads((prepared / "READY.json").read_bytes())
    bundle_id = manifest["bundle_id"]
    completed = results_root / "completed"
    completed.mkdir(parents=True, exist_ok=True)
    final = completed / bundle_id
    if (final / "result-manifest.json").is_file():
        return final
    name, version = palette_identity(palette_bin)
    stage = Path(tempfile.mkdtemp(prefix=f".{bundle_id}.staging.", dir=completed))
    try:
        (stage / "artifacts").mkdir()
        items = []
        for candidate in manifest["candidates"]:
            cid = candidate["candidate_id"]
            relative = f"artifacts/{cid}.descriptor.json"
            out = stage / relative
            ran = subprocess.run([str(palette_bin), str(prepared / candidate["artifact_name"]), str(out)],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace")
            if ran.returncode == 0:
                status = "success"
            elif PALETTE_ABSTENTION in (ran.stderr or ""):
                status = "abstained"
                out.write_bytes(_json_bytes({
                    "descriptor_schema": DESCRIPTOR_SCHEMA, "status": "abstained",
                    "reason": "no-usable-palette-colors", "provider": name, "provider_version": version,
                }))
            else:
                raise BundleError(f"the palette tool failed on frame {cid} (exit {ran.returncode}): "
                                  f"{(ran.stderr or '').strip()[-300:]}")
            items.append({"candidate_id": cid, "status": status, "artifact_name": relative,
                          "sha256": sha256_file(out), "bytes": out.stat().st_size})
        result_bytes = _json_bytes({
            "schema_version": RESULT_SCHEMA_VERSION,
            "result_id": f"result-{bundle_id}",
            "bundle_id": bundle_id,
            "input_manifest_sha256": manifest_sha,
            "input_bundle_digest": ready.get("bundle_digest"),
            "processing_identity": f"{name}-{version}",
            "preprocessing_identity": preprocessing,
            "pipeline": tool,
            "runtime": f"{platform.system()} {platform.release()} {platform.machine()}",
            "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "candidates": items,
        })
        (stage / "result-manifest.json").write_bytes(result_bytes)
        result_sha = hashlib.sha256(result_bytes).hexdigest()
        (stage / "RESULT_READY.json").write_bytes(_json_bytes({
            "schema_version": RESULT_SCHEMA_VERSION, "state": "complete", "bundle_id": bundle_id,
            "input_manifest_sha256": manifest_sha, "input_bundle_digest": ready.get("bundle_digest"),
            "result_manifest_sha256": result_sha, "completion_id": f"complete-{result_sha}",
        }))
        os.chmod(stage, 0o755)
        stage.rename(final)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if log:
        abstained = sum(1 for item in items if item["status"] == "abstained")
        log(f"result=complete bundle={bundle_id} mode=color"
            + (f" ({abstained} with no colour to describe)" if abstained else ""))
    return final
