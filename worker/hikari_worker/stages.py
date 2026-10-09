"""The stages the source worker runs.

extract: the delivered frame-sample pipeline, unchanged (it is the frame,
    PTS and colour reference, ADR-0006), on the job's source file, into
    runs/<work>/extract under /output. The file is first checked against
    the hash Shoko recorded at onboarding, so a file replaced since then
    (same size or not) is never extracted under the old file's identity.
    A file added without Shoko (ADR-0014) has no recorded hash: it is
    checked for exactly one video stream and hashed here, and that sha256
    is its fingerprint from then on.
    The pipeline refuses a non-empty output root and has no resume, so a
    failed attempt's folder is moved aside (kept, not deleted: it is
    evidence) and the retry starts clean.
derive: web_derivatives for the stills the picker chose (ADR-0008 picks
    from the whole measured pool, so a pick may be a surplus frame), built
    in a staging folder beside the gallery's images and renamed into place
    as <work>/ in one step, so the gallery never sees half a set.
scrub: the disk half of removing a work (ADR-0009 amendment): its run
    folder and its web images. The gallery has already deleted its rows and
    kept a removal record; the gallery cannot delete the images itself (it
    mounts them read-only).
listing: the names and sizes of the video files in one folder of a source
    root, for the gallery's "add a folder" page (ADR-0014 amendment). Run
    by the listing thread beside the main loop, so it does not wait behind
    an extraction. One directory read, nothing opened.

Each returns the result stored on the job, or raises StageError with an
error class the job table understands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

OUTPUT = Path(os.environ.get("HIKARI_OUTPUT", "/output"))
IMAGES = Path(os.environ.get("HIKARI_IMAGES", "/images"))
# The gallery's work id rule (gallery/src/lib/server/onboard.js WORK_ID).
WORK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,71}$")
HASHES = ("sha1", "ed2k", "md5", "crc32")
FFPROBE = os.environ.get("HIKARI_FFPROBE", "ffprobe")


class StageError(Exception):
    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


def source_roots() -> dict[str, Path]:
    """HIKARI_SOURCE_ROOTS: source folder name -> the path it is mounted
    at here, e.g. "4=/source,films=/source/films". For a file onboarded
    from Shoko the name is Shoko's managed folder id; for a file added
    without Shoko (ADR-0014) it is whatever the operator named the folder.
    Path mapping is worker configuration, never part of a job (research/01
    "Path translation")."""
    out = {}
    for part in os.environ.get("HIKARI_SOURCE_ROOTS", "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = Path(v.strip())
    return out


def is_local(params: dict) -> bool:
    """A file added without Shoko: the job carries no recorded hash or
    size, and names its source folder itself."""
    return bool(params["source"].get("local"))


def resolve_source(params: dict) -> Path:
    roots = source_roots()
    src = params["source"]
    local = is_local(params)
    tried = []
    for loc in src.get("locations") or []:
        name = loc.get("root") if local else loc.get("managed_folder_id")
        root = roots.get(str(name))
        if root is None:
            tried.append(f"source folder {name!r} is not mapped on this worker (HIKARI_SOURCE_ROOTS)" if local
                         else f"managed folder {name} is not mapped on this worker")
            continue
        rel = str(loc.get("relative_path") or "").replace("\\", "/").lstrip("/")
        path = (root / rel).resolve()
        if root.resolve() not in path.parents:
            tried.append("a location resolves outside its mapped root")
            continue
        # One stat of the chosen file, no directory listing: the disk may be
        # spun down, and waking it is expected (research/04 waiting_for_storage).
        try:
            st = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            tried.append(f"{rel} is not in source folder {name!r}" if local else f"{rel} is not at its Shoko location")
            continue
        if local:
            if not stat.S_ISREG(st.st_mode):
                raise StageError("source", f"{rel} is not a file (a folder, or something else); name the video file itself.")
            return path
        if st.st_size != src["size"]:
            raise StageError("source", f"{rel} is {st.st_size} bytes; Shoko recorded {src['size']} at onboarding. "
                                       "The file changed; onboard it again.")
        return path
    why = "; ".join(tried) or "the job lists no source location"
    if local:
        why += ". Cancel this row and add the file again with the right folder and path."
    raise StageError("source", why)


# What the gallery's folder page treats as a video: by name only. The
# extraction's own probe is the real check; this keeps subtitles, images and
# sidecar files out of the list.
VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".webm", ".avi", ".mov", ".ts", ".m2ts", ".mts",
                  ".mpg", ".mpeg", ".ogm", ".ogv", ".wmv", ".flv", ".vob", ".rm", ".rmvb", ".divx"}
LISTING_MAX = 2000


def listing(job: dict, should_stop) -> dict:
    """The video files directly inside one folder of a mapped source root:
    name and size, nothing else (research/01: nothing is scanned; this is
    one directory the operator named). Subfolders are counted, not
    entered; files that do not look like video are counted, not listed."""
    folder = job["params"].get("folder") or {}
    name = str(folder.get("root") or "")
    root = source_roots().get(name)
    if root is None:
        raise StageError("source", f"source folder {name!r} is not mapped on this worker (HIKARI_SOURCE_ROOTS)")
    rel = str(folder.get("path") or "").replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p] if rel else []
    if any(p in (".", "..") for p in parts):
        raise StageError("source", "the folder's path may not contain . or .. parts")
    where = rel or "(the folder itself)"
    # Descended one directory at a time by descriptor, each step refusing a
    # symlink (the discard stage's pattern), so nothing on the way can be
    # swapped for a link out of the source folder between a check and the
    # read, and the listing is of the directory that was opened.
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        try:
            fd = os.open(root, flags)
        except OSError:
            raise StageError("source", f"source folder {name!r} is not mounted on this worker")
        for part in parts:
            try:
                nxt = os.open(part, flags, dir_fd=fd)
            except (FileNotFoundError, NotADirectoryError):
                raise StageError("source", f"{where} is not a folder in source folder {name!r} (a file, or nothing, or a link); "
                                           "to add one file, use the add-a-file page.")
            except PermissionError:
                raise StageError("source", f"{where} cannot be read by this worker (permission)")
            except OSError as e:
                raise StageError("source", f"{where} cannot be opened: {e.strerror}")
            os.close(fd)
            fd = nxt
        files, folders, others, truncated = [], 0, 0, False
        try:
            with os.scandir(fd) as it:
                for entry in it:
                    if should_stop():
                        raise StageError("cancelled", "stopped on request")
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        folders += 1
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if Path(entry.name).suffix.lower() not in VIDEO_SUFFIXES:
                        others += 1
                        continue
                    if len(files) >= LISTING_MAX:
                        truncated = True
                        continue
                    files.append({"name": entry.name, "size": entry.stat(follow_symlinks=False).st_size})
        except PermissionError:
            raise StageError("source", f"{where} cannot be read by this worker (permission)")
    finally:
        if fd is not None:
            os.close(fd)
    files.sort(key=lambda f: f["name"])
    return {"folder": {"root": name, "path": rel}, "files": files, "videos": len(files),
            "folders": folders, "others": others, "truncated": truncated}


def probe_local(path: Path) -> dict:
    """What a file added without Shoko is, before any decoding. With Shoko
    the onboarding page refuses a file with several video streams from
    Shoko's media info; here nobody has looked yet, and the pipeline takes
    the first video stream without asking. So: exactly one video stream
    that is not attached cover art, and it must be the first."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries",
             "stream=index,codec_type,codec_name,width,height,pix_fmt:stream_disposition=attached_pic:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise StageError("transient", f"ffprobe could not be run on {path.name}: {e}")
    if out.returncode != 0:
        said = (out.stderr or "").strip().splitlines()
        raise StageError("input", f"refused: {path.name} is not a video file ffprobe can read"
                                  f"{': ' + said[-1][:200] if said else ''}")
    doc = json.loads(out.stdout or "{}")
    video = [s for s in doc.get("streams") or [] if s.get("codec_type") == "video"]
    real = [s for s in video if not (s.get("disposition") or {}).get("attached_pic")]
    if not real:
        raise StageError("input", f"refused: {path.name} has no video stream")
    if len(real) > 1:
        raise StageError("input", f"refused: {path.name} has {len(real)} video streams; choosing one is a human call, "
                                  "and this path cannot make it. Remux the one you want into its own file.")
    if video[0] is not real[0]:
        raise StageError("input", f"refused: the first video stream of {path.name} is attached cover art, which the "
                                  "extraction would read as the video. Remux the file without the attachment first.")
    v = real[0]
    try:
        duration = round(float((doc.get("format") or {}).get("duration")), 1)
    except (TypeError, ValueError):
        duration = None
    return {"video_streams": 1, "codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"),
            "pix_fmt": v.get("pix_fmt"), "duration_seconds": duration}


def hash_source(path: Path, algo: str, should_stop, log, label: str) -> set[str]:
    """Every value `algo` can give for the file (hashfile.py), computed in
    a child process at low priority, with its progress copied to the run
    log and the container log. One read of the whole file."""
    env = dict(os.environ, OPENSSL_CONF=str(Path(__file__).with_name("openssl-legacy.cnf")))
    proc = subprocess.Popen(["nice", "-n", "10", sys.executable, "-m", "hikari_worker.hashfile", algo, str(path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True)

    def pump():
        for raw in proc.stderr:
            log.write(raw)
            log.flush()
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                print(f"[{label}] {line[:300]}", flush=True)

    out = bytearray()
    reader = threading.Thread(target=pump, daemon=True)
    collector = threading.Thread(target=lambda: out.extend(proc.stdout.read()), daemon=True)
    reader.start()
    collector.start()
    while True:
        try:
            proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            if should_stop():
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise StageError("cancelled", "stopped on request")
    reader.join(timeout=10)
    collector.join(timeout=10)
    if proc.returncode != 0:
        raise StageError("transient", f"hashing {path.name} failed (exit {proc.returncode}); see the log")
    return {line.strip().upper() for line in bytes(out).decode().splitlines() if line.strip()}


def fingerprint_local(path: Path, should_stop, log_path: Path, label: str) -> str:
    """The fingerprint of a file added without Shoko: its sha256, taken
    here because nothing recorded one at onboarding (ADR-0014). It goes
    into the bundle and the stage's result; a later extraction of the same
    path hashes again and records what it finds."""
    started = time.monotonic()
    with open(log_path, "ab") as log:
        log.write(f"\n$ hash {path.name} (sha256; no hash was recorded at onboarding)\n".encode())
        actual = hash_source(path, "sha256", should_stop, log, label)
        if len(actual) != 1:
            raise StageError("transient", f"hashing {path.name} gave no digest; see the log")
        fingerprint = f"sha256:{actual.pop().lower()}"
        seconds = time.monotonic() - started
        log.write(f"{fingerprint} ({seconds:.0f} s)\n".encode())
        print(f"[{label}] the file's fingerprint is {fingerprint} ({seconds:.0f} s)", flush=True)
    return fingerprint


def verify_source(path: Path, params: dict, should_stop, log_path: Path, label: str) -> str:
    """Hash the whole file and compare it with the hash Shoko recorded at
    onboarding (research/01: check identity before a run). A size check
    alone let a replaced file of the same size through under the old
    file's fingerprint. Costs one read of the file; the extraction that
    follows reads it again, mostly from the page cache. Returns the
    fingerprint checked."""
    fingerprint = str(params["source"].get("fingerprint") or "")
    algo, _, expected = fingerprint.partition(":")
    if algo not in HASHES or not expected:
        raise StageError("source", f"the job records no usable source hash ({fingerprint or 'none'}); onboard it again")
    started = time.monotonic()
    with open(log_path, "ab") as log:
        log.write(f"\n$ check {path.name} against {algo}:{expected}\n".encode())
        actual = hash_source(path, algo, should_stop, log, label)
        seconds = time.monotonic() - started
        if expected.upper() not in actual:
            got = sorted(actual)[0] if actual else "nothing"
            log.write(f"MISMATCH: {algo} is {got}, Shoko recorded {expected} ({seconds:.0f} s)\n".encode())
            raise StageError(
                "source",
                f"{path.name} no longer matches the file that was onboarded: its {algo} is now {got}, "
                f"Shoko recorded {expected.upper()}. The file was replaced or changed since. "
                "Have Shoko hash it again, remove this episode (Remove, on this row), "
                "then onboard it again.")
        log.write(f"matches Shoko's {algo} ({seconds:.0f} s)\n".encode())
        print(f"[{label}] the file matches Shoko's {algo} hash ({seconds:.0f} s)", flush=True)
    return fingerprint


def run_logged(cmd: list[str], log_path: Path, should_stop, label: str, progress=None) -> int:
    """Run at low priority (Jellyfin keeps the CPU first) and stop the
    process group when should_stop() turns true. Everything the tool
    prints goes to the run's log file and, prefixed with `label`, to the
    container log, so its progress shows in the container's console. A tool
    that prints nothing until it ends gets `progress()`, a one-line status,
    printed every 30 s instead."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        log.write(f"\n$ {' '.join(cmd)}\n".encode())
        log.flush()
        proc = subprocess.Popen(["nice", "-n", "10", *cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)

        def pump():
            for raw in proc.stdout:
                log.write(raw)
                log.flush()
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    print(f"[{label}] {line[:300]}", flush=True)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        last = time.monotonic()
        while True:
            try:
                code = proc.wait(timeout=5)
                reader.join(timeout=10)
                return code
            except subprocess.TimeoutExpired:
                if progress and time.monotonic() - last >= 30:
                    last = time.monotonic()
                    try:
                        print(f"[{label}] {progress()}", flush=True)
                    except OSError:
                        pass
                if should_stop():
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    reader.join(timeout=10)
                    raise StageError("cancelled", "stopped on request")


def tail(path: Path, n: int = 15) -> str:
    """What the last command printed, most telling line first: its final
    non-empty line (a tool's own error message), then the lines before it.
    The command line itself is left out."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    starts = [i for i, line in enumerate(lines) if line.startswith("$ ")]
    lines = [line for line in lines[(starts[-1] + 1 if starts else 0):] if line.strip()]
    if not lines:
        return "(no output)"
    return lines[-1] + ("\n\n" + "\n".join(lines[-n:-1]) if len(lines) > 1 else "")


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def extract(job: dict, should_stop) -> dict:
    params, work = job["params"], job["work_id"]
    source = resolve_source(params)
    run_dir = OUTPUT / "runs" / work
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / "extract.log"
    local = None
    if is_local(params):
        # A cancel that lands while the probe runs wins over whatever the
        # probe said: the stage ends cancelled, not refused or retried.
        try:
            media = probe_local(source)
        except StageError:
            if should_stop():
                raise StageError("cancelled", "stopped on request")
            raise
        if should_stop():
            raise StageError("cancelled", "stopped on request")
        local = {"media": media, "size": source.stat().st_size,
                 "fingerprint": fingerprint_local(source, should_stop, log, f"extract {work}")}
        fingerprint = local["fingerprint"]
    else:
        fingerprint = verify_source(source, params, should_stop, log, f"extract {work}")
    root = run_dir / "extract"
    if root.exists():
        aside = run_dir / f"extract.failed-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        root.rename(aside)
    identity = run_dir / "identity.json"
    write_atomic(identity, json.dumps(params["identity"], indent=2, ensure_ascii=False))
    short = str(job["chain_id"]).split("-")[0]
    cmd = [
        "python", "-m", "frame_sample.pipeline_cli",
        "--input", str(source),
        "--output-root", str(root),
        "--source-identity", str(identity),
        "--bundle-id", f"{work}-{short}",
        "--job-id", str(job["id"]),
        "--source-fingerprint-ref", fingerprint,
    ]
    # Hold the frames inside opening and ending chapters (#47): an
    # onboarding choice, carried in the job's params.
    if params.get("hold_chapters"):
        cmd.append("--hold-chapters")
    code = run_logged(cmd, log, should_stop, f"extract {work}")
    ready = root / "PIPELINE_READY.json"
    summary = json.loads(ready.read_text()) if ready.is_file() else None
    if code != 0 or not summary or summary.get("state") != "complete":
        text = tail(log)
        # The pipeline refuses, before or after decoding, what it will never
        # accept: an identity missing a name or id, a colour-tag
        # combination outside the frozen rules. Retrying cannot help those.
        permanent = any(s in text for s in ("refus", "source identity", "not one of"))
        raise StageError("input" if permanent else "transient",
                         f"{text}\n\n(extraction exited {code}; full log: runs/{work}/extract.log)")
    bundle_id = f"{work}-{short}"
    return {
        # For a file added without Shoko: what this stage found it to be.
        **({"source": local} if local else {}),
        "run_root": str(root.relative_to(OUTPUT)),
        "prepared_bundle": str((root / summary["prepared_bundle"]).relative_to(OUTPUT)),
        "surplus_dir": str((root / "surplus" / bundle_id).relative_to(OUTPUT)) if (root / "surplus" / bundle_id).is_dir() else None,
        "palette_result": summary.get("palette_result"),
        "bundle_id": bundle_id,
        "published_candidates": summary.get("published_candidates"),
        "published_shots": summary.get("published_shots"),
        "held_candidates": summary.get("held_candidates"),
        "held_extracted": summary.get("held_extracted"),
        "phase_seconds": summary.get("phase_seconds"),
        "log": str(log.relative_to(OUTPUT)),
    }


def derive(job: dict, chain: list[dict], should_stop) -> dict:
    """chain: the earlier stages, nearest first (analyze, then extract)."""
    work = job["work_id"]
    by_stage = {j["stage"]: j for j in chain}
    extract_result = (by_stage.get("extract") or {}).get("result") or {}
    analyze_result = (by_stage.get("analyze") or {}).get("result") or {}
    if not extract_result.get("prepared_bundle"):
        raise StageError("input", "the extract stage recorded no bundle")
    selection_rel = analyze_result.get("selection")
    if not selection_rel:
        raise StageError("input", "the analyze stage recorded no selection")
    selection = json.loads((OUTPUT / selection_rel).read_text())
    picks = selection.get("selected") or []
    if not picks:
        raise StageError("input", "the selection is empty")
    staging = IMAGES / f".staging-{work}-{str(job['id']).split('-')[0]}"
    if staging.exists():
        shutil.rmtree(staging)
    cmd = ["python", "-m", "web_derivatives.cli",
           "--bundle", str(OUTPUT / extract_result["prepared_bundle"]),
           "--out", str(staging), "--ffprobe", "ffprobe", "--verify-sample", "8"]
    if extract_result.get("surplus_dir"):
        cmd += ["--surplus-dir", str(OUTPUT / extract_result["surplus_dir"])]
    for cid in picks:
        cmd += ["--candidate", cid]
    log = OUTPUT / "runs" / work / "derive.log"
    def made_so_far():
        n = sum(1 for p in staging.iterdir() if p.is_dir()) if staging.is_dir() else 0
        return f"{n} of {len(picks)} stills have their web images"

    code = run_logged(cmd, log, should_stop, f"derive {work}", progress=made_so_far)
    if code != 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise StageError("transient", f"{tail(log)}\n\n(web images exited {code}; full log: runs/{work}/derive.log)")
    made = sorted(p.name for p in staging.iterdir() if p.is_dir())
    missing = sorted(set(picks) - set(made))
    if missing:
        shutil.rmtree(staging, ignore_errors=True)
        raise StageError("transient", f"{len(missing)} picked stills have no images, e.g. {missing[:3]}")
    final = IMAGES / work
    if final.exists():
        old = IMAGES / f".replaced-{work}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        final.rename(old)
        staging.rename(final)
        shutil.rmtree(old, ignore_errors=True)
    else:
        staging.rename(final)
    return {"images": work, "stills": len(made), "log": str(log.relative_to(OUTPUT))}


def _tree(path: Path) -> tuple[int, int]:
    """Files and bytes under a folder, never following links."""
    files = size = 0
    for dirpath, _dirs, names in os.walk(path, followlinks=False):
        for name in names:
            try:
                size += os.lstat(os.path.join(dirpath, name)).st_size
                files += 1
            except OSError:
                pass
    return files, size


def _runs_mounted() -> bool:
    """The run folders are a mount: /output/runs itself (the worker sees
    only runs/), or /output when a template still maps the whole share.
    The image has empty folders of its own, so a folder proves nothing."""
    return os.path.ismount(OUTPUT / "runs") or os.path.ismount(OUTPUT)


def scrub(job: dict, should_stop) -> dict:
    """Delete a removed work's run folder (masters, surplus, records, set-
    aside attempts) and its web images, and nothing else: only the two
    folders named exactly by the work id, plus any half-made or swapped-out
    image folder derive leaves for that id. Safe to run again: what is
    already gone is skipped."""
    work = job["work_id"]
    if not WORK_ID.fullmatch(work):
        raise StageError("input", f"{work!r} is not a work id; nothing was deleted")
    runs = OUTPUT / "runs"
    # A missing or wrongly mapped mount must not read as "nothing on disk":
    # that would release the work id with its real folders still in place.
    # The image has empty /output and /images folders of its own, so being a
    # folder proves nothing; each root must be a mount point.
    if not _runs_mounted():
        raise StageError("transient", f"{OUTPUT / 'runs'} is not mounted here (check the container's paths); nothing was deleted")
    if not os.path.ismount(IMAGES):
        raise StageError("transient", f"{IMAGES} is not mounted here (check the container's paths); nothing was deleted")
    for root in (runs, IMAGES):
        if root.is_symlink() or not root.is_dir():
            raise StageError("transient", f"{root} is not a folder here; nothing was deleted")
    targets = [("run folder", runs, runs / work), ("web images", IMAGES, IMAGES / work)]
    leftovers = re.compile(rf"\.(staging-{re.escape(work)}-[0-9a-f]{{8}}|replaced-{re.escape(work)}-\d{{8}}T\d{{6}}Z)")
    for p in sorted(IMAGES.iterdir()):
        if leftovers.fullmatch(p.name):
            targets.append(("unfinished web images", IMAGES, p))
    removed = []
    for what, parent, path in targets:
        if should_stop():
            raise StageError("cancelled", "stopped on request; what was already deleted stays deleted")
        if path.is_symlink():
            raise StageError("input", f"{path} is a link; not following it, nothing more deleted")
        if not path.exists():
            continue
        if path.resolve().parent != parent.resolve() or not path.is_dir():
            raise StageError("input", f"{path} is not a folder directly under {parent}; not deleting it")
        files, size = _tree(path)
        print(f"[scrub {work}] deleting the {what}: {files} files, {size / 2**20:.0f} MiB", flush=True)
        try:
            shutil.rmtree(path)
        except OSError as e:
            # Most likely files this worker's user (uid 10001) does not own,
            # e.g. copied onto the share by hand. Part may already be gone;
            # the work itself is out of the index either way.
            raise StageError("source", f"could not delete all of the {what} {path}: {e.strerror or e} "
                                       f"({getattr(e, 'filename', None) or path}). Fix its owner or delete it "
                                       "on the host, then retry.")
        removed.append({"what": what, "path": str(path), "files": files, "bytes": size})
    total = sum(r["bytes"] for r in removed)
    return {"removal": (job["params"].get("removal") or {}).get("id"), "removed": removed, "bytes": total}


FRAME = re.compile(r"cand-\d{4}\.png")


def discard(job: dict, should_stop) -> dict:
    """Delete a reviewed work's extra frames (ADR-0009: surplus pixels go
    once review is done; records stay). Only `cand-NNNN.png` files directly
    in the work's surplus folder, and never one the job says to keep (the
    picked stills, any locked frame). The folder must be
    runs/<work>/extract/surplus/<bundle> under /output; anything else is
    refused before a single file goes. Safe to run again."""
    params, work = job["params"], job["work_id"]
    if not WORK_ID.fullmatch(work):
        raise StageError("input", f"{work!r} is not a work id; nothing was deleted")
    if not _runs_mounted():
        raise StageError("transient", f"{OUTPUT / 'runs'} is not mounted here (check the container's paths); nothing was deleted")
    rel = str(params.get("surplus_dir") or "")
    parts = rel.split("/")
    if len(parts) != 5 or parts[:4] != ["runs", work, "extract", "surplus"] or not parts[4] or parts[4].startswith("."):
        raise StageError("input", f"{rel!r} is not a surplus folder of {work}; nothing was deleted")
    keep = {str(c) for c in (params.get("keep") or [])}
    wanted = [str(n) for n in (params.get("delete") or [])]
    if not wanted:
        raise StageError("input", "the job names no frames to delete; nothing was deleted")
    # Walk down by directory handle, refusing a link at every step, and
    # delete by name relative to the final handle: a folder swapped for a
    # link after the check cannot carry the deletion elsewhere.
    fd = os.open(OUTPUT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            try:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as e:
                raise StageError("input", f"{OUTPUT / rel}: {part!r} is a link or not a folder ({e.strerror}); nothing was deleted")
            os.close(fd)
            fd = nfd
        present = set(os.listdir(fd))
        kept = deleted = bytes_gone = 0
        for name in wanted:
            if should_stop():
                raise StageError("cancelled", "stopped on request; frames already deleted stay deleted")
            # Only a frame the page listed, never a kept one, only a regular
            # file, and only where the page saw it.
            if not FRAME.fullmatch(name) or name[:-4] in keep or name not in present:
                continue
            try:
                st = os.lstat(name, dir_fd=fd)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            try:
                os.unlink(name, dir_fd=fd)
            except OSError as e:
                raise StageError("source", f"could not delete {OUTPUT / rel / name}: {e.strerror or e}. Fix its owner or delete it on the host, then retry.")
            deleted += 1
            bytes_gone += st.st_size
        kept = sum(1 for n in present if FRAME.fullmatch(n) and n[:-4] in keep)
    finally:
        os.close(fd)
    print(f"[discard {work}] deleted {deleted} extra frames ({bytes_gone / 2**20:.0f} MiB), kept {kept}", flush=True)
    return {"surplus_dir": rel, "deleted": deleted, "kept": kept, "bytes": bytes_gone}


PALETTE_BIN = Path(os.environ.get("HIKARI_PALETTE_BIN", "/opt/hikari/bin/palette-descriptor"))


def palette_version() -> str:
    """The binary's own version, from its output: what the descriptors will
    carry, so the folder is named by what is in it."""
    try:
        said = subprocess.run([str(PALETTE_BIN), "--identity"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        raise StageError("transient", f"{PALETTE_BIN} did not answer --identity: {e}")
    if not re.fullmatch(r"palette-descriptor-\d+\.\d+\.\d+", said):
        raise StageError("transient", f"{PALETTE_BIN} --identity said {said!r}")
    return said.split("-")[-1]


def palette_abstained(stderr: str | None, dest: Path, version: str) -> bool:
    """A frame with nothing but black, white and grey (an intertitle card, a
    pencil sequence) has no palette: the descriptor exits 1 saying so, by
    design, and the extraction stage records that as an "abstained"
    descriptor rather than a failure. Writes the same record at dest and
    returns True when stderr is that answer; False for any other failure."""
    if "no usable palette colors" not in (stderr or ""):
        return False
    dest.write_text(json.dumps({"descriptor_schema": "hikari-color-descriptor/1", "status": "abstained",
                                "reason": "no-usable-palette-colors", "provider": "palette-descriptor",
                                "provider_version": version}, indent=2) + "\n")
    return True


def palette(job: dict, should_stop) -> dict:
    """Run the palette descriptor again over a work's stills, from their
    masters, into runs/<work>/palette/<provider version>/ (the delivered
    extraction results are never touched; import prefers the newest
    palette folder). One descriptor per still the gallery has (the web
    images' manifest): a bundle frame from the prepared bundle, a surplus
    frame from the surplus folder, or skipped when its master is gone
    (extra frames discarded after review). Safe to run again."""
    work = job["work_id"]
    if not WORK_ID.fullmatch(work):
        raise StageError("input", f"{work!r} is not a work id")
    if not PALETTE_BIN.is_file():
        raise StageError("transient", f"{PALETTE_BIN} is missing from this image")
    work_root = OUTPUT / "runs" / work
    if not (work_root / "extract").is_dir():
        raise StageError("input", f"no extraction records for {work} ({work_root / 'extract'}); nothing to describe")
    ladder_path = IMAGES / work / "derivatives-manifest.json"
    if not ladder_path.is_file():
        raise StageError("input", f"no web images for {work} ({ladder_path}); nothing to describe")
    prepared = [p for p in (work_root / "extract" / "prepared").glob("*") if p.is_dir()] if (work_root / "extract" / "prepared").is_dir() else []
    if len(prepared) != 1:
        raise StageError("input", f"expected one prepared bundle under {work_root / 'extract' / 'prepared'}, found {len(prepared)}")
    bundle = json.loads((prepared[0] / "manifest.json").read_text())
    masters = {c["candidate_id"]: prepared[0] / c["artifact_name"] for c in bundle["candidates"]}
    surplus_dirs = [p for p in (work_root / "extract" / "surplus").glob("*") if p.is_dir()] if (work_root / "extract" / "surplus").is_dir() else []
    ladder = json.loads(ladder_path.read_text())
    version = palette_version()
    out_root = work_root / "palette" / version
    art = out_root / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    done = skipped = failed = 0
    items = []
    for entry in ladder.get("candidates") or []:
        if should_stop():
            raise StageError("cancelled", "stopped on request; descriptors already written stay")
        cid = entry["candidate_id"]
        src = masters.get(cid)
        if src is None:
            for d in surplus_dirs:
                if (d / f"{cid}.png").is_file():
                    src = d / f"{cid}.png"
                    break
        if src is None or not src.is_file():
            skipped += 1
            items.append({"candidate_id": cid, "status": "skipped", "reason": "no master on disk"})
            continue
        dest = art / f"{cid}.descriptor.json"
        proc = subprocess.run(["nice", "-n", "10", str(PALETTE_BIN), str(src), str(dest)], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            if palette_abstained(proc.stderr, dest, version):
                items.append({"candidate_id": cid, "status": "abstained", "artifact_name": dest.name})
                done += 1
                continue
            failed += 1
            items.append({"candidate_id": cid, "status": "failed", "reason": (proc.stderr or "").strip()[:200]})
            continue
        items.append({"candidate_id": cid, "status": "success", "artifact_name": dest.name,
                      "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "bytes": dest.stat().st_size})
        done += 1
    manifest = {"provider": "palette-descriptor", "provider_version": version, "work_id": work,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "candidates": items}
    (out_root / "result-manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    if failed and not done:
        raise StageError("transient", f"the palette descriptor failed on every still of {work}; see {out_root / 'result-manifest.json'}")
    print(f"[palette {work}] {done} described, {skipped} without a master, {failed} failed -> palette/{version}", flush=True)
    return {"palette_dir": str(out_root.relative_to(OUTPUT)), "provider_version": version,
            "described": done, "skipped": skipped, "failed": failed}


STAGES = {"extract": extract, "derive": derive, "scrub": scrub, "discard": discard, "palette": palette, "list": listing}
# Run by the listing thread (__main__.Lister), never by the main loop.
LISTING_STAGES = ["list"]


class Heartbeat(threading.Thread):
    """Keeps the lease alive on its own connection while a stage runs, and
    notices a cancel request (the Jobs page's Cancel) within about 15 s."""

    def __init__(self, connect, job_id, worker_id, beat, every: float = 15.0):
        super().__init__(daemon=True)
        self.connect, self.job_id, self.worker_id, self.beat, self.every = connect, job_id, worker_id, beat, every
        self.cancel = threading.Event()
        self.lost = threading.Event()
        self.done = threading.Event()

    def run(self):
        con = None
        while not self.done.wait(self.every):
            try:
                con = con or self.connect()
                state = self.beat(con, self.job_id, self.worker_id)
                if state is None:
                    self.lost.set()
                elif state == "cancel_requested":
                    self.cancel.set()
            except Exception:
                con = None  # the database is back next beat; the lease has minutes of slack
        if con:
            con.close()
