# Job Scheduler v2

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.6+](https://img.shields.io/badge/python-3.6+-blue.svg)](https://www.python.org/downloads/)

SQLite-based parallel job scheduler for HPC environments (TSUBAME, etc.)

[日本語ドキュメント](README_ja.md)

## Features

- ✅ **Safe Concurrent Access**: SQLite with atomic transactions for multi-node safety
- ✅ **Job Dependencies**: DAG-based dependency management (job dependencies)
- ✅ **Priority Scheduling**: Execute important jobs first
- ✅ **Smart Scheduling**: Consider remaining time for job selection
- ✅ **Flexible Arguments**: Support both positional and named arguments
- ✅ **Real-time Output**: Stream stdout/stderr in real-time
- ✅ **Automatic Recovery**: Bidirectional reconciliation between DB status and heartbeat files (stuck jobs → pending, live jobs wrongly reset → running)
- ✅ **Progress Viewer**: Real-time monitoring with dependency status
- ✅ **DB Inspection CLI**: `show`/`list`/`stats --by` — inspect jobs without CSV export
- ✅ **Interactive TUI**: `job_tui` — browse, filter, and manage jobs interactively

## Quick Start

```bash
# 1. Import CSV to SQLite
db_util import jobs.csv

# 2. Run jobs
job_scheduler jobs.db "bash run.sh"

# 3. Monitor progress
progress_viewer jobs.db --watch

# 4. Inspect results
db_util list --db-path jobs.db --status error --grep-error "CUDA"
db_util show job_00000003 --db-path jobs.db
job_tui jobs.db
```

## Installation

```bash
git clone https://github.com/your-username/job-runner-v2.git
cd job-runner-v2
chmod +x script/job_scheduler script/db_util script/progress_viewer

# Add to PATH
export PATH="$(pwd)/script:$PATH"

# To make permanent, add to ~/.bashrc
echo 'export PATH="/path/to/job-runner-v2/script:$PATH"' >> ~/.bashrc
```

`job_scheduler` requires only Python stdlib. For `db_util show`/`list`, `stats --by`, and `job_tui`, install optional dependencies first:

```bash
uv sync        # creates .venv with rich + textual
```

## Documentation

- [Quick Start Guide](QUICKSTART.md) - Get started
- [Setup Guide](SETUP.md) - Installation and configuration
- [日本語ドキュメント](README_ja.md) - Japanese documentation

## License

[MIT License](LICENSE)

## Contributing

We welcome contributions! Please feel free to submit a Pull Request.
