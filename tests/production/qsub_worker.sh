#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=0:10:00
#$ -N jobrunner_prod_test

source $HOME/.bashrc

WORK_DIR="/gs/bs/tga-furui/workspace/dev/job-runner/job-runner-v2"
DB_FILE="$WORK_DIR/test_production/production_jobs.db"

echo "=========================================="
echo "Job Runner v2 Production Test Worker"
echo "Worker: ${JOB_ID:-local}"
echo "Hostname: $(hostname)"
echo "Start time: $(date)"
echo "=========================================="

cd "$WORK_DIR"

# Run scheduler
./job_scheduler "$DB_FILE" "test_production/worker.sh" \
    --max-runtime 500 \
    --margin-time 30 \
    --smart-scheduling true

echo ""
echo "Worker finished at $(date)"
