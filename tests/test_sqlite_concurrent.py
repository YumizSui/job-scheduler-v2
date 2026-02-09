#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLiteの複数プロセス同時アクセステスト

複数のプロセスが同時にSQLiteデータベースにアクセスして、
レースコンディションやデッドロックが発生しないかをテストする。
"""

import sqlite3
import multiprocessing
import time
import random
import os
import sys
from pathlib import Path


def setup_database(db_path: str) -> None:
    """テスト用データベースのセットアップ"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")  # WALモード有効化
    conn.execute("PRAGMA busy_timeout=30000")  # 30秒のタイムアウト

    # テーブル作成
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            JOBSCHEDULER_JOB_ID TEXT PRIMARY KEY,
            JOBSCHEDULER_STATUS TEXT NOT NULL,
            JOBSCHEDULER_PRIORITY INTEGER DEFAULT 0,
            JOBSCHEDULER_ESTIMATE_TIME REAL DEFAULT 0,
            JOBSCHEDULER_ELAPSED_TIME REAL,
            JOBSCHEDULER_CREATED_AT TEXT,
            param1 TEXT,
            param2 TEXT
        )
    """)

    # インデックス作成
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_status_priority
        ON jobs(JOBSCHEDULER_STATUS, JOBSCHEDULER_PRIORITY DESC)
    """)

    # テストデータ投入（100ジョブ）
    conn.execute("DELETE FROM jobs")  # クリーンアップ
    for i in range(100):
        conn.execute("""
            INSERT INTO jobs (
                JOBSCHEDULER_JOB_ID,
                JOBSCHEDULER_STATUS,
                JOBSCHEDULER_PRIORITY,
                JOBSCHEDULER_ESTIMATE_TIME,
                JOBSCHEDULER_CREATED_AT,
                param1,
                param2
            ) VALUES (?, 'pending', ?, ?, datetime('now'), ?, ?)
        """, (f"job_{i:04d}", random.randint(1, 10), random.uniform(0.01, 0.1), f"param1_{i}", f"param2_{i}"))

    conn.commit()
    conn.close()
    print(f"✓ Database setup complete: {db_path}")


def worker_process(worker_id: int, db_path: str, num_jobs: int) -> dict:
    """ワーカープロセス：ジョブを取得して実行するシミュレーション"""
    stats = {
        'worker_id': worker_id,
        'jobs_completed': 0,
        'lock_conflicts': 0,
        'errors': 0,
        'total_time': 0
    }

    start_time = time.time()

    for _ in range(num_jobs):
        try:
            # データベース接続
            conn = sqlite3.connect(db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")

            # トランザクション開始（BEGIN IMMEDIATEで書き込みロック取得）
            conn.execute("BEGIN IMMEDIATE")

            # ジョブ取得（優先度順）
            cursor = conn.execute("""
                SELECT JOBSCHEDULER_JOB_ID, param1, param2
                FROM jobs
                WHERE JOBSCHEDULER_STATUS = 'pending'
                ORDER BY JOBSCHEDULER_PRIORITY DESC, JOBSCHEDULER_JOB_ID
                LIMIT 1
            """)

            row = cursor.fetchone()

            if row is None:
                conn.rollback()
                conn.close()
                break  # もうジョブがない

            job_id, param1, param2 = row

            # ジョブをrunningに更新
            conn.execute("""
                UPDATE jobs
                SET JOBSCHEDULER_STATUS = 'running',
                    JOBSCHEDULER_STARTED_AT = datetime('now')
                WHERE JOBSCHEDULER_JOB_ID = ?
            """, (job_id,))

            conn.commit()
            conn.close()

            # ジョブ実行シミュレーション（短時間スリープ）
            execution_time = random.uniform(0.001, 0.01)  # 1-10ms
            time.sleep(execution_time)

            # ジョブ完了をマーク
            conn = sqlite3.connect(db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")

            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                UPDATE jobs
                SET JOBSCHEDULER_STATUS = 'done',
                    JOBSCHEDULER_ELAPSED_TIME = ?,
                    JOBSCHEDULER_FINISHED_AT = datetime('now')
                WHERE JOBSCHEDULER_JOB_ID = ?
            """, (execution_time, job_id))
            conn.commit()
            conn.close()

            stats['jobs_completed'] += 1

        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                stats['lock_conflicts'] += 1
            else:
                stats['errors'] += 1
            print(f"Worker {worker_id}: Error - {e}")
            time.sleep(0.01)  # 短時間待機してリトライ

        except Exception as e:
            stats['errors'] += 1
            print(f"Worker {worker_id}: Unexpected error - {e}")

    stats['total_time'] = time.time() - start_time
    return stats


def verify_database(db_path: str) -> dict:
    """データベースの整合性チェック"""
    conn = sqlite3.connect(db_path)

    # 各ステータスの件数を取得
    cursor = conn.execute("""
        SELECT JOBSCHEDULER_STATUS, COUNT(*)
        FROM jobs
        GROUP BY JOBSCHEDULER_STATUS
    """)

    results = dict(cursor.fetchall())

    # 合計ジョブ数
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # runningステータスが残っていないかチェック（すべて完了していれば0）
    running = results.get('running', 0)

    conn.close()

    return {
        'total': total,
        'pending': results.get('pending', 0),
        'running': running,
        'done': results.get('done', 0),
        'error': results.get('error', 0),
        'is_valid': (running == 0)  # runningが0なら正常
    }


def run_concurrent_test(db_path: str, num_workers: int, jobs_per_worker: int) -> None:
    """並列アクセステストの実行"""
    print(f"\n{'='*60}")
    print(f"Running concurrent access test")
    print(f"Workers: {num_workers}, Jobs per worker: {jobs_per_worker}")
    print(f"{'='*60}\n")

    # データベースセットアップ
    setup_database(db_path)

    # ワーカープロセス起動
    start_time = time.time()

    with multiprocessing.Pool(processes=num_workers) as pool:
        results = pool.starmap(
            worker_process,
            [(i, db_path, jobs_per_worker) for i in range(num_workers)]
        )

    total_time = time.time() - start_time

    # 結果集計
    print(f"\n{'='*60}")
    print("Test Results")
    print(f"{'='*60}")

    total_completed = sum(r['jobs_completed'] for r in results)
    total_lock_conflicts = sum(r['lock_conflicts'] for r in results)
    total_errors = sum(r['errors'] for r in results)

    print(f"\nTotal execution time: {total_time:.2f}s")
    print(f"Jobs completed: {total_completed}")
    print(f"Lock conflicts: {total_lock_conflicts}")
    print(f"Errors: {total_errors}")
    print(f"Throughput: {total_completed/total_time:.2f} jobs/sec")

    print(f"\nPer-worker stats:")
    for r in results:
        print(f"  Worker {r['worker_id']}: "
              f"{r['jobs_completed']} jobs, "
              f"{r['lock_conflicts']} conflicts, "
              f"{r['errors']} errors, "
              f"{r['total_time']:.2f}s")

    # データベース整合性チェック
    print(f"\n{'='*60}")
    print("Database Integrity Check")
    print(f"{'='*60}")

    verification = verify_database(db_path)
    print(f"\nTotal jobs: {verification['total']}")
    print(f"  Pending: {verification['pending']}")
    print(f"  Running: {verification['running']}")
    print(f"  Done: {verification['done']}")
    print(f"  Error: {verification['error']}")

    if verification['is_valid']:
        print(f"\n✓ Database integrity check PASSED")
    else:
        print(f"\n✗ Database integrity check FAILED - {verification['running']} jobs stuck in 'running' state")

    print(f"\n{'='*60}\n")

    return verification['is_valid']


def main():
    """メインテスト実行"""
    db_path = "test_concurrent.db"

    # テスト前にクリーンアップ
    for ext in ['', '-shm', '-wal']:
        path = db_path + ext
        if os.path.exists(path):
            os.remove(path)

    # テストケース1: 中程度の並列度
    print("\n" + "="*60)
    print("TEST CASE 1: Moderate concurrency")
    print("="*60)
    success1 = run_concurrent_test(db_path, num_workers=4, jobs_per_worker=30)

    # クリーンアップ
    for ext in ['', '-shm', '-wal']:
        path = db_path + ext
        if os.path.exists(path):
            os.remove(path)

    # テストケース2: 高並列度
    print("\n" + "="*60)
    print("TEST CASE 2: High concurrency")
    print("="*60)
    success2 = run_concurrent_test(db_path, num_workers=10, jobs_per_worker=15)

    # クリーンアップ
    for ext in ['', '-shm', '-wal']:
        path = db_path + ext
        if os.path.exists(path):
            os.remove(path)

    # テストケース3: 非常に高い並列度（ストレステスト）
    print("\n" + "="*60)
    print("TEST CASE 3: Stress test (very high concurrency)")
    print("="*60)
    success3 = run_concurrent_test(db_path, num_workers=20, jobs_per_worker=10)

    # 最終結果
    print("\n" + "="*60)
    print("FINAL RESULTS")
    print("="*60)
    print(f"Test Case 1 (Moderate): {'PASS' if success1 else 'FAIL'}")
    print(f"Test Case 2 (High): {'PASS' if success2 else 'FAIL'}")
    print(f"Test Case 3 (Stress): {'PASS' if success3 else 'FAIL'}")

    all_passed = success1 and success2 and success3

    if all_passed:
        print("\n✓ All tests PASSED - SQLite is suitable for multi-node job scheduling")
        return 0
    else:
        print("\n✗ Some tests FAILED - Consider alternative solutions")
        return 1


if __name__ == "__main__":
    sys.exit(main())
