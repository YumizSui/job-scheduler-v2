#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Job Scheduler v2 - SQLite-based parallel job scheduler

Features:
- SQLite backend for safe concurrent access
- Multi-node support with file locking
- Priority-based scheduling
- Smart scheduling with estimate_time
- Named arguments support
- Parallel execution mode
"""

import sqlite3
import subprocess
import argparse
import time
import signal
import sys
import os
import random
import socket
import logging
from typing import Optional, Dict, List, Tuple
from threading import Event, Thread
from multiprocessing import Process, Value
from pathlib import Path

def setup_logging(use_stderr=False):
    """Configure logging output destination."""
    stream = sys.stderr if use_stderr else sys.stdout
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=stream,
    )

# Global shutdown event
shutdown_event = Event()


def _heartbeat_dir(db_path: str) -> Path:
    """Return path to the heartbeat directory for a given DB file."""
    return Path(db_path + ".heartbeat")


def _ensure_heartbeat_dir(db_path: str) -> Path:
    """Create heartbeat directory if it doesn't exist and return its path."""
    d = _heartbeat_dir(db_path)
    d.mkdir(exist_ok=True)
    return d


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logging.warning(f"Signal {signum} received. Shutting down gracefully...")
    shutdown_event.set()


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


class JobScheduler:
    """Main job scheduler class"""

    def __init__(self, db_path: str, command: str, **kwargs):
        self.db_path = db_path
        self.command = command
        self.max_runtime = kwargs.get('max_runtime', 86400)
        self.margin_time = kwargs.get('margin_time', 0)
        self.speed_factor = kwargs.get('speed_factor', 1.0)
        self.smart_scheduling = kwargs.get('smart_scheduling', True)
        self.longest_first = kwargs.get('longest_first', False)
        self.named_args = kwargs.get('named_args', False)
        self.parallel = kwargs.get('parallel', 1)
        self.dep_wait_interval = kwargs.get('dep_wait_interval', 30)
        self.heartbeat_interval = kwargs.get('heartbeat_interval', 30)
        self.stale_threshold = kwargs.get('stale_threshold', 120)
        self.target_jobs = kwargs.get('target_jobs', None)  # list of job IDs or None
        self.jobs_only = kwargs.get('jobs_only', False)

        # Generate worker ID (hostname:PID)
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"

        self.start_time = None
        self.jobs_completed = 0
        self.jobs_failed = 0

    def connect_db(self) -> sqlite3.Connection:
        """Create database connection with optimized settings"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=30000")

        # Create job_dependencies table for backward compatibility
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_dependencies (
                job_id TEXT NOT NULL,
                depends_on TEXT NOT NULL,
                PRIMARY KEY (job_id, depends_on)
            )
        """)

        # Auto-migration: Add heartbeat columns if they don't exist
        cursor = conn.execute("PRAGMA table_info(jobs)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'JOBSCHEDULER_HEARTBEAT' not in existing_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN JOBSCHEDULER_HEARTBEAT TEXT")

        if 'JOBSCHEDULER_WORKER_ID' not in existing_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN JOBSCHEDULER_WORKER_ID TEXT")

        if 'JOBSCHEDULER_KILL_REQUESTED' not in existing_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN JOBSCHEDULER_KILL_REQUESTED TEXT")

        return conn

    def get_pending_job(self, available_time: float, target_job_ids: Optional[list] = None) -> Optional[Dict]:
        """
        Get next pending job based on priority and estimate_time.

        SELECT and UPDATE run inside a single BEGIN IMMEDIATE transaction so
        concurrent workers serialize on the SQLite write lock. This eliminates
        races at the cost of sequential claiming, which is acceptable because
        the SELECT is index-backed and finishes in milliseconds.

        If target_job_ids is given, only consider jobs with those IDs.
        Returns job dict or None if no suitable job available.
        """
        max_retries = 5

        order_by = "JOBSCHEDULER_PRIORITY DESC, JOBSCHEDULER_ESTIMATE_TIME DESC, JOBSCHEDULER_JOB_ID" if self.longest_first else "JOBSCHEDULER_PRIORITY DESC, JOBSCHEDULER_JOB_ID"
        job_id_filter = ""
        job_id_params: list = []
        if target_job_ids:
            placeholders = ','.join('?' * len(target_job_ids))
            job_id_filter = f"AND JOBSCHEDULER_JOB_ID IN ({placeholders})"
            job_id_params = list(target_job_ids)

        if self.smart_scheduling and available_time > 0:
            select_query = f"""
                SELECT * FROM jobs
                WHERE JOBSCHEDULER_STATUS = 'pending'
                {job_id_filter}
                AND (JOBSCHEDULER_ESTIMATE_TIME * 3600 / ?) <= ?
                AND NOT EXISTS (
                    SELECT 1 FROM job_dependencies d
                    LEFT JOIN jobs dep ON d.depends_on = dep.JOBSCHEDULER_JOB_ID
                    WHERE d.job_id = jobs.JOBSCHEDULER_JOB_ID
                    AND (dep.JOBSCHEDULER_STATUS IS NULL OR dep.JOBSCHEDULER_STATUS != 'done')
                )
                ORDER BY {order_by}
                LIMIT 1
            """
            select_params = job_id_params + [self.speed_factor, available_time]
        else:
            select_query = f"""
                SELECT * FROM jobs
                WHERE JOBSCHEDULER_STATUS = 'pending'
                {job_id_filter}
                AND NOT EXISTS (
                    SELECT 1 FROM job_dependencies d
                    LEFT JOIN jobs dep ON d.depends_on = dep.JOBSCHEDULER_JOB_ID
                    WHERE d.job_id = jobs.JOBSCHEDULER_JOB_ID
                    AND (dep.JOBSCHEDULER_STATUS IS NULL OR dep.JOBSCHEDULER_STATUS != 'done')
                )
                ORDER BY {order_by}
                LIMIT 1
            """
            select_params = job_id_params

        lock_errors = 0
        while lock_errors < max_retries:
            conn = self.connect_db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(select_query, select_params).fetchone()

                if row is None:
                    conn.rollback()
                    return None

                job = dict(row)
                job_id = job['JOBSCHEDULER_JOB_ID']
                conn.execute("""
                    UPDATE jobs
                    SET JOBSCHEDULER_STATUS = 'running',
                        JOBSCHEDULER_STARTED_AT = datetime('now'),
                        JOBSCHEDULER_HEARTBEAT = datetime('now'),
                        JOBSCHEDULER_WORKER_ID = ?
                    WHERE JOBSCHEDULER_JOB_ID = ?
                """, (self.worker_id, job_id))
                conn.commit()
                return job

            except sqlite3.OperationalError as e:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                lock_errors += 1
                backoff = random.uniform(0.5, 1.5) * (2 ** lock_errors)
                logging.warning(f"Database lock conflict ({lock_errors}/{max_retries}): {e}, retry in {backoff:.1f}s")
                time.sleep(backoff)
            finally:
                conn.close()

        logging.warning(f"Database lock conflict after {max_retries} attempts. Giving up.")
        return None

    def has_blocked_pending_jobs(self) -> bool:
        """
        Check if there are pending jobs blocked by running/pending dependencies
        Returns True if jobs are waiting on dependencies, False otherwise
        """
        conn = self.connect_db()

        try:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM jobs j
                WHERE j.JOBSCHEDULER_STATUS = 'pending'
                AND EXISTS (
                    SELECT 1 FROM job_dependencies d
                    JOIN jobs dep ON d.depends_on = dep.JOBSCHEDULER_JOB_ID
                    WHERE d.job_id = j.JOBSCHEDULER_JOB_ID
                    AND dep.JOBSCHEDULER_STATUS IN ('running', 'pending')
                )
            """)

            count = cursor.fetchone()[0]
            return count > 0

        finally:
            conn.close()

    def count_unrunnable_pending_jobs(self) -> int:
        """
        Count pending jobs that have at least one dependency in 'error' state
        or pointing to a non-existent job. These can never become eligible
        without manual intervention.
        """
        conn = self.connect_db()

        try:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM jobs j
                WHERE j.JOBSCHEDULER_STATUS = 'pending'
                AND EXISTS (
                    SELECT 1 FROM job_dependencies d
                    LEFT JOIN jobs dep ON d.depends_on = dep.JOBSCHEDULER_JOB_ID
                    WHERE d.job_id = j.JOBSCHEDULER_JOB_ID
                    AND (dep.JOBSCHEDULER_STATUS IS NULL OR dep.JOBSCHEDULER_STATUS = 'error')
                )
            """)
            return cursor.fetchone()[0]
        finally:
            conn.close()

    def recover_stuck_jobs(self):
        """
        Recover jobs stuck in 'running' state using heartbeat detection.

        Primary: checks file mtime in the .heartbeat directory (one file per job,
        updated every heartbeat_interval seconds via touch). This avoids SQLite
        writes on GPFS which were causing DB corruption under multi-node load.

        Fallback: if no heartbeat file exists, falls back to the DB heartbeat
        column (covers old workers or cases where the file couldn't be created).
        """
        hb_dir = _heartbeat_dir(self.db_path)
        now = time.time()

        # Read running jobs with DB-level staleness info (read-only, no IMMEDIATE lock)
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT JOBSCHEDULER_JOB_ID, JOBSCHEDULER_WORKER_ID,
                       CASE WHEN JOBSCHEDULER_HEARTBEAT IS NULL THEN 1
                            WHEN JOBSCHEDULER_HEARTBEAT < datetime('now', '-' || ? || ' seconds') THEN 1
                            ELSE 0 END as db_stale
                FROM jobs WHERE JOBSCHEDULER_STATUS = 'running'
            """, (self.stale_threshold,))
            running_jobs = cursor.fetchall()
        except sqlite3.OperationalError as e:
            logging.error(f"Failed to query running jobs: {e}")
            return
        finally:
            conn.close()

        stuck_job_ids = []
        for row in running_jobs:
            job_id, worker_id, db_stale = row[0], (row[1] or 'unknown'), row[2]
            hb_file = hb_dir / job_id

            if hb_file.exists():
                age = now - hb_file.stat().st_mtime
                if age > self.stale_threshold:
                    logging.info(f"  Stuck: {job_id} (worker={worker_id}, heartbeat_age={age:.0f}s)")
                    stuck_job_ids.append(job_id)
            else:
                # No heartbeat file — fall back to DB column
                if db_stale:
                    logging.info(f"  Stuck: {job_id} (worker={worker_id}, no heartbeat file, db heartbeat stale)")
                    stuck_job_ids.append(job_id)

        if not stuck_job_ids:
            logging.info("No stuck jobs found (all running jobs have recent heartbeats)")
            return

        logging.warning(f"Found {len(stuck_job_ids)} stuck jobs (threshold={self.stale_threshold}s). Resetting to 'pending'...")

        placeholders = ','.join('?' * len(stuck_job_ids))
        conn = self.connect_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"""
                UPDATE jobs
                SET JOBSCHEDULER_STATUS = 'pending',
                    JOBSCHEDULER_STARTED_AT = NULL,
                    JOBSCHEDULER_HEARTBEAT = NULL,
                    JOBSCHEDULER_WORKER_ID = NULL,
                    JOBSCHEDULER_KILL_REQUESTED = NULL
                WHERE JOBSCHEDULER_JOB_ID IN ({placeholders})
                AND JOBSCHEDULER_STATUS = 'running'
            """, stuck_job_ids)
            conn.commit()
            logging.info(f"✓ Reset {len(stuck_job_ids)} stuck jobs to 'pending'")
        except sqlite3.OperationalError as e:
            logging.error(f"Failed to recover stuck jobs: {e}")
        finally:
            conn.close()

        # Clean up heartbeat files for recovered jobs
        for job_id in stuck_job_ids:
            try:
                (hb_dir / job_id).unlink(missing_ok=True)
                (hb_dir / f"{job_id}.kill").unlink(missing_ok=True)
            except Exception:
                pass

    def mark_job_done(self, job_id: str, status: str, elapsed_time: float,
                     error_message: Optional[str] = None):
        """Mark job as done/error and clear heartbeat/worker_id"""
        conn = self.connect_db()

        try:
            conn.execute("BEGIN IMMEDIATE")

            conn.execute("""
                UPDATE jobs
                SET JOBSCHEDULER_STATUS = ?,
                    JOBSCHEDULER_ELAPSED_TIME = ?,
                    JOBSCHEDULER_FINISHED_AT = datetime('now'),
                    JOBSCHEDULER_ERROR_MESSAGE = ?,
                    JOBSCHEDULER_HEARTBEAT = NULL,
                    JOBSCHEDULER_WORKER_ID = NULL,
                    JOBSCHEDULER_KILL_REQUESTED = NULL
                WHERE JOBSCHEDULER_JOB_ID = ?
            """, (status, elapsed_time, error_message, job_id))

            conn.commit()

        except sqlite3.OperationalError as e:
            logging.error(f"Failed to mark job {job_id} as {status}: {e}")

        finally:
            conn.close()

    def build_command(self, job: Dict) -> List[str]:
        """Build command arguments from job data"""
        # Parse command
        cmd = self.command.split()

        # Get user columns (exclude reserved columns)
        reserved = {
            'JOBSCHEDULER_JOB_ID', 'JOBSCHEDULER_STATUS', 'JOBSCHEDULER_PRIORITY',
            'JOBSCHEDULER_ESTIMATE_TIME', 'JOBSCHEDULER_ELAPSED_TIME',
            'JOBSCHEDULER_CREATED_AT', 'JOBSCHEDULER_STARTED_AT',
            'JOBSCHEDULER_FINISHED_AT', 'JOBSCHEDULER_ERROR_MESSAGE',
            'JOBSCHEDULER_DEPENDS_ON', 'JOBSCHEDULER_HEARTBEAT', 'JOBSCHEDULER_WORKER_ID',
            'JOBSCHEDULER_KILL_REQUESTED'
        }

        if self.named_args:
            # Named arguments mode: --paramA value --paramB value
            for key, value in job.items():
                if key not in reserved and value is not None:
                    cmd.append(f"--{key}")
                    cmd.append(str(value))
        else:
            # Positional arguments mode: value1 value2 value3
            for key, value in job.items():
                if key not in reserved and value is not None:
                    cmd.append(str(value))

        return cmd

    def _heartbeat_worker(self, job_id: str, stop_event: Event, kill_event: Event):
        """Background thread to update job heartbeat and detect kill requests.

        Uses file-based heartbeat (mtime update) instead of SQLite writes to
        avoid DB contention on GPFS under multi-node concurrent access.
        """
        hb_file = _heartbeat_dir(self.db_path) / job_id
        kill_file = _heartbeat_dir(self.db_path) / f"{job_id}.kill"

        while not stop_event.is_set():
            try:
                hb_file.touch()
            except Exception as e:
                logging.warning(f"Failed to update heartbeat for job {job_id}: {e}")

            try:
                if kill_file.exists():
                    logging.warning(f"Kill requested for job {job_id} (sentinel file detected)")
                    kill_event.set()
            except Exception as e:
                logging.warning(f"Failed to check kill request for job {job_id}: {e}")

            stop_event.wait(self.heartbeat_interval)

        # Clean up heartbeat file when the thread stops normally
        try:
            hb_file.unlink(missing_ok=True)
        except Exception:
            pass

    def _terminate_process_group(self, process, job_id: str, timeout: float = 5.0) -> None:
        """Send SIGTERM to the process group, escalate to SIGKILL after `timeout` seconds.

        Requires the process to have been started with start_new_session=True so that
        shell wrappers (e.g. bash → python) share a dedicated PGID and are all terminated.
        """
        try:
            pgid = os.getpgid(process.pid)
        except ProcessLookupError:
            return  # already dead
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logging.warning(f"Job {job_id} did not terminate gracefully. SIGKILL to pgid {pgid}.")
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logging.error(f"Job {job_id} pgid {pgid} did not respond to SIGKILL.")

    def run_job(self, job: Dict, max_time: float, worker_id: int = 0) -> Tuple[int, float, Optional[str]]:
        """
        Execute a single job

        Returns: (return_code, elapsed_time, error_message)
        """
        job_id = job['JOBSCHEDULER_JOB_ID']
        cmd = self.build_command(job)

        logging.info(f"Job {job_id} starting: {' '.join(cmd)}")

        start_time = time.time()
        error_message = None

        # Create heartbeat file so the job is immediately visible to recover_stuck_jobs
        hb_dir = _ensure_heartbeat_dir(self.db_path)
        hb_file = hb_dir / job_id
        try:
            hb_file.write_text(self.worker_id)
        except Exception as e:
            logging.warning(f"Failed to create heartbeat file for {job_id}: {e}")

        # Start heartbeat thread
        heartbeat_stop = Event()
        kill_event = Event()
        heartbeat_thread = Thread(target=self._heartbeat_worker, args=(job_id, heartbeat_stop, kill_event))
        heartbeat_thread.daemon = True
        heartbeat_thread.start()

        try:
            # Start subprocess
            env = os.environ.copy()
            env['JOBSCHEDULER_PARALLEL_WORKER'] = str(worker_id)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True,
            )

            # Monitor output
            stdout_done = Event()
            stderr_done = Event()

            def log_output(pipe, prefix, done_event):
                try:
                    for line in pipe:
                        if shutdown_event.is_set():
                            break
                        logging.info(f"Job {job_id} {prefix}: {line.rstrip()}")
                finally:
                    done_event.set()

            stdout_thread = Thread(target=log_output, args=(process.stdout, "stdout", stdout_done))
            stderr_thread = Thread(target=log_output, args=(process.stderr, "stderr", stderr_done))

            stdout_thread.daemon = True
            stderr_thread.daemon = True
            stdout_thread.start()
            stderr_thread.start()

            # Wait for completion with timeout
            end_time = start_time + max_time
            return_code = None

            while process.poll() is None and not shutdown_event.is_set() and not kill_event.is_set():
                if time.time() >= end_time:
                    logging.warning(f"Job {job_id} exceeded maximum runtime. Terminating.")
                    self._terminate_process_group(process, job_id)
                    return_code = -2
                    error_message = "Timeout: exceeded maximum runtime"
                    break
                time.sleep(0.1)

            if return_code is None:
                if kill_event.is_set():
                    logging.warning(f"Job {job_id} force kill requested. Terminating.")
                    self._terminate_process_group(process, job_id)
                    return_code = -3
                    error_message = "Force killed by user request"
                elif shutdown_event.is_set():
                    self._terminate_process_group(process, job_id)
                    return_code = -2
                    error_message = "Interrupted by shutdown signal"
                else:
                    return_code = process.returncode

            elapsed_time = time.time() - start_time

            # Wait for output threads
            stdout_done.wait(timeout=2)
            stderr_done.wait(timeout=2)

            if return_code != 0 and error_message is None:
                error_message = f"Process exited with code {return_code}"

            logging.info(f"Job {job_id} completed with return code {return_code} in {elapsed_time:.2f}s")
            return return_code, elapsed_time, error_message

        except Exception as e:
            elapsed_time = time.time() - start_time
            error_message = f"Exception: {str(e)}"
            logging.error(f"Job {job_id} failed: {error_message}")
            return -1, elapsed_time, error_message

        finally:
            # Stop heartbeat thread (it will delete hb_file on exit)
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
            # Ensure cleanup even if the thread didn't exit cleanly
            try:
                (hb_dir / job_id).unlink(missing_ok=True)
                (hb_dir / f"{job_id}.kill").unlink(missing_ok=True)
            except Exception:
                pass

    def _cleanup_heartbeat_files(self):
        """Remove heartbeat files for jobs that are no longer in 'running' state.

        Called at startup to clean up files left by previously crashed workers.
        """
        hb_dir = _heartbeat_dir(self.db_path)
        if not hb_dir.exists():
            return

        conn = self.connect_db()
        try:
            cursor = conn.execute(
                "SELECT JOBSCHEDULER_JOB_ID FROM jobs WHERE JOBSCHEDULER_STATUS = 'running'"
            )
            running_ids = {row[0] for row in cursor.fetchall()}
        except Exception:
            return
        finally:
            conn.close()

        for f in hb_dir.iterdir():
            job_id = f.stem if f.suffix == '.kill' else f.name
            if job_id not in running_ids:
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass

    def run_scheduling_worker(self, worker_id: int = 0):
        """Single worker scheduling loop"""
        self.start_time = time.time()
        last_recovery_time = 0.0

        # Track remaining target jobs (thread-safe copy; each worker operates on its own copy)
        remaining_targets = list(self.target_jobs) if self.target_jobs else None

        while not shutdown_event.is_set():
            elapsed = time.time() - self.start_time

            if elapsed >= self.max_runtime:
                logging.info("Reached maximum total runtime. Stopping.")
                break

            available_time = self.max_runtime - elapsed - self.margin_time

            if available_time <= 0:
                logging.info("Not enough available time remaining (considering margin). Stopping.")
                break

            # Periodically recover stuck jobs (worker 0 only to avoid contention)
            now = time.time()
            if worker_id == 0 and now - last_recovery_time >= self.stale_threshold:
                self.recover_stuck_jobs()
                last_recovery_time = now

            # Determine job ID filter for this iteration
            if remaining_targets is not None:
                if len(remaining_targets) == 0:
                    # All specified jobs have been picked up
                    if self.jobs_only:
                        if worker_id == 0 or self.parallel == 1:
                            logging.info(f"Worker {worker_id}: All specified jobs dispatched (--jobs-only). Stopping.")
                        break
                    # Fall through to normal scheduling
                    target_filter = None
                else:
                    target_filter = remaining_targets
            else:
                target_filter = None

            # Get next job
            job = self.get_pending_job(available_time, target_job_ids=target_filter)

            if job is None:
                if target_filter is not None:
                    # Specified jobs not yet available (may be blocked by dependencies or time constraint)
                    if self.has_blocked_pending_jobs():
                        if worker_id == 0 or self.parallel == 1:
                            logging.info(f"Worker {worker_id}: Target jobs not ready. Waiting {self.dep_wait_interval}s for dependencies...")
                        time.sleep(self.dep_wait_interval)
                        continue
                    else:
                        # Remaining targets are not pending (already done/error or non-existent) - skip them
                        if worker_id == 0 or self.parallel == 1:
                            logging.info(f"Worker {worker_id}: No ready target jobs. Skipping remaining: {remaining_targets}")
                        remaining_targets = []
                        continue
                # Check if there are pending jobs waiting on dependencies
                elif self.has_blocked_pending_jobs():
                    if worker_id == 0 or self.parallel == 1:
                        logging.info(f"Worker {worker_id}: No ready jobs. Waiting {self.dep_wait_interval}s for dependencies...")
                    time.sleep(self.dep_wait_interval)
                    continue
                else:
                    # Sanity check: confirm there are truly no pending jobs before stopping.
                    # get_pending_job can return None due to repeated lock contention,
                    # not only because the queue is empty.
                    conn = self.connect_db()
                    try:
                        remaining = conn.execute(
                            "SELECT COUNT(*) FROM jobs WHERE JOBSCHEDULER_STATUS = 'pending'"
                        ).fetchone()[0]
                    finally:
                        conn.close()
                    if remaining > 0:
                        unrunnable = self.count_unrunnable_pending_jobs()
                        if unrunnable >= remaining:
                            if worker_id == 0 or self.parallel == 1:
                                logging.warning(f"Worker {worker_id}: {unrunnable} pending jobs are blocked by error/missing dependencies and cannot run. Stopping.")
                            break
                        logging.warning(f"Worker {worker_id}: get_pending_job returned None but {remaining} pending jobs exist. Retrying...")
                        time.sleep(random.uniform(1, 3))
                        continue
                    if worker_id == 0 or self.parallel == 1:
                        logging.info(f"Worker {worker_id}: No suitable jobs available. Stopping.")
                    break

            # Remove dispatched job from remaining targets
            if remaining_targets is not None:
                dispatched_id = job['JOBSCHEDULER_JOB_ID']
                if dispatched_id in remaining_targets:
                    remaining_targets.remove(dispatched_id)

            # Run job
            job_id = job['JOBSCHEDULER_JOB_ID']
            return_code, elapsed_time, error_message = self.run_job(job, available_time, worker_id)

            # Update job status
            if return_code == 0 and not shutdown_event.is_set():
                self.mark_job_done(job_id, 'done', elapsed_time)
                self.jobs_completed += 1
            elif return_code == -2 or shutdown_event.is_set():
                # Timeout or interrupted - mark as pending for retry
                self.mark_job_done(job_id, 'pending', elapsed_time, error_message)
            elif return_code == -3:
                # Force killed by user request
                logging.warning(f"Job {job_id} was force killed. Marking as error.")
                self.mark_job_done(job_id, 'error', elapsed_time, error_message)
                self.jobs_failed += 1
            else:
                self.mark_job_done(job_id, 'error', elapsed_time, error_message)
                self.jobs_failed += 1

    def run_scheduling(self):
        """Main scheduling loop - manages parallel workers if needed"""
        logging.info("="*60)
        logging.info("Job Scheduler v2 starting")
        logging.info("="*60)
        logging.info(f"Database: {self.db_path}")
        logging.info(f"Worker ID: {self.worker_id}")
        logging.info(f"Command: {self.command}")
        logging.info(f"Max runtime: {self.max_runtime}s")
        logging.info(f"Margin time: {self.margin_time}s")
        logging.info(f"Speed factor: {self.speed_factor}")
        logging.info(f"Smart scheduling: {self.smart_scheduling}")
        logging.info(f"Longest first: {self.longest_first}")
        logging.info(f"Named args: {self.named_args}")
        logging.info(f"Parallel: {self.parallel}")
        logging.info(f"Dependency wait interval: {self.dep_wait_interval}s")
        logging.info(f"Heartbeat interval: {self.heartbeat_interval}s")
        logging.info(f"Stale threshold: {self.stale_threshold}s")
        logging.info("="*60)

        # Ensure heartbeat directory exists
        _ensure_heartbeat_dir(self.db_path)

        # CRITICAL: Recover stuck jobs before starting, then clean up stale files
        logging.info("Checking for stuck jobs...")
        self.recover_stuck_jobs()
        self._cleanup_heartbeat_files()

        if self.parallel > 1:
            # Parallel mode: spawn multiple worker processes
            logging.info(f"Starting {self.parallel} parallel workers...")
            workers = []

            for i in range(self.parallel):
                p = Process(target=self.run_scheduling_worker, args=(i,))
                p.start()
                workers.append(p)
                logging.info(f"Worker {i} started (PID: {p.pid})")

            # Wait for all workers to complete
            for i, p in enumerate(workers):
                p.join()
                logging.info(f"Worker {i} finished")

        else:
            # Sequential mode: single worker
            self.run_scheduling_worker(worker_id=0)

        # Final summary
        total_time = time.time() - self.start_time if self.start_time else 0
        logging.info("="*60)
        logging.info("Job Scheduler v2 finished")
        logging.info(f"Total runtime: {total_time:.2f}s")
        if self.parallel == 1:
            logging.info(f"Jobs completed: {self.jobs_completed}")
            logging.info(f"Jobs failed: {self.jobs_failed}")
        else:
            logging.info("(Use progress_viewer.py to see detailed statistics)")
        logging.info("="*60)


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Job Scheduler v2 - SQLite-based parallel job scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage with positional arguments
  job_scheduler jobs.db "bash run.sh"

  # Python script with named arguments
  job_scheduler jobs.db "python run.py" --named-args

  # With time constraints
  job_scheduler jobs.db "bash run.sh" --max-runtime 3600 --margin-time 300

  # Parallel execution
  job_scheduler jobs.db "bash run.sh" --parallel 4

  # Run specific jobs first, then continue with normal scheduling
  job_scheduler jobs.db "bash run.sh" --jobs job_00000001,job_00000002

  # Run only the specified jobs and stop
  job_scheduler jobs.db "bash run.sh" --jobs job_00000001,job_00000002 --jobs-only
        """
    )

    parser.add_argument('db_file', help='SQLite database file path')
    parser.add_argument('command', help='Command to execute for each job')

    parser.add_argument('--max-runtime', type=int, default=86400,
                       help='Maximum total runtime in seconds (default: 86400 = 24h)')
    parser.add_argument('--margin-time', type=int, default=0,
                       help='Margin time in seconds (default: 0)')
    parser.add_argument('--speed-factor', type=float, default=1.0,
                       help='Speed factor for time estimation (default: 1.0)')
    parser.add_argument('--smart-scheduling', type=lambda x: x.lower() != 'false', default=True,
                       help='Enable smart scheduling based on estimate_time (default: true)')
    parser.add_argument('--longest-first', action='store_true',
                       help='Schedule longest estimated jobs first within same priority (LPT strategy, default: false)')
    parser.add_argument('--named-args', action='store_true',
                       help='Pass arguments as --key value instead of positional')
    parser.add_argument('--parallel', type=int, default=1,
                       help='Number of parallel jobs (default: 1)')
    parser.add_argument('--dep-wait-interval', type=int, default=30,
                       help='Wait interval in seconds when jobs are blocked by dependencies (default: 30)')
    parser.add_argument('--heartbeat-interval', type=int, default=30,
                       help='Heartbeat update interval in seconds (default: 30)')
    parser.add_argument('--stale-threshold', type=int, default=120,
                       help='Threshold in seconds to consider a job stale/stuck (default: 120)')
    parser.add_argument('--jobs',
                       help='Comma-separated job IDs to prioritize (e.g. job_00000001,job_00000002). '
                            'These jobs are run first; remaining pending jobs follow unless --jobs-only is set.')
    parser.add_argument('--jobs-only', action='store_true',
                       help='Only run the jobs specified by --jobs, then stop. Requires --jobs.')
    parser.add_argument('--log-stderr', action='store_true',
                       help='Output logs to stderr instead of stdout (default: stdout)')

    args = parser.parse_args()

    # Setup logging based on --log-stderr flag
    setup_logging(use_stderr=args.log_stderr)

    # Validate
    if not os.path.exists(args.db_file):
        logging.error(f"Database file not found: {args.db_file}")
        sys.exit(1)

    if args.jobs_only and not args.jobs:
        logging.error("--jobs-only requires --jobs to be specified.")
        sys.exit(1)

    target_jobs = [j.strip() for j in args.jobs.split(',') if j.strip()] if args.jobs else None

    # Create scheduler
    scheduler = JobScheduler(
        db_path=args.db_file,
        command=args.command,
        max_runtime=args.max_runtime,
        margin_time=args.margin_time,
        speed_factor=args.speed_factor,
        smart_scheduling=args.smart_scheduling,
        longest_first=args.longest_first,
        named_args=args.named_args,
        parallel=args.parallel,
        dep_wait_interval=args.dep_wait_interval,
        heartbeat_interval=args.heartbeat_interval,
        stale_threshold=args.stale_threshold,
        target_jobs=target_jobs,
        jobs_only=args.jobs_only,
    )

    # Run
    try:
        scheduler.run_scheduling()
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
