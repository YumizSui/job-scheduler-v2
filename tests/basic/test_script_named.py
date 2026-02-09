#!/usr/bin/env python3
# Test script that receives named arguments

import argparse
import time

parser = argparse.ArgumentParser()
parser.add_argument('--paramA', required=True)
parser.add_argument('--paramB', required=True)
parser.add_argument('--paramC', required=True)
args = parser.parse_args()

print(f"Received named arguments:")
print(f"  paramA: {args.paramA}")
print(f"  paramB: {args.paramB}")
print(f"  paramC: {args.paramC}")

# Simulate some work
time.sleep(0.1)

print("Job completed successfully")
