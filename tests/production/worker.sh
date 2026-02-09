#!/bin/bash
# Production test worker script - receives positional args

JOB_NAME=$1
PARAM1=$2
PARAM2=$3
SLEEP_TIME=$4

echo "=========================================="
echo "Job: $JOB_NAME"
echo "Param1: $PARAM1"
echo "Param2: $PARAM2"
echo "Sleep time: ${SLEEP_TIME}s"
echo "Hostname: $(hostname)"
echo "Date: $(date)"
echo "=========================================="

# Simulate work
sleep "$SLEEP_TIME"

# Random success/failure (95% success rate)
if [ $((RANDOM % 20)) -eq 0 ]; then
    echo "ERROR: Simulated failure"
    exit 1
fi

echo "Job completed successfully"
exit 0
