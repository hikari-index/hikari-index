"""The source worker (the one that mounts the video): poll the job table,
run one stage at a time.

    python -m hikari_worker

Settings (environment):
    DATABASE_URL          postgres://user:password@<ip>:5432/db (an IP: on
                          some container networks a name lookup waits
                          seconds on every connection)
    HIKARI_SOURCE_ROOTS   source folder name -> mount, comma-separated,
                          e.g. "4=/source" (for files onboarded from Shoko
                          the name is Shoko's managed folder id) or
                          "films=/source/films" (any name, for files added
                          without Shoko; the gallery offers the names)
    HIKARI_WORKER_ID      default "source"
    HIKARI_WORKER_HOURS   optional window to START stages in, e.g. "01-07",
                          on the container's clock (UTC unless TZ is set);
                          a stage already running finishes
    HIKARI_POLL_SECONDS   default 60
    HIKARI_LIST_POLL_SECONDS
                          how often the listing thread looks for a folder
                          to list, default 5

One stage at a time, on purpose: extraction saturates the CPU, and the
machine that holds the video usually runs other services too. The one exception is a folder listing for the
gallery's "add a folder" page: one directory read, run by its own thread
so the operator is not kept waiting behind an extraction.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
import traceback

from . import jobs
from .stages import LISTING_STAGES, STAGES, Heartbeat, StageError, source_roots

VERSION = os.environ.get("HIKARI_WORKER_VERSION", "dev")
CAPABILITIES = ["source"]


def log(msg: str) -> None:
    print(f"[worker] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def in_window(spec: str | None) -> bool:
    if not spec:
        return True
    start, end = (int(x) for x in spec.split("-"))
    hour = time.localtime().tm_hour
    return start <= hour < end if start <= end else hour >= start or hour < end


class Lister(threading.Thread):
    """Folder listings on their own connection, a few seconds apart, beside
    whatever the main loop is running. A listing is one directory read, so
    it needs no heartbeat: the lease outlasts it many times over."""

    def __init__(self, worker_id: str, stopping: threading.Event, every: float):
        super().__init__(daemon=True)
        self.worker_id, self.stopping, self.every = worker_id, stopping, every

    def run(self):
        con = None
        while not self.stopping.is_set():
            try:
                con = con or jobs.connect()
                job = jobs.lease(con, self.worker_id, CAPABILITIES, stages=LISTING_STAGES)
            except Exception as e:
                log(f"listing: database not reachable ({e.__class__.__name__}: {e})")
                con = None
                self.stopping.wait(self.every)
                continue
            if job is None:
                self.stopping.wait(self.every)
                continue
            folder = job["params"].get("folder") or {}
            label = f"{folder.get('root')}:{folder.get('path') or ''}"
            try:
                jobs.mark_running(con, job["id"])
                result = STAGES[job["stage"]](job, self.stopping.is_set)
                state = jobs.commit(con, job["id"], self.worker_id, result)
                log(f"listed {label}: {result['videos']} video file(s) -> {state}")
            except StageError as e:
                try:
                    state = jobs.fail(con, job, self.worker_id, e.error_class, str(e))
                except Exception:
                    state = "unknown (database unreachable; the lease will expire)"
                    con = None
                log(f"listing {label}: {e.error_class} -> {state}")
            except Exception as e:
                detail = "".join(traceback.format_exception_only(type(e), e)).strip()
                try:
                    state = jobs.fail(con, job, self.worker_id, "transient", f"worker error: {detail}")
                except Exception:
                    state = "unknown (database unreachable; the lease will expire)"
                    con = None
                log(f"listing {label}: {detail} -> {state}")


def main() -> int:
    worker_id = os.environ.get("HIKARI_WORKER_ID", "source")
    poll = float(os.environ.get("HIKARI_POLL_SECONDS", "60"))
    window = os.environ.get("HIKARI_WORKER_HOURS") or None
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    con = None
    released = False
    lister = None
    log(f"{worker_id} {VERSION} starting; capabilities {CAPABILITIES}; window {window or 'always'}")
    while not stopping.is_set():
        try:
            con = con or jobs.connect()
            jobs.check_in(con, worker_id, CAPABILITIES, VERSION, sorted(source_roots()))
            if not released:
                n = jobs.release_own(con, worker_id)
                if n:
                    log(f"returned {n} stage(s) left running by the previous start to the queue")
                released = True
            if lister is None:
                # Only after the start-up recovery above: it returns every
                # stage this worker id holds to the queue, and would take
                # back a listing this thread had just leased.
                lister = Lister(worker_id, stopping, float(os.environ.get("HIKARI_LIST_POLL_SECONDS", "5")))
                lister.start()
            job = jobs.lease(con, worker_id, CAPABILITIES, exclude=LISTING_STAGES) if in_window(window) else None
        except Exception as e:
            log(f"database not reachable ({e.__class__.__name__}: {e}); retrying in {poll:.0f}s")
            con = None
            stopping.wait(poll)
            continue
        if job is None:
            stopping.wait(poll)
            continue

        stage = STAGES.get(job["stage"])
        log(f"leased {job['stage']} for {job['work_id']} (attempt {job['attempt']}/{job['max_attempts']})")
        beat = Heartbeat(jobs.connect, job["id"], worker_id, jobs.heartbeat)
        beat.start()
        should_stop = lambda: stopping.is_set() or beat.cancel.is_set() or beat.lost.is_set()
        try:
            if stage is None:
                raise StageError("input", f"this worker has no stage named {job['stage']}")
            jobs.mark_running(con, job["id"])
            if job["stage"] == "derive":
                result = stage(job, jobs.parent_chain(con, job), should_stop)
            else:
                result = stage(job, should_stop)
            beat.done.set()
            state = jobs.commit(con, job["id"], worker_id, result)
            log(f"{job['stage']} for {job['work_id']}: done -> {state}")
        except StageError as e:
            beat.done.set()
            cls = e.error_class
            if cls == "cancelled" and stopping.is_set() and not beat.cancel.is_set():
                cls = "transient"  # the container is stopping; run it again later
            stopped = cls == "transient" and stopping.is_set()
            msg = "the worker was stopped; it runs again when the worker is back" if stopped else str(e)
            state = jobs.fail(con, job, worker_id, cls, msg, retry_now=stopped)
            log(f"{job['stage']} for {job['work_id']}: {cls} -> {state}")
        except Exception as e:
            beat.done.set()
            detail = "".join(traceback.format_exception_only(type(e), e)).strip()
            try:
                state = jobs.fail(con, job, worker_id, "transient", f"worker error: {detail}")
            except Exception:
                state = "unknown (database unreachable; the lease will expire)"
                con = None
            log(f"{job['stage']} for {job['work_id']}: {detail} -> {state}")
        finally:
            beat.done.set()
    log("stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
