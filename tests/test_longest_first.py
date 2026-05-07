#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
--longest-first オプションのユニットテスト

同一優先度内で ESTIMATE_TIME 降順にジョブが取得されることを確認する。
"""

import sqlite3
import sys
import tempfile
import os
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "script"))
from job_scheduler import JobScheduler


def create_test_db(tmp_path: str) -> str:
    """テスト用DBを作成してパスを返す"""
    db_path = os.path.join(tmp_path, "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
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
            JOBSCHEDULER_KILL_REQUESTED TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE job_dependencies (
            job_id TEXT NOT NULL,
            depends_on TEXT NOT NULL,
            PRIMARY KEY (job_id, depends_on)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def insert_job(db_path: str, job_id: str, priority: int = 0, estimate_time: float = 0.0):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS, JOBSCHEDULER_PRIORITY, JOBSCHEDULER_ESTIMATE_TIME) VALUES (?, 'pending', ?, ?)",
        (job_id, priority, estimate_time)
    )
    conn.commit()
    conn.close()


class TestLongestFirst(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def _make_scheduler(self, db_path, longest_first=False, smart_scheduling=False):
        return JobScheduler(
            db_path=db_path,
            command="echo",
            max_runtime=86400,
            smart_scheduling=smart_scheduling,
            longest_first=longest_first,
        )

    def test_default_order_by_job_id(self):
        """デフォルト: 同一優先度ならJOB_ID昇順"""
        db = create_test_db(self.tmpdir)
        insert_job(db, "job_c", priority=0, estimate_time=3.0)
        insert_job(db, "job_a", priority=0, estimate_time=1.0)
        insert_job(db, "job_b", priority=0, estimate_time=2.0)

        s = self._make_scheduler(db, longest_first=False)
        job = s.get_pending_job(available_time=0)
        self.assertEqual(job["JOBSCHEDULER_JOB_ID"], "job_a")

    def test_longest_first_same_priority(self):
        """--longest-first: 同一優先度内で ESTIMATE_TIME 降順"""
        db = create_test_db(self.tmpdir)
        insert_job(db, "job_short", priority=0, estimate_time=0.5)
        insert_job(db, "job_long",  priority=0, estimate_time=3.0)
        insert_job(db, "job_mid",   priority=0, estimate_time=1.5)

        s = self._make_scheduler(db, longest_first=True)
        job = s.get_pending_job(available_time=0)
        self.assertEqual(job["JOBSCHEDULER_JOB_ID"], "job_long")

    def test_priority_takes_precedence_over_longest_first(self):
        """--longest-first 有効でも PRIORITY が上位ソートキー"""
        db = create_test_db(self.tmpdir)
        insert_job(db, "job_low_prio_long",  priority=0, estimate_time=10.0)
        insert_job(db, "job_high_prio_short", priority=5, estimate_time=0.1)

        s = self._make_scheduler(db, longest_first=True)
        job = s.get_pending_job(available_time=0)
        self.assertEqual(job["JOBSCHEDULER_JOB_ID"], "job_high_prio_short")

    def test_longest_first_within_same_priority(self):
        """--longest-first: 同一優先度グループ内のみ ESTIMATE_TIME で並び替え"""
        db = create_test_db(self.tmpdir)
        insert_job(db, "job_p5_short", priority=5, estimate_time=0.5)
        insert_job(db, "job_p5_long",  priority=5, estimate_time=4.0)
        insert_job(db, "job_p3_long",  priority=3, estimate_time=8.0)

        s = self._make_scheduler(db, longest_first=True)
        job = s.get_pending_job(available_time=0)
        # priority=5 が先、その中で estimate_time=4.0 の方が先
        self.assertEqual(job["JOBSCHEDULER_JOB_ID"], "job_p5_long")

    def test_longest_first_with_smart_scheduling(self):
        """--longest-first と --smart-scheduling の併用"""
        db = create_test_db(self.tmpdir)
        # available_time = 7200秒 (2時間)
        # 3時間のジョブはフィルタされる
        insert_job(db, "job_3h", priority=0, estimate_time=3.0)  # 3*3600=10800 > 7200 → 除外
        insert_job(db, "job_2h", priority=0, estimate_time=2.0)  # 2*3600=7200 <= 7200 → OK
        insert_job(db, "job_1h", priority=0, estimate_time=1.0)  # 1*3600=3600 <= 7200 → OK

        s = self._make_scheduler(db, longest_first=True, smart_scheduling=True)
        job = s.get_pending_job(available_time=7200)
        # 3時間ジョブは除外され、残りの中で最長の2時間ジョブが選ばれる
        self.assertEqual(job["JOBSCHEDULER_JOB_ID"], "job_2h")

    def test_default_no_max_runtime_disables_smart_scheduling(self):
        """--max-runtime 未指定時は --smart-scheduling=True でも無効化される"""
        db = create_test_db(self.tmpdir)
        s = JobScheduler(
            db_path=db,
            command="echo",
            smart_scheduling=True,  # 明示的にTrueでも、max_runtime無しなら無効化される
        )
        self.assertFalse(s.smart_scheduling)
        self.assertIsNone(s.max_runtime)

    def test_get_pending_job_with_none_available_time(self):
        """available_time=None なら ESTIMATE_TIME によるフィルタは走らない"""
        db = create_test_db(self.tmpdir)
        # 巨大な estimate_time を持つジョブでも、None なら通る
        insert_job(db, "job_huge", priority=0, estimate_time=1000.0)

        s = JobScheduler(
            db_path=db,
            command="echo",
            # max_runtime=None → smart_scheduling は自動で無効化
        )
        job = s.get_pending_job(available_time=None)
        self.assertIsNotNone(job)
        self.assertEqual(job["JOBSCHEDULER_JOB_ID"], "job_huge")


if __name__ == "__main__":
    unittest.main(verbosity=2)
