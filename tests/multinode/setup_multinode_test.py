#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
実際の複数ノードテスト用のデータベースセットアップ
"""

import sqlite3
import sys

def setup_test_db(db_path: str, num_jobs: int = 200):
    """テスト用データベースのセットアップ"""
    print(f"Setting up test database: {db_path}")
    print(f"Number of jobs: {num_jobs}")

    conn = sqlite3.connect(db_path)

    # WALモード設定
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    # テーブル作成
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            JOBSCHEDULER_JOB_ID TEXT PRIMARY KEY,
            JOBSCHEDULER_STATUS TEXT NOT NULL,
            JOBSCHEDULER_PRIORITY INTEGER DEFAULT 0,
            JOBSCHEDULER_ESTIMATE_TIME REAL DEFAULT 0,
            JOBSCHEDULER_ELAPSED_TIME REAL,
            JOBSCHEDULER_CREATED_AT TEXT,
            param1 TEXT
        )
    """)

    # インデックス作成
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_status_priority
        ON jobs(JOBSCHEDULER_STATUS, JOBSCHEDULER_PRIORITY DESC)
    """)

    # 既存データクリア
    conn.execute("DELETE FROM jobs")

    # テストジョブ投入
    for i in range(num_jobs):
        conn.execute("""
            INSERT INTO jobs (
                JOBSCHEDULER_JOB_ID,
                JOBSCHEDULER_STATUS,
                JOBSCHEDULER_PRIORITY,
                JOBSCHEDULER_ESTIMATE_TIME,
                JOBSCHEDULER_CREATED_AT,
                param1
            ) VALUES (?, 'pending', ?, ?, datetime('now'), ?)
        """, (
            f"job_{i:04d}",
            i % 10,  # 優先度0-9
            0.01,    # 推定時間0.01時間（36秒）
            f"param_{i}"
        ))

    conn.commit()

    # 確認
    count = conn.execute("SELECT COUNT(*) FROM jobs WHERE JOBSCHEDULER_STATUS='pending'").fetchone()[0]
    print(f"✓ Created {count} pending jobs")

    conn.close()


def verify_test_db(db_path: str):
    """テスト結果の検証"""
    print(f"\nVerifying test database: {db_path}")

    conn = sqlite3.connect(db_path)

    # 各ステータスの件数
    cursor = conn.execute("""
        SELECT JOBSCHEDULER_STATUS, COUNT(*)
        FROM jobs
        GROUP BY JOBSCHEDULER_STATUS
    """)

    results = dict(cursor.fetchall())
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    print(f"\nDatabase status:")
    print(f"  Total jobs: {total}")
    print(f"  Pending: {results.get('pending', 0)}")
    print(f"  Running: {results.get('running', 0)}")
    print(f"  Done: {results.get('done', 0)}")
    print(f"  Error: {results.get('error', 0)}")

    running = results.get('running', 0)
    done = results.get('done', 0)

    if running == 0 and done == total:
        print(f"\n✓ SUCCESS - All jobs completed")
        return True
    elif running > 0:
        print(f"\n⚠ WARNING - {running} jobs stuck in 'running' state")

        # 実行中のジョブを表示
        cursor = conn.execute("""
            SELECT JOBSCHEDULER_JOB_ID
            FROM jobs
            WHERE JOBSCHEDULER_STATUS = 'running'
            LIMIT 10
        """)
        stuck_jobs = cursor.fetchall()
        print(f"\nStuck jobs (showing first 10):")
        for job in stuck_jobs:
            print(f"  - {job[0]}")
        return False
    else:
        print(f"\n⚠ WARNING - Only {done}/{total} jobs completed")
        return False

    conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 setup_multinode_test.py <command> [args]")
        print("Commands:")
        print("  setup <db_path> [num_jobs]  - Setup test database")
        print("  verify <db_path>            - Verify test results")
        sys.exit(1)

    command = sys.argv[1]

    if command == "setup":
        db_path = sys.argv[2]
        num_jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 200
        setup_test_db(db_path, num_jobs)

    elif command == "verify":
        db_path = sys.argv[2]
        success = verify_test_db(db_path)
        sys.exit(0 if success else 1)

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
