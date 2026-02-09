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
        for row in rows:
            # Generate job_id if not present
            job_id = row.get('JOBSCHEDULER_JOB_ID', f"job_{imported:08d}")

            # Set default values
            status = 'pending' if reset_status else row.get('JOBSCHEDULER_STATUS', 'pending')
            priority = int(row.get('JOBSCHEDULER_PRIORITY', 0))
            estimate_time = float(row.get('JOBSCHEDULER_ESTIMATE_TIME', 0))

            # Build insert query
            columns = ['JOBSCHEDULER_JOB_ID', 'JOBSCHEDULER_STATUS', 'JOBSCHEDULER_PRIORITY', 'JOBSCHEDULER_ESTIMATE_TIME']
            values = [job_id, status, priority, estimate_time]

            # Add user columns
            for col in user_columns:
                if col in row:
                    columns.append(col)
                    values.append(row[col])

            placeholders = ','.join(['?' for _ in values])
            insert_sql = f"INSERT OR REPLACE INTO jobs ({','.join(columns)}) VALUES ({placeholders})"

            self.conn.execute(insert_sql, values)
            imported += 1

        self.conn.commit()
        print(f"✓ Imported {imported} jobs from {csv_path}")

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
    parser.add_argument('command', choices=['import', 'export', 'stats', 'reset'],
                       help='Command to execute')
    parser.add_argument('db_file', help='SQLite database file path')
    parser.add_argument('csv_file', nargs='?', help='CSV file path (for import/export)')
    parser.add_argument('--status', help='Filter by status (for export)')
    parser.add_argument('--no-reset', action='store_true',
                       help='Keep existing status when importing (default: reset to pending)')

    args = parser.parse_args()

    # Validate arguments
    if args.command in ('import', 'export') and not args.csv_file:
        parser.error(f"{args.command} requires csv_file argument")

    # Execute command
    with JobDatabase(args.db_file) as db:
        if args.command == 'import':
            db.import_csv(args.csv_file, reset_status=not args.no_reset)

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
