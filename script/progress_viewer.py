#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Progress Viewer for Job Scheduler v2

Real-time progress monitoring with statistics and job listing.
"""

import sqlite3
import argparse
import time
import sys
from datetime import datetime
from typing import Dict, List, Optional


class ProgressViewer:
    """Progress viewer for job database"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def connect_db(self) -> sqlite3.Connection:
        """Create database connection"""
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def get_stats(self) -> Dict[str, int]:
        """Get job statistics"""
        conn = self.connect_db()

        try:
            cursor = conn.execute("""
                SELECT JOBSCHEDULER_STATUS, COUNT(*) as count
                FROM jobs
                GROUP BY JOBSCHEDULER_STATUS
            """)

            stats = {row[0]: row[1] for row in cursor.fetchall()}

            # Get total
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            stats['total'] = total

            # Check if job_dependencies table exists
            cursor = conn.execute("""
                SELECT name FROM sqlite_master WHERE type='table' AND name='job_dependencies'
            """)
            has_deps = cursor.fetchone() is not None

            if has_deps:
                # Count pending jobs that are ready (no blocking dependencies)
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM jobs j
                    WHERE j.JOBSCHEDULER_STATUS = 'pending'
                    AND NOT EXISTS (
                        SELECT 1 FROM job_dependencies d
                        LEFT JOIN jobs dep ON d.depends_on = dep.JOBSCHEDULER_JOB_ID
                        WHERE d.job_id = j.JOBSCHEDULER_JOB_ID
                        AND (dep.JOBSCHEDULER_STATUS IS NULL OR dep.JOBSCHEDULER_STATUS != 'done')
                    )
                """)
                stats['pending_ready'] = cursor.fetchone()[0]

                # Count pending jobs waiting on running/pending dependencies
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
                stats['pending_waiting'] = cursor.fetchone()[0]

                # Count pending jobs blocked by error or non-existent dependencies
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
                stats['pending_blocked'] = cursor.fetchone()[0]

            return stats

        finally:
            conn.close()

    def get_running_jobs(self) -> List[Dict]:
        """Get currently running jobs"""
        conn = self.connect_db()

        try:
            cursor = conn.execute("""
                SELECT JOBSCHEDULER_JOB_ID,
                       JOBSCHEDULER_STARTED_AT,
                       JOBSCHEDULER_PRIORITY,
                       JOBSCHEDULER_WORKER_ID,
                       JOBSCHEDULER_HEARTBEAT
                FROM jobs
                WHERE JOBSCHEDULER_STATUS = 'running'
                ORDER BY JOBSCHEDULER_STARTED_AT DESC
            """)

            return [dict(row) for row in cursor.fetchall()]

        finally:
            conn.close()

    def get_recent_completed(self, limit: int = 5) -> List[Dict]:
        """Get recently completed jobs"""
        conn = self.connect_db()

        try:
            cursor = conn.execute("""
                SELECT JOBSCHEDULER_JOB_ID,
                       JOBSCHEDULER_STATUS,
                       JOBSCHEDULER_ELAPSED_TIME,
                       JOBSCHEDULER_FINISHED_AT
                FROM jobs
                WHERE JOBSCHEDULER_STATUS IN ('done', 'error')
                ORDER BY JOBSCHEDULER_FINISHED_AT DESC
                LIMIT ?
            """, (limit,))

            return [dict(row) for row in cursor.fetchall()]

        finally:
            conn.close()

    def get_estimated_time_remaining(self) -> Optional[float]:
        """Estimate time remaining based on completed jobs"""
        conn = self.connect_db()

        try:
            # Get average elapsed time of completed jobs
            cursor = conn.execute("""
                SELECT AVG(JOBSCHEDULER_ELAPSED_TIME) as avg_time
                FROM jobs
                WHERE JOBSCHEDULER_STATUS = 'done'
                AND JOBSCHEDULER_ELAPSED_TIME IS NOT NULL
            """)

            row = cursor.fetchone()
            avg_time = row[0] if row[0] else None

            if avg_time is None:
                return None

            # Count pending jobs
            pending_count = conn.execute("""
                SELECT COUNT(*) FROM jobs WHERE JOBSCHEDULER_STATUS = 'pending'
            """).fetchone()[0]

            # Estimate remaining time
            return avg_time * pending_count

        finally:
            conn.close()

    def print_progress(self, clear_screen: bool = False):
        """Print current progress"""
        if clear_screen:
            # Clear screen (ANSI escape code)
            print("\033[2J\033[H", end="")

        print("="*70)
        print(f"Job Scheduler v2 - Progress Viewer")
        print(f"Database: {self.db_path}")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)

        # Statistics
        stats = self.get_stats()
        total = stats.get('total', 0)
        pending = stats.get('pending', 0)
        running = stats.get('running', 0)
        done = stats.get('done', 0)
        error = stats.get('error', 0)

        completed = done + error
        completion_rate = (completed / total * 100) if total > 0 else 0

        print(f"\nStatistics:")
        print(f"  Total jobs:    {total}")

        # Show pending breakdown if dependency info is available
        if 'pending_ready' in stats:
            pending_ready = stats.get('pending_ready', 0)
            pending_waiting = stats.get('pending_waiting', 0)
            pending_blocked = stats.get('pending_blocked', 0)
            print(f"  Pending:       {pending:4d} ({pending/total*100:.1f}%)" if total > 0 else "  Pending:       0")
            print(f"    - Ready:     {pending_ready:4d}")
            print(f"    - Waiting:   {pending_waiting:4d}")
            if pending_blocked > 0:
                print(f"    - Blocked:   {pending_blocked:4d}")
        else:
            print(f"  Pending:       {pending:4d} ({pending/total*100:.1f}%)" if total > 0 else "  Pending:       0")

        print(f"  Running:       {running:4d}")
        print(f"  Completed:     {done:4d} ({done/total*100:.1f}%)" if total > 0 else "  Completed:     0")
        print(f"  Failed:        {error:4d} ({error/total*100:.1f}%)" if total > 0 else "  Failed:        0")
        print(f"  Progress:      [{self._progress_bar(completion_rate, 40)}] {completion_rate:.1f}%")

        # Estimated time remaining
        est_time = self.get_estimated_time_remaining()
        if est_time is not None:
            minutes = int(est_time / 60)
            seconds = int(est_time % 60)
            print(f"  Est. remaining: ~{minutes}m {seconds}s")

        # Running jobs
        running_jobs = self.get_running_jobs()
        if running_jobs:
            print(f"\nCurrently Running ({len(running_jobs)} jobs):")
            for job in running_jobs[:10]:  # Show max 10
                job_id = job['JOBSCHEDULER_JOB_ID']
                started = job['JOBSCHEDULER_STARTED_AT']
                priority = job['JOBSCHEDULER_PRIORITY']
                worker_id = job.get('JOBSCHEDULER_WORKER_ID', 'unknown')
                heartbeat = job.get('JOBSCHEDULER_HEARTBEAT')

                # Calculate heartbeat age
                if heartbeat:
                    try:
                        heartbeat_dt = datetime.fromisoformat(heartbeat)
                        now = datetime.utcnow()
                        age_seconds = int((now - heartbeat_dt).total_seconds())
                        heartbeat_info = f"heartbeat={age_seconds}s ago"
                    except:
                        heartbeat_info = f"heartbeat={heartbeat}"
                else:
                    heartbeat_info = "heartbeat=never"

                print(f"  • {job_id} (worker={worker_id}, {heartbeat_info}, priority={priority})")

            if len(running_jobs) > 10:
                print(f"  ... and {len(running_jobs) - 10} more")

        # Recent completed
        recent = self.get_recent_completed(5)
        if recent:
            print(f"\nRecently Completed:")
            for job in recent:
                job_id = job['JOBSCHEDULER_JOB_ID']
                status = job['JOBSCHEDULER_STATUS']
                elapsed = job['JOBSCHEDULER_ELAPSED_TIME']
                status_icon = "✓" if status == 'done' else "✗"
                elapsed_str = f"({elapsed:.2f}s)" if elapsed is not None else "(no time)"
                print(f"  {status_icon} {job_id} {elapsed_str}")

        print("\n" + "="*70)

    def _progress_bar(self, percentage: float, width: int = 40) -> str:
        """Generate progress bar string"""
        filled = int(width * percentage / 100)
        bar = "█" * filled + "░" * (width - filled)
        return bar

    def watch_mode(self, interval: int = 2):
        """Continuous monitoring mode"""
        try:
            while True:
                self.print_progress(clear_screen=True)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\nMonitoring stopped.")


def main():
    """CLI interface"""
    parser = argparse.ArgumentParser(
        description="Progress Viewer for Job Scheduler v2"
    )
    parser.add_argument('db_file', help='SQLite database file path')
    parser.add_argument('--watch', action='store_true',
                       help='Continuous monitoring mode (updates every 2 seconds)')
    parser.add_argument('--interval', type=int, default=2,
                       help='Update interval in seconds for watch mode (default: 2)')

    args = parser.parse_args()

    viewer = ProgressViewer(args.db_file)

    if args.watch:
        viewer.watch_mode(interval=args.interval)
    else:
        viewer.print_progress()


if __name__ == "__main__":
    main()
