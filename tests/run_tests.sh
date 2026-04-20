#!/bin/bash
#$ -cwd
#$ -l cpu_40=1
#$ -l h_rt=0:10:00
#$ -N test_job_scheduler

source $HOME/.bashrc

cd /gs/bs/tga-furui/workspace/dev/job-scheduler-v2

echo "=========================================="
echo "Job Scheduler v2 - Test Suite"
echo "Hostname: $(hostname)"
echo "Start time: $(date)"
echo "=========================================="

uv run --with pytest python -m pytest tests/test_longest_first.py tests/test_log_output.py tests/test_concurrent_claim.py tests/test_db_util_cli.py -v -s
EXIT_CODE=$?

echo ""
echo "Finished at $(date)"
echo "Exit code: $EXIT_CODE"
exit $EXIT_CODE
