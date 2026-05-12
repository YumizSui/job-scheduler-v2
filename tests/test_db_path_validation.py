#!/usr/bin/env python3
"""Tests for friendly error handling when an invalid path is supplied as a
SQLite database file (directory, text file, missing file).

Each CLI is invoked via subprocess; we check exit code != 0 and that the
error message clearly identifies the cause.
"""

import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).parent.parent / "script"
DB_UTIL = SCRIPT_DIR / "db_util.py"
PROGRESS_VIEWER = SCRIPT_DIR / "progress_viewer.py"
JOB_SCHEDULER = SCRIPT_DIR / "job_scheduler.py"
JOB_TUI = SCRIPT_DIR / "job_tui.py"


def _run(*argv, **kwargs):
    return subprocess.run(
        [sys.executable, *argv],
        capture_output=True,
        text=True,
        **kwargs,
    )


@pytest.fixture
def text_file(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello world\nnot a sqlite db\n")
    return str(p)


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "fake.csv"
    p.write_text("a,b,c\n1,2,3\n")
    return str(p)


@pytest.fixture
def directory(tmp_path):
    d = tmp_path / "somedir"
    d.mkdir()
    return str(d)


@pytest.fixture
def missing(tmp_path):
    return str(tmp_path / "does_not_exist.db")


# --- Subcommands of db_util that take a DB path positionally ---
# Subcommand list with the args needed AFTER the db path so the parser succeeds.
DBUTIL_READ_CMDS = [
    ("stats", []),
    ("reset", []),
    ("recover", []),
    ("kill", ["--jobs", "job_00000000"]),
]


@pytest.mark.parametrize("subcmd,extra", DBUTIL_READ_CMDS)
def test_dbutil_positional_rejects_directory(subcmd, extra, directory):
    r = _run(str(DB_UTIL), subcmd, directory, *extra)
    assert r.returncode != 0
    assert "is a directory" in (r.stdout + r.stderr)


@pytest.mark.parametrize("subcmd,extra", DBUTIL_READ_CMDS)
def test_dbutil_positional_rejects_text_file(subcmd, extra, text_file):
    r = _run(str(DB_UTIL), subcmd, text_file, *extra)
    assert r.returncode != 0
    assert "not a SQLite database" in (r.stdout + r.stderr)


@pytest.mark.parametrize("subcmd,extra", DBUTIL_READ_CMDS)
def test_dbutil_positional_reports_missing(subcmd, extra, missing):
    r = _run(str(DB_UTIL), subcmd, missing, *extra)
    assert r.returncode != 0
    assert "does not exist" in (r.stdout + r.stderr)


# --- db_util subcommands that take --db-path ---
DBUTIL_DBPATH_CMDS = [
    ("show", ["dummy_job_id"]),
    ("list", []),
    ("add", []),  # also needs csv_file positional
]


def _dbpath_cli(subcmd, extra, path):
    # add takes csv_file positionally before --db-path; others put it first.
    if subcmd == "add":
        # csv_file path doesn't matter — we'll fail at db_path validation first.
        return [str(DB_UTIL), "add", "/nonexistent.csv", "--db-path", path]
    if subcmd == "show":
        return [str(DB_UTIL), "show", *extra, "--db-path", path]
    if subcmd == "list":
        return [str(DB_UTIL), "list", "--db-path", path]
    raise AssertionError(subcmd)


@pytest.mark.parametrize("subcmd,extra", DBUTIL_DBPATH_CMDS)
def test_dbutil_dbpath_rejects_directory(subcmd, extra, directory):
    r = _run(*_dbpath_cli(subcmd, extra, directory))
    assert r.returncode != 0
    assert "is a directory" in (r.stdout + r.stderr)


@pytest.mark.parametrize("subcmd,extra", DBUTIL_DBPATH_CMDS)
def test_dbutil_dbpath_rejects_text_file(subcmd, extra, text_file):
    r = _run(*_dbpath_cli(subcmd, extra, text_file))
    assert r.returncode != 0
    assert "not a SQLite database" in (r.stdout + r.stderr)


@pytest.mark.parametrize("subcmd,extra", DBUTIL_DBPATH_CMDS)
def test_dbutil_dbpath_reports_missing(subcmd, extra, missing):
    r = _run(*_dbpath_cli(subcmd, extra, missing))
    assert r.returncode != 0
    assert "does not exist" in (r.stdout + r.stderr)


# --- db_util import --force must not clobber a directory or a text file ---

def test_import_force_refuses_directory(csv_file, directory):
    r = _run(str(DB_UTIL), "import", csv_file, "--db-path", directory, "--force")
    assert r.returncode != 0
    assert "is a directory" in (r.stdout + r.stderr)
    # Original directory still exists
    assert Path(directory).is_dir()


def test_import_force_refuses_text_file(csv_file, text_file):
    original = Path(text_file).read_text()
    r = _run(str(DB_UTIL), "import", csv_file, "--db-path", text_file, "--force")
    assert r.returncode != 0
    assert "not a SQLite database" in (r.stdout + r.stderr)
    # Original text file untouched
    assert Path(text_file).read_text() == original


# --- progress_viewer ---

def test_progress_viewer_rejects_directory(directory):
    r = _run(str(PROGRESS_VIEWER), directory)
    assert r.returncode != 0
    assert "is a directory" in (r.stdout + r.stderr)


def test_progress_viewer_rejects_text_file(text_file):
    r = _run(str(PROGRESS_VIEWER), text_file)
    assert r.returncode != 0
    assert "not a SQLite database" in (r.stdout + r.stderr)


def test_progress_viewer_reports_missing(missing):
    r = _run(str(PROGRESS_VIEWER), missing)
    assert r.returncode != 0
    assert "does not exist" in (r.stdout + r.stderr)


# --- job_scheduler ---

def test_job_scheduler_rejects_directory(directory):
    r = _run(str(JOB_SCHEDULER), directory, "true")
    assert r.returncode != 0
    assert "is a directory" in (r.stdout + r.stderr)


def test_job_scheduler_rejects_text_file(text_file):
    r = _run(str(JOB_SCHEDULER), text_file, "true")
    assert r.returncode != 0
    assert "not a SQLite database" in (r.stdout + r.stderr)


def test_job_scheduler_reports_missing(missing):
    r = _run(str(JOB_SCHEDULER), missing, "true")
    assert r.returncode != 0
    assert "does not exist" in (r.stdout + r.stderr)


# --- job_tui (validation runs before TUI starts, so no terminal needed) ---

def test_job_tui_rejects_directory(directory):
    r = _run(str(JOB_TUI), directory)
    assert r.returncode != 0
    assert "is a directory" in (r.stdout + r.stderr)


def test_job_tui_rejects_text_file(text_file):
    r = _run(str(JOB_TUI), text_file)
    assert r.returncode != 0
    assert "not a SQLite database" in (r.stdout + r.stderr)


def test_job_tui_reports_missing(missing):
    r = _run(str(JOB_TUI), missing)
    assert r.returncode != 0
    assert "does not exist" in (r.stdout + r.stderr)
