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
    }

    def __init__(self, db_path: str):
        """Initialize database connection"""
        self.db_path = db_path
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
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row  # Allow dict-like access

        # Enable WAL mode for better concurrency
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")  # 30 seconds
        self.conn.execute("PRAGMA synchronous=NORMAL")  # Balance between safety and speed

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
        for row in rows:
            # Generate job_id if not present
            job_id = row.get('JOBSCHEDULER_JOB_ID', f"job_{imported:08d}")
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
        for row in rows:
            job_id = row.get('JOBSCHEDULER_JOB_ID', f"job_{rows.index(row):08d}")
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

        # Get existing job IDs
        existing_ids = {row[0] for row in self.conn.execute(
            "SELECT JOBSCHEDULER_JOB_ID FROM jobs").fetchall()}

        # Import rows
        imported = 0
        skipped = 0
        for row in rows:
            # Generate job_id if not present
            job_id = row.get('JOBSCHEDULER_JOB_ID', f"job_{imported:08d}")

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

    def get_stats(self) -> Dict[str, int]:
        """Get job statistics"""
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


def main():
    """CLI interface"""
    parser = argparse.ArgumentParser(
        description="Database utility for job-runner v2"
    )
    parser.add_argument('command', choices=['import', 'export', 'stats', 'reset', 'add'],
                       help='Command to execute')
    parser.add_argument('db_file', help='SQLite database file path')
    parser.add_argument('csv_file', nargs='?', help='CSV file path (for import/export/add)')
    parser.add_argument('--status', help='Filter by status (for export/reset)')
    parser.add_argument('--no-reset', action='store_true',
                       help='Keep existing status when importing (default: reset to pending)')

    args = parser.parse_args()

    # Handle auto-detection for import/add
    if args.command in ('import', 'add'):
        if args.db_file.endswith('.csv'):
            # User provided CSV first: db_util import experiments.csv
            args.csv_file = args.db_file
            args.db_file = str(Path(args.db_file).with_suffix('.db'))
        elif not args.csv_file:
            parser.error(f"{args.command} requires csv_file argument")

    # Handle auto-detection for export
    if args.command == 'export' and not args.csv_file:
        args.csv_file = str(Path(args.db_file).with_suffix('.csv'))

    # Execute command
    with JobDatabase(args.db_file) as db:
        if args.command == 'import':
            db.import_csv(args.csv_file, reset_status=not args.no_reset)

        elif args.command == 'add':
            db.add_csv(args.csv_file)

        elif args.command == 'export':
            db.export_csv(args.csv_file, status_filter=args.status)

        elif args.command == 'stats':
            stats = db.get_stats()
            print("\nJob Statistics:")
            print(f"  Total: {stats.get('total', 0)}")
            print(f"  Pending: {stats.get('pending', 0)}")
            print(f"  Running: {stats.get('running', 0)}")
            print(f"  Done: {stats.get('done', 0)}")
            print(f"  Error: {stats.get('error', 0)}")

        elif args.command == 'reset':
            # Reset jobs to pending (all or filtered by status)
            if args.status:
                # Reset only jobs with specific status
                db.conn.execute("""
                    UPDATE jobs
                    SET JOBSCHEDULER_STATUS = 'pending',
                        JOBSCHEDULER_STARTED_AT = NULL,
                        JOBSCHEDULER_FINISHED_AT = NULL,
                        JOBSCHEDULER_ELAPSED_TIME = NULL,
                        JOBSCHEDULER_ERROR_MESSAGE = NULL
                    WHERE JOBSCHEDULER_STATUS = ?
                """, (args.status,))
                db.conn.commit()
                count = db.conn.total_changes
                print(f"✓ Reset {count} jobs with status '{args.status}' to pending")
            else:
                # Reset all jobs to pending
                db.conn.execute("""
                    UPDATE jobs
                    SET JOBSCHEDULER_STATUS = 'pending',
                        JOBSCHEDULER_STARTED_AT = NULL,
                        JOBSCHEDULER_FINISHED_AT = NULL,
                        JOBSCHEDULER_ELAPSED_TIME = NULL,
                        JOBSCHEDULER_ERROR_MESSAGE = NULL
                """)
                db.conn.commit()
                count = db.conn.total_changes
                print(f"✓ Reset {count} jobs to pending status")


if __name__ == "__main__":
    main()
