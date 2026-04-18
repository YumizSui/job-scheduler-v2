#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JobScheduler の optimistic locking 並列テスト

複数プロセスが同時に get_pending_job / mark_job_done を呼んだ場合の正当性を検証する：
  - 二重 claim が起きないこと
  - 全ジョブが漏れなく claim されること
  - claim → 完了の全ライフサイクルが正しく動くこと
  - ワーカー数 > ジョブ数 でも正しく動くこと

Usage:
    uv run --with pytest python -m pytest tests/test_concurrent_claim.py -v
    python tests/test_concurrent_claim.py
"""

import sqlite3
import multiprocessing
import tempfile
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "script"))
from job_scheduler import JobScheduler


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _create_test_db(db_path: str, num_jobs: int, num_priorities: int = 5) -> None:
    """num_jobs 件の pending ジョブを持つ DB を作成する。"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("""
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
            JOBSCHEDULER_KILL_REQUESTED TEXT,
            param TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE job_dependencies (
            job_id TEXT NOT NULL,
            depends_on TEXT NOT NULL,
            PRIMARY KEY (job_id, depends_on)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_status_priority
        ON jobs(JOBSCHEDULER_STATUS, JOBSCHEDULER_PRIORITY DESC)
    """)
    for i in range(num_jobs):
        conn.execute(
            "INSERT INTO jobs "
            "(JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS, JOBSCHEDULER_PRIORITY, param) "
            "VALUES (?, 'pending', ?, ?)",
            (f"job_{i:06d}", i % num_priorities, f"value_{i}"),
        )
    conn.commit()
    conn.close()


def _db_status_counts(db_path: str) -> dict:
    """DB のジョブ数をステータス別に返す。"""
    conn = sqlite3.connect(db_path, timeout=30)
    rows = conn.execute(
        "SELECT JOBSCHEDULER_STATUS, COUNT(*) FROM jobs GROUP BY JOBSCHEDULER_STATUS"
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# worker functions (top-level for pickling)
# ---------------------------------------------------------------------------

def _worker_claim_only(args):
    """get_pending_job だけを繰り返し呼んで claim した job_id を返す。"""
    db_path, worker_idx = args
    scheduler = JobScheduler(
        db_path=db_path, command="echo", smart_scheduling=False,
    )
    claimed = []
    while True:
        job = scheduler.get_pending_job(available_time=0)
        if job is None:
            break
        claimed.append(job["JOBSCHEDULER_JOB_ID"])
    return {"worker": worker_idx, "claimed": claimed}


def _worker_claim_and_complete(args):
    """claim → sleep → mark_job_done のフルサイクルを繰り返す。"""
    db_path, worker_idx, sleep_time = args
    scheduler = JobScheduler(
        db_path=db_path, command="echo", smart_scheduling=False,
    )
    claimed = []
    while True:
        job = scheduler.get_pending_job(available_time=0)
        if job is None:
            break
        job_id = job["JOBSCHEDULER_JOB_ID"]
        claimed.append(job_id)
        if sleep_time > 0:
            time.sleep(sleep_time)
        scheduler.mark_job_done(job_id, "done", sleep_time)
    return {"worker": worker_idx, "claimed": claimed}


# ---------------------------------------------------------------------------
# helpers for assertions
# ---------------------------------------------------------------------------

def _collect_and_verify(test_case, results, expected_jobs):
    """results (list of worker dicts) から claimed を集めて二重 claim・漏れを検証する。"""
    all_claimed = []
    for r in results:
        all_claimed.extend(r["claimed"])

    unique = set(all_claimed)
    duplicates = len(all_claimed) - len(unique)

    test_case.assertEqual(
        duplicates, 0,
        f"Duplicate claims detected! {duplicates} jobs were claimed by multiple workers",
    )
    test_case.assertEqual(
        len(unique), expected_jobs,
        f"Not all jobs claimed: {len(unique)}/{expected_jobs}",
    )

    # distribution info (printed by pytest -v -s)
    per_worker = sorted(len(r["claimed"]) for r in results)
    return per_worker


# ===========================================================================
# Test cases
# ===========================================================================


class TestConcurrentClaim(unittest.TestCase):
    """get_pending_job の並列 claim 正当性テスト（mark_job_done は呼ばない）。

    ジョブは running のまま残る。目的は「二重 claim がないこと」と
    「全ジョブが漏れなく claim されること」の検証。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def _run(self, num_jobs, num_workers):
        db = os.path.join(self.tmpdir, f"claim_{num_workers}w_{num_jobs}j.db")
        _create_test_db(db, num_jobs)

        t0 = time.time()
        with multiprocessing.Pool(num_workers) as pool:
            results = pool.map(
                _worker_claim_only, [(db, i) for i in range(num_workers)]
            )
        elapsed = time.time() - t0

        dist = _collect_and_verify(self, results, num_jobs)
        print(f"\n  {num_workers}w × {num_jobs}j: {elapsed:.2f}s  dist={dist}")

    def test_10w_200j(self):
        """10 workers, 200 jobs"""
        self._run(200, 10)

    def test_20w_500j(self):
        """20 workers, 500 jobs"""
        self._run(500, 20)

    def test_40w_500j(self):
        """40 workers, 500 jobs"""
        self._run(500, 40)


class TestConcurrentLifecycle(unittest.TestCase):
    """claim → execute → mark_done のフルサイクル並列テスト。

    全ジョブが最終的に done になることを検証する。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def _run(self, num_jobs, num_workers, sleep_per_job=0.01):
        db = os.path.join(self.tmpdir, f"life_{num_workers}w_{num_jobs}j.db")
        _create_test_db(db, num_jobs)

        t0 = time.time()
        with multiprocessing.Pool(num_workers) as pool:
            results = pool.map(
                _worker_claim_and_complete,
                [(db, i, sleep_per_job) for i in range(num_workers)],
            )
        elapsed = time.time() - t0

        dist = _collect_and_verify(self, results, num_jobs)
        print(f"\n  {num_workers}w × {num_jobs}j (sleep={sleep_per_job}s): {elapsed:.2f}s  dist={dist}")

        # DB 上で全ジョブが done になっているか
        stats = _db_status_counts(db)
        self.assertEqual(stats.get("done", 0), num_jobs, f"Not all done: {stats}")
        self.assertEqual(stats.get("pending", 0), 0, f"Pending remain: {stats}")
        self.assertEqual(stats.get("running", 0), 0, f"Running remain: {stats}")

    def test_10w_100j(self):
        """10 workers, 100 jobs, 10ms/job"""
        self._run(100, 10, sleep_per_job=0.01)

    def test_20w_200j(self):
        """20 workers, 200 jobs, 10ms/job"""
        self._run(200, 20, sleep_per_job=0.01)

    def test_40w_500j(self):
        """40 workers, 500 jobs, 5ms/job"""
        self._run(500, 40, sleep_per_job=0.005)


class TestHighContention(unittest.TestCase):
    """極端な競合シナリオのテスト。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_more_workers_than_jobs(self):
        """40 workers が 10 jobs を奪い合う: 全 job が正確に 1 回だけ claim されること"""
        db = os.path.join(self.tmpdir, "few_jobs.db")
        _create_test_db(db, 10)

        with multiprocessing.Pool(40) as pool:
            results = pool.map(
                _worker_claim_only, [(db, i) for i in range(40)]
            )

        _collect_and_verify(self, results, 10)

    def test_zero_delay_lifecycle(self):
        """sleep=0 (最大競合) のフルサイクル: 全 job が done になること"""
        db = os.path.join(self.tmpdir, "zero_delay.db")
        num_jobs = 200
        num_workers = 20
        _create_test_db(db, num_jobs)

        t0 = time.time()
        with multiprocessing.Pool(num_workers) as pool:
            results = pool.map(
                _worker_claim_and_complete,
                [(db, i, 0) for i in range(num_workers)],
            )
        elapsed = time.time() - t0

        dist = _collect_and_verify(self, results, num_jobs)
        print(f"\n  {num_workers}w × {num_jobs}j (sleep=0): {elapsed:.2f}s  dist={dist}")

        stats = _db_status_counts(db)
        self.assertEqual(stats.get("done", 0), num_jobs)
        self.assertEqual(stats.get("pending", 0), 0)
        self.assertEqual(stats.get("running", 0), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
