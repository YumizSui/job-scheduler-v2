#!/usr/bin/env python3
"""Generate large number of jobs for stress testing"""

import csv
import sys

def generate_jobs(num_jobs: int, output_file: str):
    """Generate test jobs"""
    jobs = []

    for i in range(num_jobs):
        jobs.append({
            'job_id': f'stress_{i:06d}',
            'param1': f'value_{i}',
            'param2': i * 10,
            'param3': f'data_{i % 100}',
            'sleep_time': 0.01 + (i % 10) * 0.01,  # 0.01 to 0.1 seconds
            'JOBSCHEDULER_PRIORITY': i % 100,  # Priority 0-99
            'JOBSCHEDULER_ESTIMATE_TIME': 0.0003,  # ~1 second
        })

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'job_id', 'param1', 'param2', 'param3', 'sleep_time',
            'JOBSCHEDULER_PRIORITY', 'JOBSCHEDULER_ESTIMATE_TIME'
        ])
        writer.writeheader()
        writer.writerows(jobs)

    print(f"Generated {len(jobs)} jobs -> {output_file}")

if __name__ == "__main__":
    num_jobs = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    output = sys.argv[2] if len(sys.argv) > 2 else 'test_stress/stress_jobs.csv'
    generate_jobs(num_jobs, output)
