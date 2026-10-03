"""The analyze worker: the analyze stage, on a machine with an NVIDIA GPU
(containers/rtx-worker) or without one
(containers/cpu-worker, HIKARI_DEVICE=cpu; same results, measured
2026-09-28).

    python -m hikari_worker.analyze

Reaches the job table only through the gallery's worker API (ADR-0004;
decided 2026-09-28: no database credential on an analyze machine).
Reads the bundle and surplus read-only over the share
(ADR-0003: never the raw library), writes nothing to the share, keeps its
working files in memory (/tmp is a tmpfs), and hands the records back to
the gallery, which stores them beside the run.

The analyze stage, in order:
    WD tagger, face detector, SigLIP 2 embeddings over the bundle; SigLIP 2
    over the surplus (so the picker sees the whole measured pool); the
    annotation fusion; the stratified picker (ADR-0008); then tags, faces,
    palette and labels for the surplus frames the picker took.

Settings (environment):
    HIKARI_GALLERY_URL    e.g. http://192.0.2.10:5183 (an IP address
                          avoids a name lookup on every connection)
    HIKARI_WORKER_TOKEN   this worker's token (its entry in the gallery's
                          HIKARI_WORKER_TOKENS)
    HIKARI_WORKER_ID      default "analyze"
    HIKARI_SHARE          where the data folder is mounted, default /share
    HIKARI_DEVICE         default "cuda"
    HIKARI_POLL_SECONDS   default 60
    HIKARI_WORKER_STANDBY "1" = take a stage only while no regular analyze
                          worker has checked in for 10 minutes (the
                          always-on CPU machine standing in for a GPU one)
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .stages import PALETTE_BIN, StageError, palette_abstained, palette_version, run_logged

VERSION = os.environ.get("HIKARI_WORKER_VERSION", "dev")
SHARE = Path(os.environ.get("HIKARI_SHARE", "/share"))
DEVICE = os.environ.get("HIKARI_DEVICE", "cuda")
STANDBY = os.environ.get("HIKARI_WORKER_STANDBY", "").strip().lower() in ("1", "true", "yes")


def log(msg: str) -> None:
    print(f"[analyze-worker] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


class Refused(RuntimeError):
    """The gallery answered and said no (a token, name or configuration
    problem), as opposed to not answering."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


class Api:
    def __init__(self):
        self.base = os.environ["HIKARI_GALLERY_URL"].rstrip("/")
        self.token = os.environ["HIKARI_WORKER_TOKEN"]
        self.worker = os.environ.get("HIKARI_WORKER_ID", "analyze")

    def post(self, path: str, body: bytes, content_type: str, timeout: float = 60):
        req = urllib.request.Request(self.base + path, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.token}",
            "X-Hikari-Worker": self.worker,
            "Content-Type": content_type,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                detail = json.loads(body).get("message") or ""
            except ValueError:
                detail = "" if "<html" in body[:200].lower() else " ".join(body.split())[:160]
            if e.code == 404 and not detail:
                detail = "this gallery has no worker API yet (an older gallery image?)"
            message = f"{path} returned HTTP {e.code}{': ' + detail if detail else ''}"
            if e.code in (400, 401, 403, 503):
                raise Refused(message, e.code) from None
            raise RuntimeError(message) from None

    def call(self, path: str, payload: dict, timeout: float = 60):
        return self.post(path, json.dumps(payload).encode(), "application/json", timeout)


class Beat(threading.Thread):
    """Keeps the lease through the gallery and notices a cancel."""

    def __init__(self, api: Api, job_id: str, every: float = 15.0):
        super().__init__(daemon=True)
        self.api, self.job_id, self.every = api, job_id, every
        self.cancel, self.lost, self.done = threading.Event(), threading.Event(), threading.Event()

    def run(self):
        while not self.done.wait(self.every):
            try:
                state = self.api.call(f"/api/worker/jobs/{self.job_id}/heartbeat", {}, timeout=20).get("state")
                if state is None:
                    self.lost.set()
                elif state == "cancel_requested":
                    self.cancel.set()
            except Exception:
                pass  # the gallery is back next beat; the lease has minutes of slack


def analyze(job: dict, should_stop) -> tuple[Path, dict]:
    work = job["work_id"]
    extract = next((p for p in job["chain"] if p["stage"] == "extract"), None)
    er = (extract or {}).get("result") or {}
    if not er.get("prepared_bundle") or not er.get("run_root"):
        raise StageError("input", "the extract stage recorded no bundle")
    run_root = SHARE / er["run_root"]
    if not run_root.is_dir():
        raise StageError("transient", f"the run root is not readable at {run_root} (is the share mounted?)")
    # The bundle is the one folder under prepared/, found by listing, as the
    # picker and the import do: a name Windows cannot hold (one ending in "."
    # from an episode title) reaches this container over SMB under a
    # substitute name, so the recorded name does not exist here.
    bundle = SHARE / er["prepared_bundle"]
    if not bundle.is_dir():
        found = [p for p in (run_root / "prepared").iterdir() if p.is_dir()] if (run_root / "prepared").is_dir() else []
        if len(found) != 1:
            raise StageError("input", f"expected one bundle folder under {run_root / 'prepared'}, found {len(found)}")
        bundle = found[0]
    colour = run_root / er["palette_result"] if er.get("palette_result") else None
    surplus = SHARE / er["surplus_dir"] if er.get("surplus_dir") else None
    out = Path("/tmp") / f"analyze-{job['id'][:8]}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    log_path = out / "analyze.log"
    started = time.time()

    def step(label, *args):
        code = run_logged(["python", "-m", *args], log_path, should_stop, f"{label} {work}")
        if code != 0:
            raise StageError("transient", f"{label} exited {code}")

    step("tags", "inference.tag_bundle", "--bundle", str(bundle), "--out", str(out / "tags"), "--device", DEVICE)
    step("faces", "inference.detect_faces", "--bundle", str(bundle), "--out", str(out / "faces"), "--device", DEVICE)
    step("embed", "inference.embed_bundle", "--bundle", str(bundle), "--out", str(out / "embeddings"), "--device", DEVICE)
    if surplus and surplus.is_dir():
        step("embed-surplus", "inference.embed_loose", "--dir", str(surplus), "--out", str(out / "surplus-embeddings"), "--device", DEVICE)
    if not (colour and colour.is_dir()):
        raise StageError("input", "the extract stage recorded no colour results (the palette descriptors)")
    fuse = ["annotation.cli", "--bundle", str(bundle), "--results", str(colour),
            "--tags", str(out / "tags" / "result-manifest.json"), "--faces", str(out / "faces" / "result-manifest.json"),
            "--out", str(out / "proposals.json"), "--run-ref", f"run-{work}"]
    coverage = run_root / "audit" / "shot-coverage.json"
    if coverage.is_file():
        fuse += ["--shot-coverage", str(coverage)]
    step("annotate", *fuse)
    # The operator's fewer / balanced / more (the gallery's BUDGETS); a chain
    # queued before the choice existed has none and gets balanced.
    factor = float(((job.get("params") or {}).get("budget") or {}).get("factor") or 1.0)
    pick = ["selection.cli", "--run-root", str(run_root), "--results-dir", str(out), "--out", str(out / "selection.json")]
    if factor != 1.0:
        pick += ["--budget-factor", str(factor)]
    # The work's locked frames (ADR-0008 operator pins), from the gallery.
    for cid in (job.get("params") or {}).get("pins") or []:
        pick += ["--pin", str(cid)]
    step("pick", *pick)
    sel = json.loads((out / "selection.json").read_text())
    labeled = label_picked_surplus(sel, bundle, run_root, surplus, out, work, step, should_stop)
    summary = {"selected": len(sel.get("selected") or []), "pool": sel.get("pool"), "budget": sel.get("budget"),
               "pinned": len(sel.get("pinned") or []), "pins_missing": sel.get("pins_missing") or [],
               "surplus_labeled": labeled, "device": DEVICE, "seconds": round(time.time() - started, 1)}
    return out, summary


def label_picked_surplus(sel, bundle, run_root, surplus, out, work, step, should_stop) -> int:
    """Tags, faces, palette and labels for the surplus frames the picker took.

    Extraction measures colour and the annotation labels only the bundle, so a
    picked surplus frame reached the gallery with an embedding and nothing
    else (measured 2026-09-28: 342 of 1,528 stills). Labeling the whole pool
    before the pick costs about 20x more for frames that are mostly
    discarded, so only the picks are labeled, after the pick (maintainer
    decision 2026-09-28; ADR-0008 amendment). The pick itself is unchanged:
    surplus frames still compete without palette and category terms.

    Writes surplus-picks.json, surplus-tags/, surplus-faces/,
    surplus-colour/artifacts/ and surplus-proposals.json beside the bundle's
    records. The palette tool is the same binary the extraction runs (the
    image checks its sha256), so its descriptors match the bundle's.
    """
    manifest = json.loads((bundle / "manifest.json").read_text())
    in_bundle = {c["candidate_id"] for c in manifest["candidates"]}
    picked = [cid for cid in sel.get("selected") or [] if cid not in in_bundle]
    if not picked:
        return 0
    if not (surplus and surplus.is_dir()):
        raise StageError("input", f"{len(picked)} picks are surplus frames but the extract recorded no surplus folder")
    audit_path = run_root / "audit" / "breadth-omitted-candidates.json"
    rows = json.loads(audit_path.read_text()) if audit_path.is_file() else []
    rows = rows if isinstance(rows, list) else next(iter(rows.values()), [])
    audit = {r["candidate_id"]: r for r in rows}
    width = int(manifest["candidates"][0].get("width") or 0) if manifest["candidates"] else 0
    candidates = []
    for cid in picked:
        r = audit.get(cid)
        if r is None:
            raise StageError("input", f"picked surplus frame {cid} has no breadth-omitted audit record")
        candidates.append({"candidate_id": cid, "shot_id": r["shot_id"], "width": width,
                           "frame_quality": r.get("frame_quality") or {}})
    picks_file = out / "surplus-picks.json"
    picks_file.write_text(json.dumps({"bundle_id": manifest["bundle_id"], "candidates": candidates},
                                     sort_keys=True, indent=2) + "\n")
    step("tags-surplus", "inference.tag_bundle", "--loose", str(surplus), "--candidates", str(picks_file),
         "--out", str(out / "surplus-tags"), "--device", DEVICE)
    step("faces-surplus", "inference.detect_faces", "--loose", str(surplus), "--candidates", str(picks_file),
         "--out", str(out / "surplus-faces"), "--device", DEVICE)
    colour = out / "surplus-colour"
    (colour / "artifacts").mkdir(parents=True)
    version = palette_version()
    abstained = 0
    for cid in picked:
        if should_stop():
            raise StageError("cancelled", "stopped while measuring colour")
        dest = colour / "artifacts" / f"{cid}.descriptor.json"
        done = subprocess.run([str(PALETTE_BIN), str(surplus / f"{cid}.png"), str(dest)],
                              capture_output=True, text=True)
        if done.returncode != 0:
            # A black-and-white frame has no palette; that is an answer, not
            # a failure (the extraction records the same for bundle frames).
            if palette_abstained(done.stderr, dest, version):
                abstained += 1
                continue
            raise StageError("transient", f"palette for {cid} exited {done.returncode}: {done.stderr.strip()[-300:]}")
    log(f"[palette-surplus {work}] {len(picked)} descriptors"
        + (f" ({abstained} with no palette: black and white only)" if abstained else ""))
    step("annotate-surplus", "annotation.cli", "--candidates", str(picks_file), "--results", str(colour),
         "--tags", str(out / "surplus-tags" / "result-manifest.json"),
         "--faces", str(out / "surplus-faces" / "result-manifest.json"),
         "--out", str(out / "surplus-proposals.json"), "--run-ref", f"run-{work}")
    return len(picked)


def pack(out: Path) -> list[dict]:
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="strict")
            files.append({"path": p.relative_to(out).as_posix(), "text": text,
                          "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    return files


def main() -> int:
    api = Api()
    poll = float(os.environ.get("HIKARI_POLL_SECONDS", "60"))
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    starting = True
    waiting_for = None
    trouble_since = None  # when the gallery first refused or went unreachable
    trouble_polls = 0
    log(f"{api.worker} {VERSION} starting; gallery {api.base}; device {DEVICE}"
        + ("; standby: takes analyze stages only while no regular analyze worker is on" if STANDBY else ""))
    while not stopping.is_set():
        try:
            reply = api.call("/api/worker/lease", {"version": VERSION, "starting": starting, "standby": STANDBY})
            if trouble_since is not None:
                # Say so, or the last error reads as current for as long as
                # the log is open.
                lasted = time.monotonic() - trouble_since
                log(f"the gallery answers again after {lasted / 60:.0f} min ({trouble_polls} failed poll{'s' if trouble_polls != 1 else ''})")
                trouble_since, trouble_polls = None, 0
            if starting and reply.get("released"):
                log(f"returned {reply['released']} stage(s) left running by the previous start to the queue")
            starting = False
            job = reply.get("job")
            if reply.get("standing_by_for") != waiting_for:
                waiting_for = reply.get("standing_by_for")
                log(f"{api.worker} is standing by: the regular analyze worker {waiting_for} is on" if waiting_for
                    else f"{api.worker}: no regular analyze worker is on; taking stages")
        except Exception as e:
            if trouble_since is None:
                trouble_since = time.monotonic()
            trouble_polls += 1
            if isinstance(e, Refused) and e.code == 401:
                log(f"the gallery refused {api.worker}'s token ({e}); is \"{api.worker}:<this token>\" in its "
                    f"HIKARI_WORKER_TOKENS? retrying in {poll:.0f}s")
            elif isinstance(e, Refused):
                log(f"the gallery refused {api.worker} ({e}); retrying in {poll:.0f}s")
            else:
                log(f"gallery not reachable ({e}); retrying in {poll:.0f}s")
            stopping.wait(poll)
            continue
        if not job:
            stopping.wait(poll)
            continue
        log(f"leased {job['stage']} for {job['work_id']} (attempt {job['attempt']}/{job['max_attempts']})")
        beat = Beat(api, job["id"])
        beat.start()
        should_stop = lambda: stopping.is_set() or beat.cancel.is_set() or beat.lost.is_set()
        out = None
        try:
            if job["stage"] != "analyze":
                raise StageError("input", f"this worker has no stage named {job['stage']}")
            out, summary = analyze(job, should_stop)
            files = pack(out)
            body = gzip.compress(json.dumps({"files": files, "summary": summary}).encode("utf-8"))
            log(f"uploading {len(files)} files ({len(body) / 1048576:.1f} MiB compressed)")
            reply = api.post(f"/api/worker/jobs/{job['id']}/result", body, "application/gzip", timeout=300)
            log(f"analyze for {job['work_id']}: {summary['selected']} picked of {summary['pool']} -> {reply.get('state')}")
        except StageError as e:
            cls = e.error_class
            stopped = stopping.is_set() and not beat.cancel.is_set()
            if cls == "cancelled" and stopped:
                cls = "transient"
            msg = "the worker was stopped; it runs again when the worker is back" if stopped else str(e)
            try:
                state = api.call(f"/api/worker/jobs/{job['id']}/fail", {"error_class": cls, "message": msg, "retry_now": stopped}).get("state")
            except Exception:
                state = "unknown (gallery unreachable; the lease will expire)"
            log(f"analyze for {job['work_id']}: {cls} -> {state}")
        except Exception as e:
            try:
                state = api.call(f"/api/worker/jobs/{job['id']}/fail", {"error_class": "transient", "message": f"worker error: {e}"}).get("state")
            except Exception:
                state = "unknown (gallery unreachable; the lease will expire)"
            log(f"analyze for {job['work_id']}: {e} -> {state}")
        finally:
            beat.done.set()
            if out:
                shutil.rmtree(out, ignore_errors=True)
    log("stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
