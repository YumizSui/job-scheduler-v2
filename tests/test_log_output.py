#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
--log-stderr オプションのテスト

デフォルトでstdoutにログ出力、--log-stderr指定でstderrに出力されることを確認。
"""

import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).parent.parent / "script" / "job_scheduler.py")


class TestLogOutput(unittest.TestCase):

    def test_default_logs_to_stdout(self):
        """デフォルト: ログがstdoutに出力される"""
        result = subprocess.run(
            [sys.executable, SCRIPT, "nonexistent.db", "echo"],
            capture_output=True, text=True,
        )
        self.assertIn("Database file not found", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_log_stderr_logs_to_stderr(self):
        """--log-stderr: ログがstderrに出力される"""
        result = subprocess.run(
            [sys.executable, SCRIPT, "nonexistent.db", "echo", "--log-stderr"],
            capture_output=True, text=True,
        )
        self.assertIn("Database file not found", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
