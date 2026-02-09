#!/bin/bash
# Stress test worker - minimal overhead

JOB_ID=$1
PARAM1=$2
PARAM2=$3
PARAM3=$4
SLEEP_TIME=$5

# Fast execution
sleep "$SLEEP_TIME"

# Random failure (1% rate)
if [ $((RANDOM % 100)) -eq 0 ]; then
    echo "ERROR: Random failure"
    exit 1
fi

echo "OK: $JOB_ID completed"
exit 0
