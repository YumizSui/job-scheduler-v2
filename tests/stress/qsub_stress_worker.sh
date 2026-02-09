#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=1:00:00
#$ -N stress_test

source $HOME/.bashrc

WORK_DIR="/gs/bs/tga-furui/workspace/dev/job-runner/job-runner-v2"
DB_FILE="$WORK_DIR/test_stress/stress_jobs.db"
WORKER_ID="${JOB_ID}"

echo "=========================================="
echo "Stress Test Worker"
echo "Worker ID: ${WORKER_ID}"
echo "Hostname: $(hostname)"
echo "Start time: $(date)"
echo "=========================================="

cd "$WORK_DIR"

# Run scheduler
./job_scheduler "$DB_FILE" "test_stress/worker.sh" \
    --max-runtime 3400 \
    --margin-time 100 \
    --smart-scheduling true

echo ""
echo "Worker finished at $(date)"
echo "Exit code: $?"
