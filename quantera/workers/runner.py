"""Worker loop: claim jobs from the PostgreSQL queue and run them in this process.

Run: python -m quantera.workers   (several processes may run; SKIP LOCKED keeps them apart)
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from quantera.analysis import jobs, scan
from quantera.analysis.models import AnalysisJob
from quantera.construction import service as construction
from quantera.lake.client import LakeClient
from quantera.models_ml import service as models_ml
from quantera.research import backtests

log = logging.getLogger("quantera.worker")
Runner = Callable[[Callable[[], Session], LakeClient, uuid.UUID], None]
RUNNERS: dict[str, Runner] = {"SCAN": scan.run, "OPTIMIZE": construction.run,
                              "BACKTEST": backtests.run, "TRAIN": models_ml.train}


def run_one(sessions: Callable[[], Session], lake: LakeClient) -> bool:
    """Claim and run one job. Returns False when the queue is empty."""
    with sessions() as s:
        jobs.requeue_stalled(s)
        s.commit()
        job = jobs.claim(s, tuple(RUNNERS))
        if job is None:
            return False
        job_id, kind = job.id, job.kind
    log.info("running %s job %s", kind, job_id)
    status, error = None, None
    try:
        RUNNERS[kind](sessions, lake, job_id)
    except jobs.Cancelled:
        status = "CANCELLED"
    except Exception as exc:
        log.exception("job %s failed", job_id)
        status, error = "FAILED", f"{type(exc).__name__}: {exc}"[:1000]
    with sessions() as s:
        j = s.get(AnalysisJob, job_id)
        assert j is not None
        if status:
            j.status, j.error = status, error
        j.finished_at = jobs.now()
        j.heartbeat_at = jobs.now()
        if j.status in ("SUCCEEDED", "PARTIAL"):
            j.progress = 1.0
        s.commit()
    return True


def run_until_empty(sessions: Callable[[], Session], lake: LakeClient, limit: int = 100) -> int:
    n = 0
    while n < limit and run_one(sessions, lake):
        n += 1
    return n


def main() -> None:
    from quantera.core.config import get_settings
    from quantera.core.db import Database

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    db = Database(settings.database_url, settings.db_schema)
    lake = LakeClient(settings.lake_api_url, settings.lake_release)
    log.info("worker started")
    while True:
        if not run_one(db.sessions, lake):
            time.sleep(2)
