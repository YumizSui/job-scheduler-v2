#!/usr/bin/env python3
"""Generate test jobs for production testing"""

import csv

# Generate 100 test jobs with various parameters
jobs = []
for i in range(100):
    jobs.append({
        'job_name': f'test_job_{i:03d}',
        'param1': f'value_{i}',
        'param2': i * 10,
        'sleep_time': 0.1 + (i % 5) * 0.1,  # 0.1 to 0.5 seconds
        'JOBSCHEDULER_PRIORITY': i % 10,  # Priority 0-9
        'JOBSCHEDULER_ESTIMATE_TIME': 0.001,  # ~3 seconds
    })

# Write CSV
with open('test_production/production_jobs.csv', 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['job_name', 'param1', 'param2', 'sleep_time',
                                            'JOBSCHEDULER_PRIORITY', 'JOBSCHEDULER_ESTIMATE_TIME'])
    writer.writeheader()
    writer.writerows(jobs)

print(f"Generated {len(jobs)} test jobs")
