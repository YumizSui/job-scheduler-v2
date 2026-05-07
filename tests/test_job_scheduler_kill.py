#!/usr/bin/env python3
"""Tests for process-group kill behavior in job_scheduler.

Verifies that start_new_session=True + os.killpg terminates grandchild processes
(e.g. python wrapper → sleep grandchild) rather than leaking them as orphans.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "script"))

# Wrapper command: python3 spawns a grandchild sleep.
# Using python3 (not bash) as wrapper avoids bash's exec-optimization which
# would replace bash with the grandchild PID instead of forking.
WRAPPER_CMD = [
    "python3", "-c",
    "import subprocess; p = subprocess.Popen(['sleep', '60']); p.wait()",
]


def _grandchild_pid(parent_pid: int) -> int | None:
    """Return first child PID of parent_pid via pgrep, or None."""
    r = subprocess.run(["pgrep", "-P", str(parent_pid)], capture_output=True, text=True)
    pids = r.stdout.split()
    return int(pids[0]) if pids else None


def _pid_alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


class TestStartNewSession:
    def test_creates_separate_process_group(self):
        proc = subprocess.Popen(
            WRAPPER_CMD,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            child_pgid = os.getpgid(proc.pid)
            parent_pgid = os.getpgid(0)
            assert child_pgid != parent_pgid, (
                "start_new_session=True must place child in a different process group"
            )
        finally:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()


class TestKillpgTerminatesGrandchild:
    def test_killpg_kills_grandchild(self):
        """os.killpg on the child's pgid must also terminate grandchildren."""
        proc = subprocess.Popen(
            WRAPPER_CMD,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)  # let wrapper spawn grandchild

        grand_pid = _grandchild_pid(proc.pid)
        assert grand_pid is not None, "grandchild sleep process not found"
        assert _pid_alive(grand_pid), "grandchild should be running"

        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=5)

        time.sleep(0.1)
        assert not _pid_alive(grand_pid), (
            f"grandchild pid={grand_pid} survived os.killpg — orphan leak"
        )

    @pytest.mark.xfail(reason="single-PID terminate leaks grandchild — documents the original bug")
    def test_single_pid_terminate_leaks_grandchild(self):
        """Demonstrate the original bug: process.terminate() leaves grandchild alive."""
        proc = subprocess.Popen(
            WRAPPER_CMD,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)

        grand_pid = _grandchild_pid(proc.pid)
        assert grand_pid is not None, "grandchild not found"

        proc.terminate()
        proc.wait(timeout=5)

        time.sleep(0.1)
        alive = _pid_alive(grand_pid)
        # Clean up orphan before asserting
        if alive:
            try:
                os.kill(grand_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        # Expected to fail: grandchild IS still alive (this is the original bug)
        assert not alive, "grandchild survived single-PID terminate (original bug)"


class TestTerminateProcessGroup:
    """Integration test: JobScheduler._terminate_process_group kills grandchildren."""

    def test_terminate_process_group_method(self):
        from job_scheduler import JobScheduler

        scheduler = JobScheduler(db_path=":memory:", command="echo")

        proc = subprocess.Popen(
            WRAPPER_CMD,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)

        grand_pid = _grandchild_pid(proc.pid)
        assert grand_pid is not None, "grandchild not found"

        scheduler._terminate_process_group(proc, "test-job-kill")

        time.sleep(0.1)
        assert not _pid_alive(grand_pid), (
            f"grandchild pid={grand_pid} survived _terminate_process_group"
        )
