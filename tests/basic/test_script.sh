#!/bin/bash
# Test script that receives positional arguments

echo "Received arguments: $@"
echo "  Arg 1 (paramA): $1"
echo "  Arg 2 (paramB): $2"
echo "  Arg 3 (paramC): $3"

# Simulate some work
sleep 0.1

echo "Job completed successfully"
exit 0
