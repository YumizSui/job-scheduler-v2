#!/bin/bash
# Run comprehensive stress test

set -e

# Load bashrc for miqsub alias
source $HOME/.bashrc

WORK_DIR="/gs/bs/tga-furui/workspace/dev/job-runner/job-runner-v2"
cd "$WORK_DIR"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Job Runner v2 - Stress Test Suite${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

# Test 1: Large job count (1000 jobs, 5 workers)
echo -e "${GREEN}Test 1: Large Job Count (1000 jobs, 5 workers)${NC}"
echo "Generating jobs..."
python3 test_stress/generate_stress_jobs.py 1000 test_stress/stress_1000.csv
python3 db_util.py import test_stress/stress_1000.csv --db-path test_stress/stress_1000.db

echo "Submitting workers..."
for i in $(seq 1 5); do
    cat > test_stress/qsub_temp_${i}.sh << EOF
#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=0:30:00
#$ -N stress1k_w${i}

source \$HOME/.bashrc
cd "$WORK_DIR"
./job_scheduler test_stress/stress_1000.db "test_stress/worker.sh" --max-runtime 1600 --margin-time 100
EOF
    chmod +x test_stress/qsub_temp_${i}.sh
    miqsub test_stress/qsub_temp_${i}.sh
    sleep 1
done

echo -e "${GREEN}Test 1 submitted (Job: stress1k_w1-5)${NC}"
echo ""

# Test 2: High concurrency (500 jobs, 20 workers)
echo -e "${GREEN}Test 2: High Concurrency (500 jobs, 20 workers)${NC}"
echo "Generating jobs..."
python3 test_stress/generate_stress_jobs.py 500 test_stress/stress_500.csv
python3 db_util.py import test_stress/stress_500.csv --db-path test_stress/stress_500.db

echo "Submitting workers..."
for i in $(seq 1 20); do
    cat > test_stress/qsub_temp2_${i}.sh << EOF
#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=0:20:00
#$ -N stress_high_w${i}

source \$HOME/.bashrc
cd "$WORK_DIR"
./job_scheduler test_stress/stress_500.db "test_stress/worker.sh" --max-runtime 1000 --margin-time 50
EOF
    chmod +x test_stress/qsub_temp2_${i}.sh
    miqsub test_stress/qsub_temp2_${i}.sh
    sleep 0.5
done

echo -e "${GREEN}Test 2 submitted (Job: stress_high_w1-20)${NC}"
echo ""

# Test 3: Parallel mode stress (200 jobs, 5 workers with 4 parallel each)
echo -e "${GREEN}Test 3: Parallel Mode Stress (200 jobs, 5 workers × 4 parallel)${NC}"
echo "Generating jobs..."
python3 test_stress/generate_stress_jobs.py 200 test_stress/stress_parallel.csv
python3 db_util.py import test_stress/stress_parallel.csv --db-path test_stress/stress_parallel.db

echo "Submitting workers..."
for i in $(seq 1 5); do
    cat > test_stress/qsub_temp3_${i}.sh << EOF
#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=0:20:00
#$ -N stress_par_w${i}

source \$HOME/.bashrc
cd "$WORK_DIR"
./job_scheduler test_stress/stress_parallel.db "test_stress/worker.sh" --parallel 4 --max-runtime 1000 --margin-time 50
EOF
    chmod +x test_stress/qsub_temp3_${i}.sh
    miqsub test_stress/qsub_temp3_${i}.sh
    sleep 1
done

echo -e "${GREEN}Test 3 submitted (Job: stress_par_w1-5)${NC}"
echo ""

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}All stress tests submitted!${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""
echo "Monitor progress with:"
echo "  qstat"
echo "  python3 progress_viewer.py test_stress/stress_1000.db --watch"
echo "  python3 progress_viewer.py test_stress/stress_500.db --watch"
echo "  python3 progress_viewer.py test_stress/stress_parallel.db --watch"
echo ""
echo "Verify results after completion:"
echo "  python3 db_util.py stats test_stress/stress_1000.db"
echo "  python3 db_util.py stats test_stress/stress_500.db"
echo "  python3 db_util.py stats test_stress/stress_parallel.db"
echo ""
