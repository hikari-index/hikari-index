"""The worker's side of the job table (ADR-0011, research/01 "Recoverable job
protocol"): check in, lease one runnable stage, keep the lease alive, and
end it as committed, retried, blocked, ineligible, cancelled or dead.

A stage is runnable when its capability is ours, it is queued (or waiting
out a retry, or its lease expired under a dead worker), and its parent
stage has committed. Row locks (FOR UPDATE SKIP LOCKED) make two workers
unable to take the same stage.
"""

from __future__ import annotations

import json
import os
import urllib.parse

import pg8000.native

LEASE = "10 minutes"
RETRY_BACKOFF_MINUTES = 15  # times the attempt number


def connect(url: str | None = None) -> pg8000.native.Connection:
    u = urllib.parse.urlparse(url or os.environ["DATABASE_URL"])
    return pg8000.native.Connection(
        user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""),
        host=u.hostname,
        port=u.port or 5432,
        database=u.path.lstrip("/"),
        timeout=30,
    )


def _rows(con, sql, **params):
    rows = con.run(sql, **params)
    cols = [c["name"] for c in con.columns] if con.columns else []
    return [dict(zip(cols, r)) for r in rows]


def check_in(con, worker_id: str, capabilities: list[str], version: str, source_roots: list[str]) -> None:
    """source_roots: the names of the source folders mounted here (never
    their paths), for the gallery's local-file onboarding page."""
    con.run(
        "insert into workers (id, capabilities, version, source_roots, last_seen_at) "
        "values (:id, cast(:caps as jsonb), :v, cast(:roots as jsonb), now()) "
        "on conflict (id) do update set capabilities = excluded.capabilities, version = excluded.version, "
        "source_roots = excluded.source_roots, last_seen_at = now()",
        id=worker_id, caps=json.dumps(capabilities), v=version, roots=json.dumps(source_roots),
    )


def release_own(con, worker_id: str) -> int:
    """At start-up: stages this worker held when it last stopped go back to
    the queue at once (their attempt already counted)."""
    rows = con.run(
        "update jobs set state = case when state = 'cancel_requested' then 'cancelled' else 'retry_wait' end, "
        "not_before = now(), owner = null, lease_expires_at = null, updated_at = now(), "
        "error_class = case when state = 'cancel_requested' then error_class else 'transient' end, "
        "error_message = case when state = 'cancel_requested' then error_message else 'the worker stopped while this stage ran' end "
        "where owner = :w and state in ('leased', 'running', 'cancel_requested') returning id",
        w=worker_id,
    )
    return len(rows)


def lease(con, worker_id: str, capabilities: list[str], stages: list[str] | None = None,
          exclude: list[str] | None = None) -> dict | None:
    """Take the oldest runnable stage, or None. A stage out of attempts is
    marked dead_letter instead of being handed out again. `stages` limits
    the lease to those stage names, `exclude` keeps those out: the main
    loop leaves folder listings to the listing thread, which takes nothing
    else (ADR-0014 amendment)."""
    con.run("begin")
    try:
        found = _rows(
            con,
            "select j.* from jobs j left join jobs p on p.id = j.parent_id "
            "where j.capability = any(cast(:caps as text[])) "
            "and (cast(:only as text[]) is null or j.stage = any(cast(:only as text[]))) "
            "and j.stage <> all(cast(:skip as text[])) "
            "and ((j.state in ('queued', 'retry_wait') and (j.not_before is null or j.not_before <= now())) "
            "     or (j.state in ('leased', 'running') and j.lease_expires_at < now())) "
            "and (j.parent_id is null or p.state = 'committed') "
            "order by j.created_at, j.id for update of j skip locked limit 1",
            caps="{" + ",".join(capabilities) + "}",
            only="{" + ",".join(stages) + "}" if stages else None,
            skip="{" + ",".join(exclude or []) + "}",
        )
        if not found:
            con.run("commit")
            return None
        job = found[0]
        if job["attempt"] >= job["max_attempts"]:
            con.run(
                "update jobs set state = 'dead_letter', owner = null, lease_expires_at = null, finished_at = now(), updated_at = now() where id = :id",
                id=job["id"],
            )
            con.run("commit")
            return None
        job = _rows(
            con,
            "update jobs set state = 'leased', owner = :w, attempt = attempt + 1, "
            f"lease_expires_at = now() + interval '{LEASE}', heartbeat_at = now(), "
            # started_at is when this try began (the gallery's leaseFor does the same)
            "started_at = now(), updated_at = now(), not_before = null "
            "where id = :id returning *",
            w=worker_id, id=job["id"],
        )[0]
        con.run("commit")
        return job
    except Exception:
        con.run("rollback")
        raise


def mark_running(con, job_id) -> None:
    con.run("update jobs set state = 'running', updated_at = now() where id = :id and state = 'leased'", id=job_id)


def heartbeat(con, job_id, worker_id: str) -> str | None:
    """Extend the lease; returns the stage's state (so the caller sees a
    cancel request), or None if the stage is no longer ours."""
    rows = con.run(
        f"update jobs set heartbeat_at = now(), lease_expires_at = now() + interval '{LEASE}', updated_at = now() "
        "where id = :id and owner = :w returning state",
        id=job_id, w=worker_id,
    )
    # A busy worker is still a live one on the Jobs page.
    con.run("update workers set last_seen_at = now() where id = :w", w=worker_id)
    return rows[0][0] if rows else None


def parent_chain(con, job) -> list[dict]:
    """The stages before this one, nearest first."""
    out, parent = [], job.get("parent_id")
    while parent:
        rows = _rows(con, "select * from jobs where id = :id", id=parent)
        if not rows:
            break
        out.append(rows[0])
        parent = rows[0]["parent_id"]
    return out


def commit(con, job_id, worker_id: str, result: dict) -> str:
    """Record the result. A cancel requested while the stage ran wins: the
    stage ends cancelled (its output kept on the record), so nothing
    after it starts. Returns the final state."""
    rows = con.run(
        "update jobs set state = case when state = 'cancel_requested' then 'cancelled' else 'committed' end, "
        "result = cast(:r as jsonb), owner = null, lease_expires_at = null, "
        "error_class = case when state = 'cancel_requested' then 'cancelled' else null end, "
        "error_message = case when state = 'cancel_requested' then 'cancelled while it ran; its output is kept' else null end, "
        "finished_at = now(), updated_at = now() where id = :id and owner = :w returning state",
        id=job_id, w=worker_id, r=json.dumps(result),
    )
    return rows[0][0] if rows else "lost"


def fail(con, job, worker_id: str, error_class: str, message: str, retry_now: bool = False) -> str:
    """End an attempt that did not commit. transient -> retry later (or
    dead_letter when out of attempts); input -> ineligible; source ->
    blocked for the operator; cancelled -> cancelled. Returns the state."""
    message = message[-2000:]
    if error_class == "cancelled":
        state = "cancelled"
    elif error_class == "input":
        state = "ineligible"
    elif error_class == "source":
        state = "blocked"
    elif job["attempt"] >= job["max_attempts"]:
        state = "dead_letter"
    else:
        state = "retry_wait"
    # A stop of the worker is not the stage's fault: no backoff for it.
    backoff = 0 if retry_now else RETRY_BACKOFF_MINUTES * max(1, job["attempt"])
    con.run(
        "update jobs set state = :s, owner = null, lease_expires_at = null, error_class = :c, error_message = :m, "
        "not_before = case when :s = 'retry_wait' then now() + make_interval(mins => :b) else null end, "
        "finished_at = case when :s in ('retry_wait') then null else now() end, updated_at = now() "
        "where id = :id and owner = :w",
        s=state, c=error_class, m=message, b=backoff, id=job["id"], w=worker_id,
    )
    return state
