#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Database utility for job-runner v2

Provides functions to:
- Import CSV to SQLite
- Export SQLite to CSV
- Manage job database schema
"""

import sqlite3
import csv
import sys
import time
import argparse
from typing import List, Dict, Optional
from pathlib import Path


class JobDatabase:
    """SQLite database manager for job scheduling"""

    RESERVED_COLUMNS = {
        'JOBSCHEDULER_JOB_ID',
        'JOBSCHEDULER_STATUS',
        'JOBSCHEDULER_PRIORITY',
        'JOBSCHEDULER_ESTIMATE_TIME',
        'JOBSCHEDULER_ELAPSED_TIME',
        'JOBSCHEDULER_CREATED_AT',
        'JOBSCHEDULER_STARTED_AT',
        'JOBSCHEDULER_FINISHED_AT',
        'JOBSCHEDULER_ERROR_MESSAGE',
        'JOBSCHEDULER_DEPENDS_ON',
        'JOBSCHEDULER_HEARTBEAT',
        'JOBSCHEDULER_WORKER_ID',
        'JOBSCHEDULER_KILL_REQUESTED',
    }

    def __init__(self, db_path: str, read_only: bool = False):
        """Initialize database connection"""
        self.db_path = db_path
        self.read_only = read_only
        self.conn = None

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()

    def connect(self):
        """Connect to database with optimized settings"""
        if self.read_only:
            uri = f"file:{Path(self.db_path).resolve()}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, timeout=5)
        else:
            self.conn = sqlite3.connect(self.db_path, timeout=30)
            self.conn.execute("PRAGMA journal_mode=DELETE")
            self.conn.execute("PRAGMA busy_timeout=30000")  # 30 seconds
            self.conn.execute("PRAGMA synchronous=NORMAL")  # Balance between safety and speed
        self.conn.row_factory = sqlite3.Row  # Allow dict-like access

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def create_schema(self, user_columns: List[str] = None):
        """Create jobs table with dynamic user columns"""
        user_columns = user_columns or []

        # Build column definitions
        columns = [
            "JOBSCHEDULER_JOB_ID TEXT PRIMARY KEY",
            "JOBSCHEDULER_STATUS TEXT NOT NULL DEFAULT 'pending'",
            "JOBSCHEDULER_PRIORITY INTEGER DEFAULT 0",
            "JOBSCHEDULER_ESTIMATE_TIME REAL DEFAULT 0",
            "JOBSCHEDULER_ELAPSED_TIME REAL",
            "JOBSCHEDULER_CREATED_AT TEXT DEFAULT (datetime('now'))",
            "JOBSCHEDULER_STARTED_AT TEXT",
            "JOBSCHEDULER_FINISHED_AT TEXT",
            "JOBSCHEDULER_ERROR_MESSAGE TEXT",
            "JOBSCHEDULER_DEPENDS_ON TEXT",
            "JOBSCHEDULER_HEARTBEAT TEXT",
            "JOBSCHEDULER_WORKER_ID TEXT",
            "JOBSCHEDULER_KILL_REQUESTED TEXT",
        ]

        # Add user columns
        for col in user_columns:
            if col not in self.RESERVED_COLUMNS:
                columns.append(f"{col} TEXT")

        # Create table
        create_sql = f"CREATE TABLE IF NOT EXISTS jobs ({', '.join(columns)})"
        self.conn.execute(create_sql)

        # Create indexes
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_priority
            ON jobs(JOBSCHEDULER_STATUS, JOBSCHEDULER_PRIORITY DESC)
        """)

        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_estimate
            ON jobs(JOBSCHEDULER_STATUS, JOBSCHEDULER_ESTIMATE_TIME)
        """)

        # Create job_dependencies table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS job_dependencies (
                job_id TEXT NOT NULL,
                depends_on TEXT NOT NULL,
                PRIMARY KEY (job_id, depends_on)
            )
        """)

        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dep_job_id ON job_dependencies(job_id)
        """)

        self.conn.commit()

    def import_csv(self, csv_path: str, reset_status: bool = True):
        """Import jobs from CSV file"""
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            print("Warning: CSV file is empty")
            return

        # Get all columns from CSV
        csv_columns = list(rows[0].keys())

        # Separate user columns from reserved columns
        user_columns = [col for col in csv_columns if col not in self.RESERVED_COLUMNS]

        # Create or update schema
        self.create_schema(user_columns)

        # Check if we need to add new columns to existing table
        cursor = self.conn.execute("PRAGMA table_info(jobs)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        for col in csv_columns:
            if col not in existing_columns and col not in self.RESERVED_COLUMNS:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")

        # Import rows
        imported = 0
        job_ids = []
        for i, row in enumerate(rows):
            # Generate job_id if not present
            job_id = row.get('JOBSCHEDULER_JOB_ID', f"job_{i:08d}")
            job_ids.append(job_id)

            # Set default values
            status = 'pending' if reset_status else row.get('JOBSCHEDULER_STATUS', 'pending')
            priority = int(row.get('JOBSCHEDULER_PRIORITY', 0))
            estimate_time = float(row.get('JOBSCHEDULER_ESTIMATE_TIME', 0))
            depends_on = row.get('JOBSCHEDULER_DEPENDS_ON', '').strip()

            # Build insert query
            columns = ['JOBSCHEDULER_JOB_ID', 'JOBSCHEDULER_STATUS', 'JOBSCHEDULER_PRIORITY', 'JOBSCHEDULER_ESTIMATE_TIME', 'JOBSCHEDULER_DEPENDS_ON']
            values = [job_id, status, priority, estimate_time, depends_on]

            # Add user columns
            for col in user_columns:
                if col in row:
                    columns.append(col)
                    values.append(row[col])

            placeholders = ','.join(['?' for _ in values])
            insert_sql = f"INSERT OR REPLACE INTO jobs ({','.join(columns)}) VALUES ({placeholders})"

            self.conn.execute(insert_sql, values)
            imported += 1

        # Clear existing dependencies for reimported jobs
        placeholders = ','.join(['?' for _ in job_ids])
        self.conn.execute(f"DELETE FROM job_dependencies WHERE job_id IN ({placeholders})", job_ids)

        # Parse and insert dependencies
        for i, row in enumerate(rows):
            job_id = row.get('JOBSCHEDULER_JOB_ID', f"job_{i:08d}")
            depends_on = row.get('JOBSCHEDULER_DEPENDS_ON', '').strip()

            if depends_on:
                # Split by space
                dep_list = depends_on.split()
                for dep in dep_list:
                    if dep:
                        self.conn.execute(
                            "INSERT OR IGNORE INTO job_dependencies (job_id, depends_on) VALUES (?, ?)",
                            (job_id, dep)
                        )

        self.conn.commit()

        # Validate dependencies
        self._validate_dependencies()

        print(f"✓ Imported {imported} jobs from {csv_path}")

    def _validate_dependencies(self):
        """Validate dependencies after import"""
        # Check for non-existent job IDs
        cursor = self.conn.execute("""
            SELECT DISTINCT d.job_id, d.depends_on
            FROM job_dependencies d
            LEFT JOIN jobs j ON d.depends_on = j.JOBSCHEDULER_JOB_ID
            WHERE j.JOBSCHEDULER_JOB_ID IS NULL
        """)

        missing = cursor.fetchall()
        if missing:
            print("Warning: Found dependencies to non-existent jobs:")
            for row in missing:
                print(f"  - Job '{row[0]}' depends on non-existent job '{row[1]}'")

        # Check for self-dependencies
        cursor = self.conn.execute("""
            SELECT job_id FROM job_dependencies WHERE job_id = depends_on
        """)

        self_deps = cursor.fetchall()
        if self_deps:
            print("Warning: Found self-dependencies:")
            for row in self_deps:
                print(f"  - Job '{row[0]}' depends on itself")

    def add_csv(self, csv_path: str):
        """Add jobs from CSV to existing database"""
        if not self.table_exists():
            raise RuntimeError("Database is not initialized. Use 'import' command to create the schema first.")

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            print("Warning: CSV file is empty")
            return

        # Get all columns from CSV
        csv_columns = list(rows[0].keys())

        # Get existing table columns
        cursor = self.conn.execute("PRAGMA table_info(jobs)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        # Separate user columns
        csv_user_columns = {col for col in csv_columns if col not in self.RESERVED_COLUMNS}
        db_user_columns = existing_columns - self.RESERVED_COLUMNS - {'JOBSCHEDULER_DEPENDS_ON'}

        # Check schema compatibility
        missing_in_csv = db_user_columns - csv_user_columns
        extra_in_csv = csv_user_columns - db_user_columns

        if missing_in_csv:
            print(f"Warning: DB has columns not in CSV (will be NULL): {missing_in_csv}")
        if extra_in_csv:
            sys.exit(f"Error: CSV has columns not in DB: {extra_in_csv}")

        # Get existing job IDs and compute next auto-ID offset
        existing_ids = {row[0] for row in self.conn.execute(
            "SELECT JOBSCHEDULER_JOB_ID FROM jobs").fetchall()}

        # Compute max existing auto-generated index so new IDs don't collide
        max_existing = -1
        for eid in existing_ids:
            if eid.startswith("job_") and eid[4:].isdigit():
                max_existing = max(max_existing, int(eid[4:]))
        next_id = max_existing + 1

        # Import rows
        imported = 0
        skipped = 0
        auto_offset = 0
        for row in rows:
            # Generate job_id if not present
            if 'JOBSCHEDULER_JOB_ID' not in row or not row['JOBSCHEDULER_JOB_ID']:
                job_id = f"job_{next_id + auto_offset:08d}"
                auto_offset += 1
            else:
                job_id = row['JOBSCHEDULER_JOB_ID']

            # Check for duplicates
            if job_id in existing_ids:
                print(f"Warning: Skipping duplicate job ID: {job_id}")
                skipped += 1
                continue

            # Set default values
            status = 'pending'
            priority = int(row.get('JOBSCHEDULER_PRIORITY', 0))
            estimate_time = float(row.get('JOBSCHEDULER_ESTIMATE_TIME', 0))
            depends_on = row.get('JOBSCHEDULER_DEPENDS_ON', '').strip()

            # Build insert query - include all DB columns
            columns = ['JOBSCHEDULER_JOB_ID', 'JOBSCHEDULER_STATUS', 'JOBSCHEDULER_PRIORITY', 'JOBSCHEDULER_ESTIMATE_TIME', 'JOBSCHEDULER_DEPENDS_ON']
            values = [job_id, status, priority, estimate_time, depends_on]

            # Add user columns (all from DB schema)
            for col in db_user_columns:
                columns.append(col)
                values.append(row.get(col, None))

            placeholders = ','.join(['?' for _ in values])
            insert_sql = f"INSERT INTO jobs ({','.join(columns)}) VALUES ({placeholders})"

            self.conn.execute(insert_sql, values)

            # Parse and insert dependencies
            if depends_on:
                dep_list = depends_on.split()
                for dep in dep_list:
                    if dep:
                        self.conn.execute(
                            "INSERT OR IGNORE INTO job_dependencies (job_id, depends_on) VALUES (?, ?)",
                            (job_id, dep)
                        )

            imported += 1

        self.conn.commit()

        # Validate dependencies
        self._validate_dependencies()

        print(f"✓ Added {imported} jobs from {csv_path} (skipped {skipped} duplicates)")

    def export_csv(self, csv_path: str, status_filter: Optional[str] = None):
        """Export jobs to CSV file"""
        if not self.table_exists():
            raise RuntimeError("Database is not initialized. Use 'import' command to create the schema.")

        # Build query
        query = "SELECT * FROM jobs"
        params = []

        if status_filter:
            query += " WHERE JOBSCHEDULER_STATUS = ?"
            params.append(status_filter)

        query += " ORDER BY JOBSCHEDULER_JOB_ID"

        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            print("Warning: No jobs to export")
            return

        # Get column names
        columns = [description[0] for description in cursor.description]

        # Write CSV
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()

            for row in rows:
                writer.writerow(dict(row))

        print(f"✓ Exported {len(rows)} jobs to {csv_path}")

    def table_exists(self, table_name: str = 'jobs') -> bool:
        """Check if table exists in database"""
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cursor.fetchone() is not None

    def recover_stuck_jobs(self, stale_threshold: int = 120) -> int:
        """Recover jobs stuck in 'running' state with stale or missing heartbeat.

        Uses file-based heartbeat (mtime) as primary signal — mirrors the logic in
        job_scheduler.py and progress_viewer.py so all three tools agree on what
        "stuck" means. Falls back to the DB heartbeat column for workers that
        predate the file-based mechanism.

        Returns the number of jobs recovered.
        """
        hb_dir = Path(self.db_path + ".heartbeat")
        now = time.time()

        cursor = self.conn.execute("""
            SELECT JOBSCHEDULER_JOB_ID, JOBSCHEDULER_WORKER_ID,
                   CASE WHEN JOBSCHEDULER_HEARTBEAT IS NULL THEN 1
                        WHEN JOBSCHEDULER_HEARTBEAT < datetime('now', '-' || ? || ' seconds') THEN 1
                        ELSE 0 END as db_stale
            FROM jobs WHERE JOBSCHEDULER_STATUS = 'running'
        """, (stale_threshold,))
        running_jobs = cursor.fetchall()

        stuck_job_ids = []
        for row in running_jobs:
            job_id, worker_id, db_stale = row[0], (row[1] or 'unknown'), row[2]
            hb_file = hb_dir / job_id if hb_dir.exists() else None

            if hb_file is not None and hb_file.exists():
                try:
                    age = now - hb_file.stat().st_mtime
                except FileNotFoundError:
                    age = float('inf')
                if age > stale_threshold:
                    print(f"  Recovering stuck job: {job_id} (worker={worker_id}, heartbeat_age={age:.0f}s)")
                    stuck_job_ids.append(job_id)
            elif db_stale:
                print(f"  Recovering stuck job: {job_id} (worker={worker_id}, no heartbeat file, db heartbeat stale)")
                stuck_job_ids.append(job_id)

        if not stuck_job_ids:
            return 0

        placeholders = ','.join('?' * len(stuck_job_ids))
        self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute(
            f"""
            UPDATE jobs
            SET JOBSCHEDULER_STATUS = 'pending',
                JOBSCHEDULER_STARTED_AT = NULL,
                JOBSCHEDULER_HEARTBEAT = NULL,
                JOBSCHEDULER_WORKER_ID = NULL,
                JOBSCHEDULER_KILL_REQUESTED = NULL
            WHERE JOBSCHEDULER_JOB_ID IN ({placeholders})
            AND JOBSCHEDULER_STATUS = 'running'
            """,
            stuck_job_ids,
        )
        self.conn.commit()
        print(f"[Recovery] Reset {len(stuck_job_ids)} stuck job(s) to 'pending' (heartbeat threshold: {stale_threshold}s)")

        if hb_dir.exists():
            for job_id in stuck_job_ids:
                try:
                    (hb_dir / job_id).unlink(missing_ok=True)
                    (hb_dir / f"{job_id}.kill").unlink(missing_ok=True)
                except Exception:
                    pass

        return len(stuck_job_ids)

    def reconcile_running_jobs(self, fresh_threshold: int = 120) -> int:
        """Flip jobs back to 'running' when heartbeat file is fresh but status is not.

        Reverse of recover_stuck_jobs(): fixes cases where status was bulk-reset
        or otherwise corrupted away from 'running' while the worker is still
        alive and touching its heartbeat file.

        Returns the number of jobs reconciled.
        """
        hb_dir = Path(self.db_path + ".heartbeat")
        if not hb_dir.exists():
            return 0

        now = time.time()
        fresh_ages: Dict[str, float] = {}
        for hb_file in hb_dir.iterdir():
            if hb_file.name.endswith('.kill'):
                continue
            try:
                age = now - hb_file.stat().st_mtime
            except FileNotFoundError:
                continue
            if age <= fresh_threshold:
                fresh_ages[hb_file.name] = age

        if not fresh_ages:
            return 0

        fresh_ids = list(fresh_ages.keys())
        placeholders = ','.join('?' * len(fresh_ids))
        cursor = self.conn.execute(
            f"""
            SELECT JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS
            FROM jobs
            WHERE JOBSCHEDULER_JOB_ID IN ({placeholders})
            AND JOBSCHEDULER_STATUS != 'running'
            """,
            fresh_ids,
        )
        mismatched = cursor.fetchall()
        if not mismatched:
            return 0

        mismatched_ids = [row[0] for row in mismatched]
        for row in mismatched:
            job_id, old_status = row[0], row[1]
            age = fresh_ages.get(job_id, 0.0)
            print(f"  Reconciled: {job_id} (status: {old_status} → running, heartbeat_age={age:.0f}s)")

        placeholders = ','.join('?' * len(mismatched_ids))
        self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute(
            f"""
            UPDATE jobs
            SET JOBSCHEDULER_STATUS = 'running',
                JOBSCHEDULER_HEARTBEAT = datetime('now'),
                JOBSCHEDULER_FINISHED_AT = NULL,
                JOBSCHEDULER_ELAPSED_TIME = NULL,
                JOBSCHEDULER_ERROR_MESSAGE = NULL,
                JOBSCHEDULER_STARTED_AT = COALESCE(JOBSCHEDULER_STARTED_AT, datetime('now'))
            WHERE JOBSCHEDULER_JOB_ID IN ({placeholders})
            AND JOBSCHEDULER_STATUS != 'running'
            """,
            mismatched_ids,
        )
        self.conn.commit()
        print(f"[Reconcile] Restored {len(mismatched_ids)} job(s) to 'running' (heartbeat threshold: {fresh_threshold}s)")
        return len(mismatched_ids)

    def get_stats(self) -> Dict[str, int]:
        """Get job statistics"""
        if not self.table_exists():
            raise RuntimeError("Database is not initialized. Use 'import' command to create the schema.")

        cursor = self.conn.execute("""
            SELECT JOBSCHEDULER_STATUS, COUNT(*) as count
            FROM jobs
            GROUP BY JOBSCHEDULER_STATUS
        """)

        stats = {row[0]: row[1] for row in cursor.fetchall()}

        # Get total
        total = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        stats['total'] = total

        return stats

    def _valid_columns(self) -> set:
        """Return set of column names for the jobs table."""
        cursor = self.conn.execute("PRAGMA table_info(jobs)")
        return {row[1] for row in cursor.fetchall()}

    def get_job(self, job_id: str) -> Optional[Dict]:
        """Return all fields for a single job, or None if not found."""
        if not self.table_exists():
            return None
        cursor = self.conn.execute(
            "SELECT * FROM jobs WHERE JOBSCHEDULER_JOB_ID = ?", (job_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def get_dependencies(self, job_id: str):
        """Return (depends_on_list, dependents_list) for a job."""
        depends_on = [row[0] for row in self.conn.execute(
            "SELECT depends_on FROM job_dependencies WHERE job_id = ?", (job_id,)
        ).fetchall()]
        dependents = [row[0] for row in self.conn.execute(
            "SELECT job_id FROM job_dependencies WHERE depends_on = ?", (job_id,)
        ).fetchall()]
        return depends_on, dependents

    def list_jobs(self, status=None, worker=None, grep_error=None, since=None, until=None,
                  priority_min=None, priority_max=None, sort='JOBSCHEDULER_JOB_ID',
                  limit=None, columns=None):
        """Query jobs with filters. Returns (headers, rows)."""
        if not self.table_exists():
            raise RuntimeError("Database is not initialized.")

        valid_cols = self._valid_columns()

        if sort not in valid_cols:
            raise ValueError(f"Invalid sort column: {sort!r}. Valid: {sorted(valid_cols)}")

        if columns:
            invalid = [c for c in columns if c not in valid_cols]
            if invalid:
                raise ValueError(f"Invalid columns: {invalid}. Valid: {sorted(valid_cols)}")
            select_expr = ', '.join(columns)
        else:
            select_expr = '*'

        if grep_error:
            import re as _re
            def _regexp(pattern, value):
                if value is None:
                    return False
                try:
                    return _re.search(pattern, value) is not None
                except _re.error:
                    return False
            self.conn.create_function("REGEXP", 2, _regexp, deterministic=True)

        conditions = []
        params = []
        if status:
            conditions.append("JOBSCHEDULER_STATUS = ?")
            params.append(status)
        if worker:
            conditions.append("JOBSCHEDULER_WORKER_ID = ?")
            params.append(worker)
        if grep_error:
            conditions.append("JOBSCHEDULER_ERROR_MESSAGE REGEXP ?")
            params.append(grep_error)
        if since:
            conditions.append("JOBSCHEDULER_CREATED_AT >= ?")
            params.append(since)
        if until:
            conditions.append("JOBSCHEDULER_CREATED_AT <= ?")
            params.append(until)
        if priority_min is not None:
            conditions.append("JOBSCHEDULER_PRIORITY >= ?")
            params.append(priority_min)
        if priority_max is not None:
            conditions.append("JOBSCHEDULER_PRIORITY <= ?")
            params.append(priority_max)

        sql = f"SELECT {select_expr} FROM jobs"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += f" ORDER BY {sort}"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)

        cursor = self.conn.execute(sql, params)
        rows = cursor.fetchall()
        headers = [d[0] for d in cursor.description]
        return headers, rows

    def get_stats_by(self, dimension: str) -> list:
        """Group job counts by status/worker/priority."""
        dim_map = {
            'status': 'JOBSCHEDULER_STATUS',
            'worker': 'JOBSCHEDULER_WORKER_ID',
            'priority': 'JOBSCHEDULER_PRIORITY',
        }
        if dimension not in dim_map:
            raise ValueError(f"dimension must be one of {sorted(dim_map.keys())}")
        col = dim_map[dimension]
        cursor = self.conn.execute(f"""
            SELECT {col} as dim,
                   COUNT(*) as total,
                   SUM(CASE WHEN JOBSCHEDULER_STATUS='done' THEN 1 ELSE 0 END) as done,
                   SUM(CASE WHEN JOBSCHEDULER_STATUS='error' THEN 1 ELSE 0 END) as error,
                   SUM(CASE WHEN JOBSCHEDULER_STATUS='running' THEN 1 ELSE 0 END) as running,
                   SUM(CASE WHEN JOBSCHEDULER_STATUS='pending' THEN 1 ELSE 0 END) as pending
            FROM jobs
            GROUP BY {col}
            ORDER BY total DESC
        """)
        return cursor.fetchall()

    def reset_jobs(self, job_ids: Optional[List[str]], status_filter: Optional[str], set_status: str):
        """Reset jobs to set_status. Returns (count, missing_ids)."""
        if set_status == 'pending':
            SET_CLAUSE = """
                SET JOBSCHEDULER_STATUS = ?,
                    JOBSCHEDULER_STARTED_AT = NULL,
                    JOBSCHEDULER_FINISHED_AT = NULL,
                    JOBSCHEDULER_ELAPSED_TIME = NULL,
                    JOBSCHEDULER_ERROR_MESSAGE = NULL,
                    JOBSCHEDULER_HEARTBEAT = NULL,
                    JOBSCHEDULER_WORKER_ID = NULL,
                    JOBSCHEDULER_KILL_REQUESTED = NULL"""
        else:
            SET_CLAUSE = """
                SET JOBSCHEDULER_STATUS = ?,
                    JOBSCHEDULER_FINISHED_AT = datetime('now'),
                    JOBSCHEDULER_HEARTBEAT = NULL,
                    JOBSCHEDULER_WORKER_ID = NULL,
                    JOBSCHEDULER_KILL_REQUESTED = NULL"""

        if job_ids:
            placeholders = ','.join('?' * len(job_ids))
            conditions = [f"JOBSCHEDULER_JOB_ID IN ({placeholders})"]
            params: List = [set_status] + list(job_ids)
            if status_filter:
                conditions.append("JOBSCHEDULER_STATUS = ?")
                params.append(status_filter)
            where = " AND ".join(conditions)
            self.conn.execute(f"UPDATE jobs{SET_CLAUSE} WHERE {where}", params)
            self.conn.commit()
            count = self.conn.total_changes
            found_ids = {row[0] for row in self.conn.execute(
                f"SELECT JOBSCHEDULER_JOB_ID FROM jobs WHERE JOBSCHEDULER_JOB_ID IN ({placeholders})",
                job_ids
            ).fetchall()}
            missing = [jid for jid in job_ids if jid not in found_ids]
            return count, missing
        elif status_filter:
            self.conn.execute(
                f"UPDATE jobs{SET_CLAUSE} WHERE JOBSCHEDULER_STATUS = ?",
                (set_status, status_filter)
            )
            self.conn.commit()
            return self.conn.total_changes, []
        else:
            self.conn.execute(f"UPDATE jobs{SET_CLAUSE}", (set_status,))
            self.conn.commit()
            return self.conn.total_changes, []

    def request_kill(self, job_ids: List[str]) -> int:
        """Mark running jobs for kill via DB flag + sentinel file. Returns count."""
        placeholders = ','.join('?' * len(job_ids))
        found = {row[0]: row[1] for row in self.conn.execute(
            f"SELECT JOBSCHEDULER_JOB_ID, JOBSCHEDULER_STATUS FROM jobs "
            f"WHERE JOBSCHEDULER_JOB_ID IN ({placeholders})", job_ids
        ).fetchall()}
        for jid in job_ids:
            if jid not in found:
                print(f"  Warning: job ID not found: {jid}")
        for jid, status in found.items():
            if status != 'running':
                print(f"  Warning: {jid} is not running (status={status}), skipping")

        self.conn.execute(
            f"UPDATE jobs SET JOBSCHEDULER_KILL_REQUESTED = datetime('now') "
            f"WHERE JOBSCHEDULER_JOB_ID IN ({placeholders}) AND JOBSCHEDULER_STATUS = 'running'",
            job_ids
        )
        self.conn.commit()
        killed_count = self.conn.total_changes

        running_ids = [jid for jid, status in found.items() if status == 'running']
        if running_ids:
            hb_dir = Path(self.db_path + ".heartbeat")
            if hb_dir.exists():
                for jid in running_ids:
                    try:
                        (hb_dir / f"{jid}.kill").touch()
                    except Exception as e:
                        print(f"  Warning: failed to create kill sentinel for {jid}: {e}")

        return killed_count


# Default columns shown by `list` when --columns is not specified
_LIST_DEFAULT_HIDDEN = {
    'JOBSCHEDULER_HEARTBEAT', 'JOBSCHEDULER_KILL_REQUESTED',
    'JOBSCHEDULER_DEPENDS_ON', 'JOBSCHEDULER_CREATED_AT',
    'JOBSCHEDULER_STARTED_AT', 'JOBSCHEDULER_FINISHED_AT',
    'JOBSCHEDULER_ESTIMATE_TIME',
}


def _require_rich():
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        return Console(), Table, Panel, Text
    except ImportError:
        sys.exit("rich が必要です。uv sync を実行してください。")


def _print_job_detail(job: dict, depends_on: list, dependents: list):
    console, Table, Panel, Text = _require_rich()
    table = Table("Field", "Value", show_header=True, header_style="bold cyan",
                  show_lines=False, expand=True)
    for key, value in job.items():
        val_str = str(value) if value is not None else "-"
        style = "dim" if key in JobDatabase.RESERVED_COLUMNS else "green"
        if key == 'JOBSCHEDULER_ERROR_MESSAGE' and value:
            val_text = Text(val_str, overflow="fold")
            table.add_row(key, val_text, style=style)
        else:
            table.add_row(key, val_str, style=style)
    console.print(table)
    if depends_on:
        console.print(Panel(", ".join(depends_on), title="Depends on", expand=False))
    if dependents:
        console.print(Panel(", ".join(dependents), title="Dependents", expand=False))


def _print_job_list(headers: list, rows: list, display_columns: Optional[List[str]] = None):
    console, Table, Panel, Text = _require_rich()
    if display_columns:
        col_indices = [i for i, h in enumerate(headers) if h in display_columns]
        shown_headers = [headers[i] for i in col_indices]
    else:
        col_indices = [i for i, h in enumerate(headers) if h not in _LIST_DEFAULT_HIDDEN]
        shown_headers = [headers[i] for i in col_indices]

    short_headers = [h.replace('JOBSCHEDULER_', '') for h in shown_headers]

    table = Table(*short_headers, show_header=True, header_style="bold cyan",
                  row_styles=["", "dim"], expand=True)
    for row in rows:
        cells = []
        for i in col_indices:
            val = row[i] if row[i] is not None else "-"
            cells.append(str(val))
        table.add_row(*cells)
    console.print(table)
    console.print(f"[dim]{len(rows)} row(s)[/dim]")


def _print_stats_by(rows: list, dimension: str):
    console, Table, Panel, Text = _require_rich()
    table = Table(dimension, "total", "done", "error", "running", "pending",
                  show_header=True, header_style="bold cyan", expand=False)
    for row in rows:
        dim_val = str(row[0]) if row[0] is not None else "(null)"
        table.add_row(dim_val, str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5]))
    console.print(f"\n[bold]By {dimension}:[/bold]")
    console.print(table)


def main():
    """CLI interface"""
    parser = argparse.ArgumentParser(
        description="Database utility for job-runner v2"
    )
    subparsers = parser.add_subparsers(dest='command', help='Command to execute', required=True)

    # import: csv_file [--db-path path.db] [--force]
    import_parser = subparsers.add_parser('import', help='Import CSV to SQLite (creates new DB or resets existing)')
    import_parser.add_argument('csv_file', help='CSV file path')
    import_parser.add_argument('--db-path', help='SQLite database file path (default: csv_file with .db extension)')
    import_parser.add_argument('--force', action='store_true', help='Overwrite existing database file without confirmation')

    # add: csv_file --db-path path.db
    add_parser = subparsers.add_parser('add', help='Add jobs from CSV to existing database')
    add_parser.add_argument('csv_file', help='CSV file path')
    add_parser.add_argument('--db-path', required=True, help='SQLite database file path')

    # export: db_file [--csv-path out.csv] [--status STATUS]
    export_parser = subparsers.add_parser('export', help='Export SQLite to CSV')
    export_parser.add_argument('db_file', help='SQLite database file path')
    export_parser.add_argument('--csv-path', help='CSV file path (default: db_file with .csv extension)')
    export_parser.add_argument('--status', help='Filter by status (pending/running/done/error)')
    export_parser.add_argument('--force', action='store_true', help='Overwrite existing CSV file without confirmation')

    # stats: db_file
    stats_parser = subparsers.add_parser('stats', help='Show job statistics')
    stats_parser.add_argument('db_file', help='SQLite database file path')
    stats_parser.add_argument('--no-recover', action='store_true',
                              help='Disable automatic recovery of stuck jobs')
    stats_parser.add_argument('--stale-threshold', type=int, default=120,
                              help='Seconds before a running job is considered stuck (default: 120)')
    stats_parser.add_argument('--by', choices=['status', 'worker', 'priority'],
                              help='Show breakdown grouped by this dimension')

    # show: job_id --db-path
    show_parser = subparsers.add_parser('show', help='Show all fields of a single job')
    show_parser.add_argument('job_id', help='Job ID to inspect')
    show_parser.add_argument('--db-path', required=True, help='SQLite database file path')

    # list: --db-path [filters]
    list_parser = subparsers.add_parser('list', help='List jobs in a formatted table')
    list_parser.add_argument('--db-path', required=True, help='SQLite database file path')
    list_parser.add_argument('--status', help='Filter by status (pending/running/done/error)')
    list_parser.add_argument('--worker', help='Filter by exact WORKER_ID')
    list_parser.add_argument('--grep-error', help='Python regex filter on ERROR_MESSAGE')
    list_parser.add_argument('--since', help='Minimum CREATED_AT (ISO timestamp)')
    list_parser.add_argument('--until', help='Maximum CREATED_AT (ISO timestamp)')
    list_parser.add_argument('--priority-min', type=int, help='Minimum PRIORITY (inclusive)')
    list_parser.add_argument('--priority-max', type=int, help='Maximum PRIORITY (inclusive)')
    list_parser.add_argument('--sort', default='JOBSCHEDULER_JOB_ID',
                             help='Column to sort by (default: JOBSCHEDULER_JOB_ID)')
    list_parser.add_argument('--limit', type=int, help='Maximum number of rows to show')
    list_parser.add_argument('--columns', help='Comma-separated column names to display')

    # reset: db_file [--status STATUS] [--jobs JOB_IDS] [--set-status STATUS]
    reset_parser = subparsers.add_parser('reset', help='Reset jobs to a target status (default: pending)')
    reset_parser.add_argument('db_file', help='SQLite database file path')
    reset_parser.add_argument('--status', help='Filter: only reset jobs currently in this status (pending/running/done/error)')
    reset_parser.add_argument('--jobs', help='Comma-separated job IDs to reset (e.g. job_00000000,job_00000001)')
    reset_parser.add_argument('--set-status', dest='set_status', default='pending',
                              choices=['pending', 'done', 'error', 'running'],
                              help='Target status to set jobs to (default: pending)')
    reset_parser.add_argument('--stale-threshold', type=int, default=120,
                              help='Seconds within which a heartbeat is considered fresh; '
                                   'jobs with a fresh heartbeat are restored to running after reset (default: 120)')

    # recover: db_file — reconcile DB status with heartbeat files (both directions)
    recover_parser = subparsers.add_parser(
        'recover',
        help='Reconcile DB status with heartbeat files (both directions)',
    )
    recover_parser.add_argument('db_file', help='SQLite database file path')
    recover_parser.add_argument('--stale-threshold', type=int, default=120,
                                help='Seconds threshold for stale/fresh heartbeat (default: 120)')
    recover_parser.add_argument('--direction', choices=['both', 'stuck', 'mismatch'], default='both',
                                help="both: run both checks (default); "
                                     "stuck: only 'running→pending' for stale heartbeats; "
                                     "mismatch: only 'pending/error→running' for fresh heartbeats")

    # kill: db_file --jobs JOB_IDS
    kill_parser = subparsers.add_parser('kill', help='Request termination of running jobs (marks for force kill)')
    kill_parser.add_argument('db_file', help='SQLite database file path')
    kill_parser.add_argument('--jobs', required=True, help='Comma-separated running job IDs to kill (e.g. job_00000000,job_00000001)')

    args = parser.parse_args()

    # Handle defaults for import
    if args.command == 'import':
        db_file = args.db_path if args.db_path else str(Path(args.csv_file).with_suffix('.db'))
        csv_file = args.csv_file
        if Path(db_file).exists() and not args.force:
            sys.exit(f"Error: Database file already exists: {db_file}\nUse --force to overwrite.")
        with JobDatabase(db_file) as db:
            db.import_csv(csv_file, reset_status=True)

    # Handle add
    elif args.command == 'add':
        if not Path(args.db_path).exists():
            sys.exit(f"Error: Database file does not exist: {args.db_path}")
        with JobDatabase(args.db_path) as db:
            db.add_csv(args.csv_file)

    # Handle export
    elif args.command == 'export':
        if not Path(args.db_file).exists():
            sys.exit(f"Error: Database file does not exist: {args.db_file}")
        csv_file = args.csv_path if args.csv_path else str(Path(args.db_file).with_suffix('.csv'))
        if Path(csv_file).exists() and not args.force:
            sys.exit(f"Error: CSV file already exists: {csv_file}\nUse --force to overwrite.")
        with JobDatabase(args.db_file) as db:
            db.export_csv(csv_file, status_filter=args.status)

    # Handle stats
    elif args.command == 'stats':
        if not Path(args.db_file).exists():
            sys.exit(f"Error: Database file does not exist: {args.db_file}")
        with JobDatabase(args.db_file) as db:
            if not args.no_recover:
                db.recover_stuck_jobs(stale_threshold=args.stale_threshold)
                db.reconcile_running_jobs(fresh_threshold=args.stale_threshold)
            stats = db.get_stats()
            print("\nJob Statistics:")
            print(f"  Total: {stats.get('total', 0)}")
            print(f"  Pending: {stats.get('pending', 0)}")
            print(f"  Running: {stats.get('running', 0)}")
            print(f"  Done: {stats.get('done', 0)}")
            print(f"  Error: {stats.get('error', 0)}")
            if args.by:
                rows = db.get_stats_by(args.by)
                _print_stats_by(rows, args.by)

    # Handle reset
    elif args.command == 'reset':
        if not Path(args.db_file).exists():
            sys.exit(f"Error: Database file does not exist: {args.db_file}")
        with JobDatabase(args.db_file) as db:
            if not db.table_exists():
                sys.exit("Error: Database is not initialized. Use 'import' command to create the schema.")
            set_status = args.set_status
            job_ids = [j.strip() for j in args.jobs.split(',') if j.strip()] if args.jobs else None
            count, missing = db.reset_jobs(job_ids, args.status, set_status)
            if job_ids:
                if args.status:
                    print(f"✓ Reset {count} jobs (from {len(job_ids)} specified IDs, status='{args.status}') to {set_status}")
                else:
                    print(f"✓ Reset {count} of {len(job_ids)} specified jobs to {set_status}")
            elif args.status:
                print(f"✓ Reset {count} jobs with status '{args.status}' to {set_status}")
            else:
                print(f"✓ Reset {count} jobs to {set_status} status")
            for jid in missing:
                print(f"  Warning: job ID not found: {jid}")

            if set_status != 'running':
                db.reconcile_running_jobs(fresh_threshold=args.stale_threshold)

    # Handle recover
    elif args.command == 'recover':
        if not Path(args.db_file).exists():
            sys.exit(f"Error: Database file does not exist: {args.db_file}")
        with JobDatabase(args.db_file) as db:
            if not db.table_exists():
                sys.exit("Error: Database is not initialized. Use 'import' command to create the schema.")
            if args.direction in ('both', 'stuck'):
                db.recover_stuck_jobs(stale_threshold=args.stale_threshold)
            if args.direction in ('both', 'mismatch'):
                db.reconcile_running_jobs(fresh_threshold=args.stale_threshold)

    # Handle kill
    elif args.command == 'kill':
        if not Path(args.db_file).exists():
            sys.exit(f"Error: Database file does not exist: {args.db_file}")
        with JobDatabase(args.db_file) as db:
            if not db.table_exists():
                sys.exit("Error: Database is not initialized. Use 'import' command to create the schema.")
            job_ids = [j.strip() for j in args.jobs.split(',') if j.strip()]
            if not job_ids:
                sys.exit("Error: No job IDs specified.")
            killed_count = db.request_kill(job_ids)
            print(f"✓ Marked {killed_count} running job(s) for termination")
            if killed_count > 0:
                print("  The scheduler will detect the signal within the next heartbeat interval (default: 30s)")

    # Handle show
    elif args.command == 'show':
        if not Path(args.db_path).exists():
            sys.exit(f"Error: Database file does not exist: {args.db_path}")
        with JobDatabase(args.db_path, read_only=True) as db:
            job = db.get_job(args.job_id)
            if job is None:
                print(f"Error: job not found: {args.job_id}", file=sys.stderr)
                sys.exit(2)
            depends_on, dependents = db.get_dependencies(args.job_id)
        _print_job_detail(job, depends_on, dependents)

    # Handle list
    elif args.command == 'list':
        if not Path(args.db_path).exists():
            sys.exit(f"Error: Database file does not exist: {args.db_path}")
        columns = [c.strip() for c in args.columns.split(',') if c.strip()] if args.columns else None
        try:
            with JobDatabase(args.db_path, read_only=True) as db:
                headers, rows = db.list_jobs(
                    status=args.status,
                    worker=args.worker,
                    grep_error=args.grep_error,
                    since=args.since,
                    until=args.until,
                    priority_min=args.priority_min,
                    priority_max=args.priority_max,
                    sort=args.sort,
                    limit=args.limit,
                    columns=columns,
                )
        except ValueError as e:
            sys.exit(f"Error: {e}")
        _print_job_list(headers, rows, display_columns=columns)


if __name__ == "__main__":
    main()
