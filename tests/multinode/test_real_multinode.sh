#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=0:10:00
#$ -N sqlite_multinode_test

# 実際のTSUBAME環境で複数ノードからSQLiteアクセスをテストするスクリプト

source $HOME/.bashrc

# テスト設定
WORK_DIR="/gs/bs/tga-furui/workspace/dev/job-runner/job-runner-v2"
DB_FILE="$WORK_DIR/test_real_multinode.db"
LOG_DIR="$WORK_DIR/test_logs"
WORKER_ID="${JOB_ID:-local}_${SGE_TASK_ID:-0}_$$"

mkdir -p "$LOG_DIR"

echo "=========================================="
echo "Worker: $WORKER_ID"
echo "Hostname: $(hostname)"
echo "Job ID: ${JOB_ID:-N/A}"
echo "Task ID: ${SGE_TASK_ID:-N/A}"
echo "Start time: $(date)"
echo "=========================================="

# ワーカースクリプト
python3 << 'EOF'
import sqlite3
import sys
import time
import random
import os

worker_id = os.environ.get('WORKER_ID', 'unknown')
db_path = os.environ.get('DB_FILE')
max_jobs = int(os.environ.get('MAX_JOBS', '20'))

completed = 0
conflicts = 0
errors = 0

print(f"Worker {worker_id} starting...")
print(f"Database: {db_path}")
print(f"Max jobs: {max_jobs}")

for attempt in range(max_jobs):
    try:
        # データベース接続
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")

        # トランザクション開始
        conn.execute("BEGIN IMMEDIATE")

        # ジョブ取得
        cursor = conn.execute("""
            SELECT JOBSCHEDULER_JOB_ID, param1
            FROM jobs
            WHERE JOBSCHEDULER_STATUS = 'pending'
            ORDER BY JOBSCHEDULER_PRIORITY DESC
            LIMIT 1
        """)

        row = cursor.fetchone()

        if row is None:
            conn.rollback()
            conn.close()
            print(f"No more jobs available (attempt {attempt+1})")
            break

        job_id, param1 = row

        # ジョブをrunningに更新
        conn.execute("""
            UPDATE jobs
            SET JOBSCHEDULER_STATUS = 'running'
            WHERE JOBSCHEDULER_JOB_ID = ?
        """, (job_id,))

        conn.commit()
        conn.close()

        print(f"Processing job: {job_id}")

        # ジョブ実行シミュレーション
        execution_time = random.uniform(0.01, 0.1)
        time.sleep(execution_time)

        # 完了マーク
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            UPDATE jobs
            SET JOBSCHEDULER_STATUS = 'done',
                JOBSCHEDULER_ELAPSED_TIME = ?
            WHERE JOBSCHEDULER_JOB_ID = ?
        """, (execution_time, job_id))
        conn.commit()
        conn.close()

        completed += 1
        print(f"Completed job: {job_id} ({execution_time:.3f}s)")

    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            conflicts += 1
            print(f"Lock conflict on attempt {attempt+1}")
        else:
            errors += 1
            print(f"Operational error: {e}")
        time.sleep(random.uniform(0.01, 0.05))

    except Exception as e:
        errors += 1
        print(f"Unexpected error: {e}")

print(f"\n========================================")
print(f"Worker {worker_id} finished")
print(f"  Completed: {completed}")
print(f"  Conflicts: {conflicts}")
print(f"  Errors: {errors}")
print(f"========================================")
EOF

echo ""
echo "Worker $WORKER_ID finished at $(date)"
