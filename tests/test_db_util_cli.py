#!/usr/bin/env python3
"""Tests for db_util CLI extensions: show, list, stats --by, injection guards."""

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "script"))
from db_util import JobDatabase


@pytest.fixture
def db_path(tmp_path):
    """Create a test DB with 5 jobs (2 error, 2 done, 1 pending)."""
    p = str(tmp_path / "test.db")
    with JobDatabase(p) as db:
        db.create_schema(user_columns=["param1", "param2"])
        jobs = [
            ("job_00000000", "error", 5, "CUDA out of memory on device 0", "worker-a"),
            ("job_00000001", "error", 3, "CUDA out of memory on device 1", "worker-b"),
            ("job_00000002", "error", 2, "Segmentation fault", "worker-a"),
            ("job_00000003", "done", 8, None, "worker-b"),
            ("job_00000004", "pending", 1, None, None),
        ]
        for job_id, status, priority, error_msg, worker in jobs:
            db.conn.execute(
                "INSERT INTO jobs (JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS, "
                "JOBSCHEDULER_PRIORITY, JOBSCHEDULER_ERROR_MESSAGE, JOBSCHEDULER_WORKER_ID, "
                "param1, param2) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, status, priority, error_msg, worker, "v1", "v2"),
            )
        db.conn.commit()
    return p


# --- get_job / show ---

def test_get_job_found(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        job = db.get_job("job_00000000")
    assert job is not None
    assert job["JOBSCHEDULER_STATUS"] == "error"
    assert "CUDA out of memory" in (job["JOBSCHEDULER_ERROR_MESSAGE"] or "")


def test_get_job_not_found(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        job = db.get_job("nonexistent")
    assert job is None


def test_get_dependencies_empty(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        dep_on, dep_by = db.get_dependencies("job_00000000")
    assert dep_on == []
    assert dep_by == []


# --- list_jobs: filter correctness ---

def test_list_jobs_no_filter(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        headers, rows = db.list_jobs()
    assert len(rows) == 5
    assert "JOBSCHEDULER_JOB_ID" in headers


def test_list_jobs_status_filter(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        _, rows = db.list_jobs(status="error")
    assert len(rows) == 3


def test_list_jobs_worker_filter(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        _, rows = db.list_jobs(worker="worker-a")
    assert len(rows) == 2


def test_list_jobs_grep_error_matches(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        _, rows = db.list_jobs(grep_error="CUDA.*memory")
    assert len(rows) == 2


def test_list_jobs_grep_error_no_match(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        _, rows = db.list_jobs(grep_error="nonexistent pattern xyz")
    assert len(rows) == 0


def test_list_jobs_invalid_regex_returns_zero(db_path):
    # Invalid regex should not raise, just return 0 results
    with JobDatabase(db_path, read_only=True) as db:
        _, rows = db.list_jobs(grep_error="(unclosed")
    assert len(rows) == 0


def test_list_jobs_priority_range(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        _, rows = db.list_jobs(priority_min=3, priority_max=5)
    assert len(rows) == 2


def test_list_jobs_limit(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        _, rows = db.list_jobs(limit=2)
    assert len(rows) == 2


def test_list_jobs_columns_subset(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        headers, rows = db.list_jobs(columns=["JOBSCHEDULER_JOB_ID", "JOBSCHEDULER_STATUS"])
    assert headers == ["JOBSCHEDULER_JOB_ID", "JOBSCHEDULER_STATUS"]
    assert len(rows[0]) == 2


# --- Injection guards ---

def test_invalid_sort_raises(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        with pytest.raises(ValueError, match="Invalid sort"):
            db.list_jobs(sort="JOB_ID; DROP TABLE jobs")


def test_invalid_columns_raises(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        with pytest.raises(ValueError, match="Invalid columns"):
            db.list_jobs(columns=["JOBSCHEDULER_JOB_ID", "injected; DROP TABLE jobs"])


# --- get_stats_by ---

def test_stats_by_status(db_path):
    with JobDatabase(db_path) as db:
        rows = db.get_stats_by("status")
    total_sum = sum(r[1] for r in rows)
    assert total_sum == 5
    status_map = {r[0]: r[1] for r in rows}
    assert status_map.get("error") == 3
    assert status_map.get("done") == 1
    assert status_map.get("pending") == 1


def test_stats_by_worker(db_path):
    with JobDatabase(db_path) as db:
        rows = db.get_stats_by("worker")
    total_sum = sum(r[1] for r in rows)
    assert total_sum == 5
    worker_map = {r[0]: r[1] for r in rows}
    assert worker_map.get("worker-a") == 2
    assert worker_map.get("worker-b") == 2


def test_stats_by_invalid_raises():
    with pytest.raises(ValueError, match="dimension must be one of"):
        with JobDatabase(":memory:") as db:
            db.create_schema()
            db.get_stats_by("bogus")


# --- reset_jobs ---

def test_reset_jobs_by_ids(db_path):
    with JobDatabase(db_path) as db:
        count, missing = db.reset_jobs(["job_00000000"], None, "pending")
    assert count == 1
    assert missing == []
    with JobDatabase(db_path, read_only=True) as db:
        job = db.get_job("job_00000000")
    assert job["JOBSCHEDULER_STATUS"] == "pending"
    assert job["JOBSCHEDULER_ERROR_MESSAGE"] is None


def test_reset_jobs_missing_id(db_path):
    with JobDatabase(db_path) as db:
        count, missing = db.reset_jobs(["nonexistent"], None, "pending")
    assert "nonexistent" in missing


def test_reset_jobs_by_status(db_path):
    with JobDatabase(db_path) as db:
        count, missing = db.reset_jobs(None, "error", "pending")
    assert count == 3
    assert missing == []


# --- read_only connection ---

def test_readonly_cannot_write(db_path):
    with JobDatabase(db_path, read_only=True) as db:
        with pytest.raises(Exception):
            db.conn.execute("UPDATE jobs SET JOBSCHEDULER_STATUS = 'done'")


# --- reconcile_running_jobs ---

def _set_status(db_path, job_id, status):
    with JobDatabase(db_path) as db:
        db.conn.execute(
            "UPDATE jobs SET JOBSCHEDULER_STATUS = ? WHERE JOBSCHEDULER_JOB_ID = ?",
            (status, job_id),
        )
        db.conn.commit()


def _make_heartbeat(db_path, job_id, age_seconds=0.0):
    """Create a heartbeat file whose mtime is `age_seconds` old."""
    hb_dir = Path(db_path + ".heartbeat")
    hb_dir.mkdir(parents=True, exist_ok=True)
    f = hb_dir / job_id
    f.touch()
    if age_seconds > 0:
        mtime = time.time() - age_seconds
        os.utime(f, (mtime, mtime))
    return f


def test_reconcile_restores_fresh_heartbeat_jobs(db_path):
    # job_00000004 is pending, give it a fresh heartbeat; job_00000000 is error, give it a stale one.
    _make_heartbeat(db_path, "job_00000004", age_seconds=0)
    _make_heartbeat(db_path, "job_00000000", age_seconds=600)  # way past threshold
    with JobDatabase(db_path) as db:
        reconciled = db.reconcile_running_jobs(fresh_threshold=120)
    assert reconciled == 1
    with JobDatabase(db_path, read_only=True) as db:
        assert db.get_job("job_00000004")["JOBSCHEDULER_STATUS"] == "running"
        assert db.get_job("job_00000000")["JOBSCHEDULER_STATUS"] == "error"


def test_reconcile_no_op_when_status_already_running(db_path):
    _set_status(db_path, "job_00000004", "running")
    _make_heartbeat(db_path, "job_00000004", age_seconds=0)
    with JobDatabase(db_path) as db:
        reconciled = db.reconcile_running_jobs(fresh_threshold=120)
    assert reconciled == 0


def test_reconcile_no_op_when_heartbeat_dir_missing(tmp_path):
    p = str(tmp_path / "empty.db")
    with JobDatabase(p) as db:
        db.create_schema()
        db.conn.execute(
            "INSERT INTO jobs (JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS) VALUES (?, ?)",
            ("job_x", "pending"),
        )
        db.conn.commit()
        reconciled = db.reconcile_running_jobs(fresh_threshold=120)
    assert reconciled == 0


def test_reset_then_auto_reconcile_via_cli(db_path):
    # Set up: fresh heartbeat for job_00000000 (currently error), stale for job_00000002.
    _make_heartbeat(db_path, "job_00000000", age_seconds=0)
    _make_heartbeat(db_path, "job_00000002", age_seconds=600)

    script = Path(__file__).parent.parent / "script" / "db_util.py"
    result = subprocess.run(
        [sys.executable, str(script), "reset", db_path, "--set-status", "error"],
        capture_output=True,
        text=True,
        check=True,
    )
    # Reset flipped everything to error, then reconcile should restore fresh-heartbeat job(s).
    assert "Reconciled" in result.stdout or "Restored" in result.stdout

    with JobDatabase(db_path, read_only=True) as db:
        assert db.get_job("job_00000000")["JOBSCHEDULER_STATUS"] == "running"
        # Stale heartbeat → stays error after reset
        assert db.get_job("job_00000002")["JOBSCHEDULER_STATUS"] == "error"


def test_recover_subcommand_both_directions(db_path):
    # Mismatch case: pending job with fresh heartbeat → should become running
    _make_heartbeat(db_path, "job_00000004", age_seconds=0)
    # Stuck case: running job with stale DB heartbeat (no heartbeat file) → should become pending
    _set_status(db_path, "job_00000003", "running")
    with JobDatabase(db_path) as db:
        db.conn.execute(
            "UPDATE jobs SET JOBSCHEDULER_HEARTBEAT = datetime('now', '-1 hour') "
            "WHERE JOBSCHEDULER_JOB_ID = ?",
            ("job_00000003",),
        )
        db.conn.commit()

    script = Path(__file__).parent.parent / "script" / "db_util.py"
    subprocess.run(
        [sys.executable, str(script), "recover", db_path, "--direction", "both"],
        capture_output=True,
        text=True,
        check=True,
    )

    with JobDatabase(db_path, read_only=True) as db:
        assert db.get_job("job_00000004")["JOBSCHEDULER_STATUS"] == "running"
        assert db.get_job("job_00000003")["JOBSCHEDULER_STATUS"] == "pending"
