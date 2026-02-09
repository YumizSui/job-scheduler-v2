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
import logging
from typing import Optional, Dict, List, Tuple
from threading import Event, Thread
from multiprocessing import Process, Value
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Global shutdown event
shutdown_event = Event()


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
        self.named_args = kwargs.get('named_args', False)
        self.parallel = kwargs.get('parallel', 1)

        self.start_time = None
        self.jobs_completed = 0
        self.jobs_failed = 0

    def connect_db(self) -> sqlite3.Connection:
        """Create database connection with optimized settings"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def get_pending_job(self, available_time: float) -> Optional[Dict]:
        """
        Get next pending job based on priority and estimate_time

        Returns job dict or None if no suitable job available
        """
        conn = self.connect_db()

        try:
            # Start transaction with immediate lock
            conn.execute("BEGIN IMMEDIATE")

            # Build query based on smart scheduling
            if self.smart_scheduling and available_time > 0:
                # Only select jobs that can complete within available time
                query = """
                    SELECT * FROM jobs
                    WHERE JOBSCHEDULER_STATUS = 'pending'
                    AND (JOBSCHEDULER_ESTIMATE_TIME * 3600 / ?) <= ?
                    ORDER BY JOBSCHEDULER_PRIORITY DESC, JOBSCHEDULER_JOB_ID
                    LIMIT 1
                """
                cursor = conn.execute(query, (self.speed_factor, available_time))
            else:
                # Simple priority-based selection
                query = """
                    SELECT * FROM jobs
                    WHERE JOBSCHEDULER_STATUS = 'pending'
                    ORDER BY JOBSCHEDULER_PRIORITY DESC, JOBSCHEDULER_JOB_ID
                    LIMIT 1
                """
                cursor = conn.execute(query)

            row = cursor.fetchone()

            if row is None:
                conn.rollback()
                return None

            # Convert to dict
            job = dict(row)
            job_id = job['JOBSCHEDULER_JOB_ID']

            # Mark as running
            conn.execute("""
                UPDATE jobs
                SET JOBSCHEDULER_STATUS = 'running',
                    JOBSCHEDULER_STARTED_AT = datetime('now')
                WHERE JOBSCHEDULER_JOB_ID = ?
            """, (job_id,))

            conn.commit()
            return job

        except sqlite3.OperationalError as e:
            logging.warning(f"Database lock conflict: {e}")
            conn.rollback()
            return None

        finally:
            conn.close()

    def recover_stuck_jobs(self):
        """
        Recover jobs stuck in 'running' state.
        This happens when scheduler is killed/interrupted.
        Resets all 'running' jobs to 'pending'.
        """
        conn = self.connect_db()

        try:
            conn.execute("BEGIN IMMEDIATE")

            # Count stuck jobs
            cursor = conn.execute("""
                SELECT COUNT(*) FROM jobs WHERE JOBSCHEDULER_STATUS = 'running'
            """)
            stuck_count = cursor.fetchone()[0]

            if stuck_count > 0:
                logging.warning(f"Found {stuck_count} stuck jobs in 'running' state. Resetting to 'pending'...")

                # Reset running jobs to pending
                conn.execute("""
                    UPDATE jobs
                    SET JOBSCHEDULER_STATUS = 'pending',
                        JOBSCHEDULER_STARTED_AT = NULL
                    WHERE JOBSCHEDULER_STATUS = 'running'
                """)

                conn.commit()
                logging.info(f"✓ Reset {stuck_count} stuck jobs to 'pending'")
            else:
                logging.info("No stuck jobs found")

        except sqlite3.OperationalError as e:
            logging.error(f"Failed to recover stuck jobs: {e}")

        finally:
            conn.close()

    def mark_job_done(self, job_id: str, status: str, elapsed_time: float,
                     error_message: Optional[str] = None):
        """Mark job as done/error"""
        conn = self.connect_db()

        try:
            conn.execute("BEGIN IMMEDIATE")

            conn.execute("""
                UPDATE jobs
                SET JOBSCHEDULER_STATUS = ?,
                    JOBSCHEDULER_ELAPSED_TIME = ?,
                    JOBSCHEDULER_FINISHED_AT = datetime('now'),
                    JOBSCHEDULER_ERROR_MESSAGE = ?
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

        # Auto-add bash for .sh files if not already specified
        if len(cmd) == 1 and cmd[0].endswith('.sh') and not cmd[0].startswith('bash'):
            cmd = ['bash'] + cmd

        # Get user columns (exclude reserved columns)
        reserved = {
            'JOBSCHEDULER_JOB_ID', 'JOBSCHEDULER_STATUS', 'JOBSCHEDULER_PRIORITY',
            'JOBSCHEDULER_ESTIMATE_TIME', 'JOBSCHEDULER_ELAPSED_TIME',
            'JOBSCHEDULER_CREATED_AT', 'JOBSCHEDULER_STARTED_AT',
            'JOBSCHEDULER_FINISHED_AT', 'JOBSCHEDULER_ERROR_MESSAGE'
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

    def run_job(self, job: Dict, max_time: float) -> Tuple[int, float, Optional[str]]:
        """
        Execute a single job

        Returns: (return_code, elapsed_time, error_message)
        """
        job_id = job['JOBSCHEDULER_JOB_ID']
        cmd = self.build_command(job)

        logging.info(f"Job {job_id} starting: {' '.join(cmd)}")

        start_time = time.time()
        error_message = None

        try:
            # Start subprocess
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
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

            while process.poll() is None and not shutdown_event.is_set():
                if time.time() >= end_time:
                    logging.warning(f"Job {job_id} exceeded maximum runtime. Terminating.")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logging.warning(f"Job {job_id} did not terminate gracefully. Killing.")
                        process.kill()
                    return_code = -2
                    error_message = "Timeout: exceeded maximum runtime"
                    break
                time.sleep(0.1)

            if return_code is None:
                if shutdown_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
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

    def run_scheduling_worker(self, worker_id: int = 0):
        """Single worker scheduling loop"""
        self.start_time = time.time()

        while not shutdown_event.is_set():
            elapsed = time.time() - self.start_time

            if elapsed >= self.max_runtime:
                logging.info("Reached maximum total runtime. Stopping.")
                break

            available_time = self.max_runtime - elapsed - self.margin_time

            if available_time <= 0:
                logging.info("Not enough available time remaining (considering margin). Stopping.")
                break

            # Get next job
            job = self.get_pending_job(available_time)

            if job is None:
                if worker_id == 0 or self.parallel == 1:
                    logging.info(f"Worker {worker_id}: No suitable jobs available. Stopping.")
                break

            # Run job
            job_id = job['JOBSCHEDULER_JOB_ID']
            return_code, elapsed_time, error_message = self.run_job(job, available_time)

            # Update job status
            if not shutdown_event.is_set():
                if return_code == 0:
                    self.mark_job_done(job_id, 'done', elapsed_time)
                    self.jobs_completed += 1
                elif return_code == -2:
                    # Timeout or interrupted - mark as pending for retry
                    self.mark_job_done(job_id, 'pending', elapsed_time, error_message)
                else:
                    self.mark_job_done(job_id, 'error', elapsed_time, error_message)
                    self.jobs_failed += 1

    def run_scheduling(self):
        """Main scheduling loop - manages parallel workers if needed"""
        logging.info("="*60)
        logging.info("Job Scheduler v2 starting")
        logging.info("="*60)
        logging.info(f"Database: {self.db_path}")
        logging.info(f"Command: {self.command}")
        logging.info(f"Max runtime: {self.max_runtime}s")
        logging.info(f"Margin time: {self.margin_time}s")
        logging.info(f"Speed factor: {self.speed_factor}")
        logging.info(f"Smart scheduling: {self.smart_scheduling}")
        logging.info(f"Named args: {self.named_args}")
        logging.info(f"Parallel: {self.parallel}")
        logging.info("="*60)

        # CRITICAL: Recover stuck jobs before starting
        logging.info("Checking for stuck jobs...")
        self.recover_stuck_jobs()

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
    parser.add_argument('--named-args', action='store_true',
                       help='Pass arguments as --key value instead of positional')
    parser.add_argument('--parallel', type=int, default=1,
                       help='Number of parallel jobs (default: 1, not yet implemented)')

    args = parser.parse_args()

    # Validate
    if not os.path.exists(args.db_file):
        logging.error(f"Database file not found: {args.db_file}")
        sys.exit(1)

    # Parallel mode is now implemented!

    # Create scheduler
    scheduler = JobScheduler(
        db_path=args.db_file,
        command=args.command,
        max_runtime=args.max_runtime,
        margin_time=args.margin_time,
        speed_factor=args.speed_factor,
        smart_scheduling=args.smart_scheduling,
        named_args=args.named_args,
        parallel=args.parallel,
    )

    # Run
    try:
        scheduler.run_scheduling()
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
