# Job Scheduler v2

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.6+](https://img.shields.io/badge/python-3.6+-blue.svg)](https://www.python.org/downloads/)

SQLite-based parallel job scheduler for HPC environments (TSUBAME, etc.)

[日本語ドキュメント](README_ja.md)

## Features

- ✅ **Safe Concurrent Access**: SQLite with WAL mode for multi-node safety
- ✅ **Priority Scheduling**: Execute important jobs first
- ✅ **Smart Scheduling**: Consider remaining time for job selection
- ✅ **Flexible Arguments**: Support both positional and named arguments
- ✅ **Real-time Output**: Stream stdout/stderr in real-time
- ✅ **Automatic Recovery**: Auto-recover from unexpected interruptions
- ✅ **Progress Viewer**: Real-time monitoring with dedicated viewer

## Quick Start

```bash
# 1. Import CSV to SQLite
db_util import jobs.db input.csv

# 2. Run jobs
job_scheduler jobs.db run.sh

# 3. Monitor progress
progress_viewer jobs.db --watch
```

## Installation

```bash
git clone https://github.com/your-username/job-runner-v2.git
cd job-runner-v2
chmod +x script/job_scheduler script/db_util.py script/progress_viewer.py

# Add to PATH
export PATH="$(pwd)/script:$PATH"

# To make permanent, add to ~/.bashrc
echo 'export PATH="/path/to/job-runner-v2/script:$PATH"' >> ~/.bashrc
```

No external dependencies required! (Python 3.6+ standard library only)

## Documentation

- [Quick Start Guide](QUICKSTART.md) - Get started in 5 minutes
- [Setup Guide](SETUP.md) - Installation and configuration
- [Full Documentation](README.md) - Complete reference
- [Test Guide](README_TEST.md) - Testing instructions
- [日本語ドキュメント](README_ja.md) - Japanese documentation

## Benchmarks

| Test | Configuration | Result | Success Rate |
|------|---------------|--------|--------------|
| Local | 8 parallel × 100 jobs | 100/100 | 100% |
| Multi-node | 10 workers × 200 jobs | 200/200 | 100% |
| Stress | 30 workers × 1700 jobs | 1668/1700 | 98.1% |

## License

[MIT License](LICENSE)

## Contributing

We welcome contributions! Please feel free to submit a Pull Request.
