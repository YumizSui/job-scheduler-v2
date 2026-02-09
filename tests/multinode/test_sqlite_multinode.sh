#!/bin/bash
# 複数ノードシミュレーション: 複数のジョブスクリプトを同時実行してSQLiteの動作を確認

set -e

# 色付き出力
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Multi-node SQLite Test${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

# テストディレクトリ
TEST_DIR="./test_multinode_tmp"
DB_FILE="$TEST_DIR/test_jobs.db"
LOG_DIR="$TEST_DIR/logs"

# クリーンアップ
rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"
mkdir -p "$LOG_DIR"

# テスト用データベース作成スクリプト
cat > "$TEST_DIR/setup_db.py" << 'EOF'
#!/usr/bin/env python3
import sqlite3
import sys

db_path = sys.argv[1]
num_jobs = int(sys.argv[2])

conn = sqlite3.connect(db_path)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=30000")

conn.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        JOBSCHEDULER_JOB_ID TEXT PRIMARY KEY,
        JOBSCHEDULER_STATUS TEXT NOT NULL,
        JOBSCHEDULER_PRIORITY INTEGER DEFAULT 0,
        param1 TEXT
    )
""")

conn.execute("DELETE FROM jobs")

for i in range(num_jobs):
    conn.execute(
        "INSERT INTO jobs VALUES (?, 'pending', ?, ?)",
        (f"job_{i:04d}", i % 10, f"param_{i}")
    )

conn.commit()
conn.close()
print(f"Created {num_jobs} jobs in {db_path}")
EOF

chmod +x "$TEST_DIR/setup_db.py"

# ワーカースクリプト（ジョブを取得して実行）
cat > "$TEST_DIR/worker.py" << 'EOF'
#!/usr/bin/env python3
import sqlite3
import sys
import time
import random

worker_id = sys.argv[1]
db_path = sys.argv[2]
max_jobs = int(sys.argv[3])

completed = 0
conflicts = 0

for _ in range(max_jobs):
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")

        conn.execute("BEGIN IMMEDIATE")

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
            break

        job_id, param1 = row

        conn.execute("""
            UPDATE jobs
            SET JOBSCHEDULER_STATUS = 'running'
            WHERE JOBSCHEDULER_JOB_ID = ?
        """, (job_id,))

        conn.commit()
        conn.close()

        # ジョブ実行シミュレーション
        time.sleep(random.uniform(0.001, 0.01))

        # 完了マーク
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            UPDATE jobs
            SET JOBSCHEDULER_STATUS = 'done'
            WHERE JOBSCHEDULER_JOB_ID = ?
        """, (job_id,))
        conn.commit()
        conn.close()

        completed += 1

    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            conflicts += 1
        time.sleep(0.001)

print(f"Worker {worker_id}: completed={completed}, conflicts={conflicts}")
EOF

chmod +x "$TEST_DIR/worker.py"

# テストパラメータ
NUM_JOBS=100
NUM_WORKERS=8

echo "Setting up database with $NUM_JOBS jobs..."
python3 "$TEST_DIR/setup_db.py" "$DB_FILE" "$NUM_JOBS"

echo ""
echo "Launching $NUM_WORKERS workers in parallel..."
START_TIME=$(date +%s)

# ワーカーをバックグラウンドで起動
for i in $(seq 1 $NUM_WORKERS); do
    python3 "$TEST_DIR/worker.py" "$i" "$DB_FILE" 20 > "$LOG_DIR/worker_$i.log" 2>&1 &
done

# すべてのワーカーの完了を待つ
wait

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo -e "${GREEN}All workers finished in ${ELAPSED}s${NC}"
echo ""

# ワーカーのログを表示
echo "Worker results:"
for i in $(seq 1 $NUM_WORKERS); do
    cat "$LOG_DIR/worker_$i.log"
done

echo ""

# データベースの整合性チェック
cat > "$TEST_DIR/verify.py" << 'EOF'
#!/usr/bin/env python3
import sqlite3
import sys

db_path = sys.argv[1]
conn = sqlite3.connect(db_path)

cursor = conn.execute("""
    SELECT JOBSCHEDULER_STATUS, COUNT(*)
    FROM jobs
    GROUP BY JOBSCHEDULER_STATUS
""")

results = dict(cursor.fetchall())
total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

print("Database verification:")
print(f"  Total: {total}")
print(f"  Pending: {results.get('pending', 0)}")
print(f"  Running: {results.get('running', 0)}")
print(f"  Done: {results.get('done', 0)}")

running = results.get('running', 0)
if running == 0:
    print("\n✓ PASS - No jobs stuck in 'running' state")
    sys.exit(0)
else:
    print(f"\n✗ FAIL - {running} jobs stuck in 'running' state")
    sys.exit(1)
EOF

chmod +x "$TEST_DIR/verify.py"

python3 "$TEST_DIR/verify.py" "$DB_FILE"
RESULT=$?

echo ""
if [ $RESULT -eq 0 ]; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Multi-node test PASSED${NC}"
    echo -e "${GREEN}SQLite is working correctly${NC}"
    echo -e "${GREEN}========================================${NC}"
else
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}Multi-node test FAILED${NC}"
    echo -e "${RED}========================================${NC}"
fi

echo ""
echo "Test artifacts are in: $TEST_DIR"
echo "Database file: $DB_FILE"
echo ""

exit $RESULT
