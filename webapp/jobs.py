"""In-flight job tracking: live result streaming and mid-run cancellation.

A job wraps one asyncio task over the ``ultra_scraper`` engine. Subscribers get
an ``asyncio.Queue`` of events, which is what lets the dashboard paint each
profile the moment it lands instead of blocking on the whole batch.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# Events are small JSON dicts: {"type": ..., ...}
#   started  — job accepted, total may be unknown yet
#   progress — one result, plus done/total counters
#   log      — human-readable status line
#   finished — terminal; carries status and the summary counts
Event = dict[str, Any]


@dataclass
class Job:
    """One user-initiated run."""

    id: str
    kind: str
    params: dict
    status: str = "queued"          # queued | running | done | cancelled | error
    error: str = ""
    total: int = 0
    done: int = 0
    results: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    run_id: str = ""
    task: asyncio.Task | None = None
    _subscribers: list[asyncio.Queue] = field(default_factory=list)
    _history: list[Event] = field(default_factory=list)

    # -- event plumbing ----------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        """Attach a listener, replaying what it missed.

        A browser that connects after the job starts — or reconnects after a
        dropped stream — still needs the earlier results, so the backlog is
        replayed before any new events.
        """
        queue: asyncio.Queue = asyncio.Queue()
        for event in self._history:
            queue.put_nowait(event)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def emit(self, event: Event) -> None:
        # Progress events are the bulk of the stream and are already captured
        # in `results`; keeping only the small ones bounds memory on big runs.
        if event.get("type") != "progress" or len(self._history) < 2000:
            self._history.append(event)
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    def summary(self) -> dict:
        blocked = sum(1 for r in self.results if r.get("blocked"))
        ok = sum(
            1 for r in self.results
            if r.get("status") == 200 and not r.get("error") and not r.get("blocked")
        )
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "error": self.error,
            # `total` is what the run set out to do; `kept` is what it actually
            # produced. They differ whenever a run is cancelled part-way, so
            # the two must not share a name.
            "total": self.total,
            "done": self.done,
            "kept": len(self.results),
            "ok": ok,
            "blocked": blocked,
            "errors": len(self.results) - ok - blocked,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_id": self.run_id,
        }


class JobManager:
    """Registry of running and recently finished jobs."""

    def __init__(self, max_kept: int = 40) -> None:
        self._jobs: dict[str, Job] = {}
        self._max_kept = max_kept

    def create(self, kind: str, params: dict) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, params=params)
        self._jobs[job.id] = job
        self._evict()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        return [j.summary() for j in sorted(
            self._jobs.values(), key=lambda j: j.started_at, reverse=True
        )]

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status not in ("queued", "running"):
            return False
        if job.task is not None:
            job.task.cancel()
        return True

    def _evict(self) -> None:
        """Drop the oldest finished jobs; never touch one still running."""
        finished = sorted(
            (j for j in self._jobs.values() if j.finished_at is not None),
            key=lambda j: j.finished_at or 0,
        )
        while len(self._jobs) > self._max_kept and finished:
            self._jobs.pop(finished.pop(0).id, None)
