#!/usr/bin/env python3
"""Tests for early-exit behavior when only error/missing-dep blocked jobs remain.

Without the fix, the scheduler spin-loops every 1-3s with a "Retrying..." warning
until --max-runtime expires. The fix detects this state via
count_unrunnable_pending_jobs() and breaks instead.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "script"))
from job_scheduler import JobScheduler  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
SCHEDULER = REPO_ROOT / "script" / "job_scheduler.py"


def _tmp_db_path() -> str:
    base = Path.home() / "tmpdir"
    base.mkdir(exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".sqlite", dir=str(base))
    os.close(fd)
    os.unlink(path)
    return path


def _create_schema(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE jobs (
            JOBSCHEDULER_JOB_ID TEXT PRIMARY KEY,
            JOBSCHEDULER_STATUS TEXT NOT NULL DEFAULT 'pending',
            JOBSCHEDULER_PRIORITY INTEGER DEFAULT 0,
            JOBSCHEDULER_ESTIMATE_TIME REAL DEFAULT 0,
            JOBSCHEDULER_ELAPSED_TIME REAL,
            JOBSCHEDULER_CREATED_AT TEXT DEFAULT (datetime('now')),
            JOBSCHEDULER_STARTED_AT TEXT,
            JOBSCHEDULER_FINISHED_AT TEXT,
            JOBSCHEDULER_ERROR_MESSAGE TEXT,
            JOBSCHEDULER_DEPENDS_ON TEXT,
            JOBSCHEDULER_HEARTBEAT TEXT,
            JOBSCHEDULER_WORKER_ID TEXT,
            JOBSCHEDULER_KILL_REQUESTED TEXT
        );
        CREATE TABLE job_dependencies (
            job_id TEXT NOT NULL,
            depends_on TEXT NOT NULL,
            PRIMARY KEY (job_id, depends_on)
        );
    """)
    return conn


def _status_counts(db_path: str) -> dict:
    conn = sqlite3.connect(db_path, timeout=10)
    rows = conn.execute(
        "SELECT JOBSCHEDULER_STATUS, COUNT(*) FROM jobs GROUP BY JOBSCHEDULER_STATUS"
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def _run_scheduler(db_path: str, max_runtime: int = 60, timeout: int = 20) -> tuple[int, str]:
    """Run job_scheduler.py with a no-op command and return (returncode, combined_output)."""
    proc = subprocess.run(
        [sys.executable, str(SCHEDULER), db_path, "true",
         "--max-runtime", str(max_runtime),
         "--dep-wait-interval", "2"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout + proc.stderr


class TestUnrunnableHelper(unittest.TestCase):
    """Direct unit test of count_unrunnable_pending_jobs."""

    def setUp(self):
        self.db = _tmp_db_path()
        conn = _create_schema(self.db)
        conn.executescript("""
            INSERT INTO jobs (JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS) VALUES
                ('A_err', 'error'),
                ('A_done', 'done'),
                ('A_run', 'running'),
                ('B_err', 'pending'),
                ('B_missing', 'pending'),
                ('B_done', 'pending'),
                ('B_run', 'pending');
            INSERT INTO job_dependencies VALUES
                ('B_err', 'A_err'),
                ('B_missing', 'NOPE'),
                ('B_done', 'A_done'),
                ('B_run', 'A_run');
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        Path(self.db).unlink(missing_ok=True)

    def test_counts_only_error_or_missing_dep_pending(self):
        s = JobScheduler(db_path=self.db, command="true")
        # B_err (error dep) + B_missing (missing dep) = 2.
        # B_done's dep is satisfied, B_run's dep is still in-flight — both excluded.
        self.assertEqual(s.count_unrunnable_pending_jobs(), 2)


class TestSchedulerExitsOnUnrunnable(unittest.TestCase):
    """Integration: scheduler must break, not spin, when only unrunnable pending remain."""

    def setUp(self):
        self.db = _tmp_db_path()

    def tearDown(self):
        Path(self.db).unlink(missing_ok=True)

    def test_error_dep_causes_exit(self):
        conn = _create_schema(self.db)
        conn.executescript("""
            INSERT INTO jobs (JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS) VALUES
                ('A', 'error'),
                ('B', 'pending');
            INSERT INTO job_dependencies VALUES ('B', 'A');
        """)
        conn.commit()
        conn.close()

        t0 = time.monotonic()
        rc, output = _run_scheduler(self.db, max_runtime=60, timeout=15)
        elapsed = time.monotonic() - t0

        self.assertEqual(rc, 0, f"scheduler exited non-zero. output:\n{output}")
        self.assertLess(elapsed, 10, f"scheduler took {elapsed:.1f}s — should exit promptly. output:\n{output}")
        self.assertIn("blocked by error/missing dependencies", output)
        # B must remain pending (never ran).
        self.assertEqual(_status_counts(self.db).get("pending", 0), 1)

    def test_missing_dep_causes_exit(self):
        conn = _create_schema(self.db)
        conn.executescript("""
            INSERT INTO jobs (JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS) VALUES
                ('B', 'pending');
            INSERT INTO job_dependencies VALUES ('B', 'NOPE');
        """)
        conn.commit()
        conn.close()

        t0 = time.monotonic()
        rc, output = _run_scheduler(self.db, max_runtime=60, timeout=15)
        elapsed = time.monotonic() - t0

        self.assertEqual(rc, 0, f"scheduler exited non-zero. output:\n{output}")
        self.assertLess(elapsed, 10, f"scheduler took {elapsed:.1f}s. output:\n{output}")
        self.assertIn("blocked by error/missing dependencies", output)

    def test_mixed_pending_runs_ready_then_exits(self):
        """A ready job alongside an unrunnable one: scheduler runs the ready one, then exits."""
        conn = _create_schema(self.db)
        conn.executescript("""
            INSERT INTO jobs (JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS) VALUES
                ('A_err', 'error'),
                ('B_blocked', 'pending'),
                ('C_ready',   'pending');
            INSERT INTO job_dependencies VALUES ('B_blocked', 'A_err');
        """)
        conn.commit()
        conn.close()

        t0 = time.monotonic()
        rc, output = _run_scheduler(self.db, max_runtime=60, timeout=15)
        elapsed = time.monotonic() - t0

        self.assertEqual(rc, 0, f"output:\n{output}")
        self.assertLess(elapsed, 10, f"took {elapsed:.1f}s. output:\n{output}")
        counts = _status_counts(self.db)
        self.assertEqual(counts.get("done", 0), 1, f"C_ready should be done. counts={counts}")
        self.assertEqual(counts.get("pending", 0), 1, f"B_blocked should remain pending. counts={counts}")


if __name__ == "__main__":
    unittest.main()
