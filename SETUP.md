# Setup Guide

## Requirements

- Python 3.6 or higher
- Standard library only (no external dependencies)
- POSIX-compatible OS (Linux, macOS, etc.)

## Installation

### Quick Install

```bash
git clone <your-repository-url>
cd job-runner-v2
chmod +x script/job_scheduler script/db_util.py script/progress_viewer.py

# Add to PATH
export PATH="$(pwd)/script:$PATH"

# To make permanent, add to ~/.bashrc
echo 'export PATH="/path/to/job-runner-v2/script:$PATH"' >> ~/.bashrc
```

### Verify Installation

```bash
# Check Python version
python3 --version

# Test basic functionality
job_scheduler --help
db_util --help
progress_viewer --help
```

## Quick Test

```bash
# Run basic test
cd test_basic
db_util import test.db test_jobs.csv
job_scheduler test.db test_script.sh --max-runtime 60

# Check results
progress_viewer test.db
```

## Configuration for TSUBAME/HPC

### 1. Add to PATH (Required)

```bash
# Add to ~/.bashrc
export PATH="/path/to/job-runner-v2/script:$PATH"
```

### 2. Create alias for miqsub (TSUBAME)

```bash
# Add to ~/.bashrc (if not already set)
alias miqsub='qsub -g your-group-id'
```

### 3. Test on compute node

```bash
# Submit a test job
cd test_basic
cat > test_job.sh << 'EOJ'
#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=0:05:00

source $HOME/.bashrc
cd /path/to/job-runner-v2/test_basic
job_scheduler test.db test_script.sh --max-runtime 180
EOJ

miqsub test_job.sh
```

## Troubleshooting

### Permission Denied

```bash
chmod +x script/job_scheduler script/db_util.py script/progress_viewer.py
chmod +x tests/basic/*.sh
chmod +x tests/production/*.sh
chmod +x tests/stress/*.sh
```

### Python Not Found

```bash
# Check Python location
which python3

# Ensure scripts are in PATH
export PATH="/path/to/job-runner-v2/script:$PATH"
job_scheduler --help
```

### Database Locked

This is normal during high contention. The scheduler automatically retries.
If persistent, check for long-running processes or stale locks.

## Next Steps

1. Read [QUICKSTART.md](QUICKSTART.md) for usage examples
2. Check [README.md](README.md) or [README_ja.md](README_ja.md) for full documentation

## Support

For issues and questions, please check the documentation:
- [QUICKSTART.md](QUICKSTART.md) - Quick start guide
- [README.md](README.md) / [README_ja.md](README_ja.md) - Full documentation
