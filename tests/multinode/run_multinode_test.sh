#!/bin/bash
# 実際の複数ノードテストを実行するスクリプト

set -e

# .bashrcを読み込んでmiqsubエイリアスを有効化
source $HOME/.bashrc

WORK_DIR="/gs/bs/tga-furui/workspace/dev/job-runner/job-runner-v2"
DB_FILE="$WORK_DIR/test_real_multinode.db"
LOG_DIR="$WORK_DIR/test_logs"
JOB_SCRIPT="$WORK_DIR/test_real_multinode.sh"

# 色付き出力
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Real Multi-node SQLite Test${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

# パラメータ
NUM_JOBS=200
NUM_WORKERS=10

echo "Test parameters:"
echo "  Total jobs: $NUM_JOBS"
echo "  Number of workers: $NUM_WORKERS"
echo "  Database: $DB_FILE"
echo "  Log directory: $LOG_DIR"
echo ""

# クリーンアップ
rm -rf "$LOG_DIR"
mkdir -p "$LOG_DIR"

# データベースセットアップ
echo "Setting up database..."
python3 "$WORK_DIR/setup_multinode_test.py" setup "$DB_FILE" "$NUM_JOBS"

echo ""
echo -e "${GREEN}Database setup complete${NC}"
echo ""

# ジョブ投入
echo "Submitting $NUM_WORKERS qsub jobs..."
echo ""

for i in $(seq 1 $NUM_WORKERS); do
    # miqsubで投入（環境変数を-vで明示的に渡す）
    miqsub -N "sqlite_test_w${i}" \
        -v WORKER_ID="worker_$i",DB_FILE="$DB_FILE",MAX_JOBS=30 \
        -o "$LOG_DIR/worker_${i}.out" \
        -e "$LOG_DIR/worker_${i}.err" \
        "$JOB_SCRIPT"

    # システム負荷を避けるため少し待機
    sleep 1
done

echo ""
echo -e "${GREEN}All workers submitted${NC}"
echo ""
echo "Monitor progress with:"
echo "  qstat"
echo "  watch -n 2 'python3 $WORK_DIR/setup_multinode_test.py verify $DB_FILE'"
echo ""
echo "After all jobs complete, verify results with:"
echo "  python3 $WORK_DIR/setup_multinode_test.py verify $DB_FILE"
echo ""
