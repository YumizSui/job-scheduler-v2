#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interactive TUI for browsing and managing job scheduler databases.

Usage:
    job_tui <db_path> [--enable-actions] [--refresh-interval N] [--no-auto-refresh]
"""

import argparse
import json
import os
import sys
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

sys.path.insert(0, str(Path(__file__).parent))
from db_util import JobDatabase

DEFAULT_LIMIT = 500

_CONFIG_PATH = (
    Path(os.environ["XDG_CONFIG_HOME"]) if "XDG_CONFIG_HOME" in os.environ
    else Path.home() / ".config"
) / "job_tui" / "config.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}

# Columns shown in the table by default (JOBSCHEDULER_ prefix stripped for display)
_HIDDEN_IN_TABLE = {
    'JOBSCHEDULER_HEARTBEAT', 'JOBSCHEDULER_KILL_REQUESTED',
    'JOBSCHEDULER_DEPENDS_ON', 'JOBSCHEDULER_ESTIMATE_TIME',
}

# Sort cycle: pressing 's' advances through this list
_SORT_CYCLE = [
    'JOBSCHEDULER_JOB_ID',
    'JOBSCHEDULER_STATUS',
    'JOBSCHEDULER_PRIORITY',
    'JOBSCHEDULER_ELAPSED_TIME',
    'JOBSCHEDULER_CREATED_AT',
]


def _short(col: str) -> str:
    return col.replace('JOBSCHEDULER_', '')


class ConfirmModal(ModalScreen[bool]):
    """Yes/No confirmation dialog."""

    BINDINGS = [
        Binding("y", "confirm_yes", "Yes", priority=True),
        Binding("n", "confirm_no", "No", priority=True),
        Binding("escape", "confirm_no", "Cancel", priority=True),
        Binding("left", "focus_prev", show=False),
        Binding("right", "focus_next", show=False),
        Binding("h", "focus_prev", show=False),
        Binding("l", "focus_next", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._message, id="confirm-message"),
            Horizontal(
                Button("Yes (y)", id="confirm-yes"),
                Button("No (n)", id="confirm-no"),
                id="confirm-buttons",
            ),
            id="confirm-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_focus_prev(self) -> None:
        self.focus_previous()

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_confirm_yes(self) -> None:
        self.dismiss(True)

    def action_confirm_no(self) -> None:
        self.dismiss(False)


class DetailPanel(Static):
    """Right panel showing all fields of the selected job."""

    def show_job(self, job: dict, depends_on: list, dependents: list) -> None:
        lines = []
        for key, value in job.items():
            val_str = str(value) if value is not None else "-"
            lines.append(f"[cyan]{_short(key)}[/cyan]: {val_str}")
        if depends_on:
            lines.append(f"\n[bold]Depends on:[/bold] {', '.join(depends_on)}")
        if dependents:
            lines.append(f"[bold]Dependents:[/bold] {', '.join(dependents)}")
        self.update("\n".join(lines))

    def clear_job(self) -> None:
        self.update("[dim]Click a row to see job details[/dim]")


class JobTUI(App):
    SCROLL_SENSITIVITY_Y = 1

    CSS = """
    Screen {
        layout: vertical;
    }
    #main-area {
        height: 1fr;
    }
    #job-table {
        width: 2fr;
        border: solid $primary;
    }
    #detail-panel {
        width: 1fr;
        border: solid $secondary;
        padding: 1;
        overflow-y: auto;
    }
    #filter-bar {
        height: 3;
        border: solid $accent;
    }
    #status-bar {
        height: 1;
        background: $surface;
        color: $text-muted;
    }
    ConfirmModal {
        align: center middle;
    }
    #confirm-dialog {
        width: 50;
        height: auto;
        padding: 2;
        border: solid $warning;
        background: $surface;
    }
    #confirm-message {
        text-align: center;
        margin-bottom: 1;
        width: 100%;
    }
    #confirm-buttons {
        height: auto;
        align: center middle;
    }
    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("/", "focus_filter", "Filter"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("p", "toggle_pause", "Pause"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("q", "confirm_quit", "Quit"),
        Binding("escape", "confirm_quit", "Quit", show=False),
        Binding("ctrl+r", "reset_job", "Reset→pending", priority=True),
        Binding("ctrl+k", "kill_job", "Kill", priority=True),
    ]

    def __init__(self, db_path: str, enable_actions: bool = False,
                 refresh_interval: float = 5.0, no_auto_refresh: bool = False) -> None:
        super().__init__()
        self.db_path = db_path
        self.enable_actions = enable_actions
        self.refresh_interval = refresh_interval
        self.no_auto_refresh = no_auto_refresh
        self._initial_theme: str | None = _load_config().get("theme")
        self._paused = False
        self._filter_text = ""
        self._debounce_timer = None
        self._sort_idx = 0
        self._headers: list[str] = []
        self._all_rows: list = []
        self._table_col_indices: list[int] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-area"):
            yield DataTable(id="job-table", cursor_type="row")
            detail = DetailPanel(id="detail-panel")
            detail.clear_job()
            yield detail
        yield Input(placeholder="/ filter  status=running  priority>3  worker=node01  free text  [Esc] back",
                    id="filter-bar")
        yield Label("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        if self._initial_theme:
            try:
                self.theme = self._initial_theme
            except Exception:
                pass
        self._load_data()
        if not self.no_auto_refresh:
            self.set_interval(self.refresh_interval, self._auto_reload)

    def _auto_reload(self) -> None:
        if not self._paused:
            self._load_data()

    def _current_sort(self) -> str:
        return _SORT_CYCLE[self._sort_idx % len(_SORT_CYCLE)]

    def _load_data(self) -> None:
        try:
            with JobDatabase(self.db_path, read_only=True) as db:
                headers, rows = db.list_jobs(sort=self._current_sort(), limit=DEFAULT_LIMIT)
        except Exception as e:
            self.query_one("#status-bar", Label).update(f"[red]DB error: {e}[/red]")
            return

        self._headers = headers
        self._all_rows = [list(r) for r in rows]
        self._table_col_indices = [
            i for i, h in enumerate(headers) if h not in _HIDDEN_IN_TABLE
        ]
        self._apply_filter()

    def _apply_filter(self) -> None:
        table = self.query_one("#job-table", DataTable)
        table.clear(columns=True)

        shown_headers = [self._headers[i] for i in self._table_col_indices]
        for h in shown_headers:
            table.add_column(_short(h), key=h)

        ftext = self._filter_text.strip()
        filtered = self._all_rows

        if ftext:
            # Parse "key=value" tokens first, remainder is free text
            import re
            key_val = {}
            remainder_parts = []
            for token in ftext.split():
                m = re.match(r'^(status|worker|priority)(>=|<=|!=|>|<|=)(.+)$', token, re.IGNORECASE)
                if m:
                    key_val[m.group(1).lower()] = (m.group(2), m.group(3))
                else:
                    remainder_parts.append(token)
            remainder = " ".join(remainder_parts).lower()

            status_col = next((i for i, h in enumerate(self._headers)
                               if h == 'JOBSCHEDULER_STATUS'), None)
            worker_col = next((i for i, h in enumerate(self._headers)
                               if h == 'JOBSCHEDULER_WORKER_ID'), None)
            priority_col = next((i for i, h in enumerate(self._headers)
                                 if h == 'JOBSCHEDULER_PRIORITY'), None)
            error_col = next((i for i, h in enumerate(self._headers)
                              if h == 'JOBSCHEDULER_ERROR_MESSAGE'), None)

            def _cmp(cell_val, op, target):
                try:
                    a, b = float(cell_val), float(target)
                    return {'=': a == b, '!=': a != b, '>': a > b, '<': a < b,
                            '>=': a >= b, '<=': a <= b}[op]
                except (ValueError, TypeError):
                    s = str(cell_val or '').lower()
                    return {'=': s == target.lower(), '!=': s != target.lower(),
                            '>': s > target.lower(), '<': s < target.lower(),
                            '>=': s >= target.lower(), '<=': s <= target.lower()}[op]

            def row_matches(row):
                if 'status' in key_val:
                    op, val = key_val['status']
                    if status_col is None or not _cmp(row[status_col], op, val):
                        return False
                if 'worker' in key_val:
                    op, val = key_val['worker']
                    if worker_col is None or (op == '=' and val.lower() not in str(row[worker_col] or '').lower()):
                        return False
                if 'priority' in key_val:
                    op, val = key_val['priority']
                    if priority_col is None or not _cmp(row[priority_col], op, val):
                        return False
                if remainder:
                    all_text = " ".join(str(v or '') for v in row)
                    try:
                        if not re.search(remainder, all_text, re.IGNORECASE):
                            return False
                    except re.error:
                        if remainder not in all_text.lower():
                            return False
                return True

            filtered = [r for r in self._all_rows if row_matches(r)]

        for row in filtered:
            cells = [str(row[i]) if row[i] is not None else "-"
                     for i in self._table_col_indices]
            table.add_row(*cells)

        sort_name = _short(self._current_sort())
        pause_flag = " [PAUSED]" if self._paused else ""
        self.query_one("#status-bar", Label).update(
            f"sort={sort_name}  filter={ftext!r}  {len(filtered)}/{len(self._all_rows)} rows{pause_flag}"
        )

    def _selected_job_id(self) -> str | None:
        table = self.query_one("#job-table", DataTable)
        row_key = table.cursor_row
        try:
            row = table.get_row_at(row_key)
        except Exception:
            return None
        # JOB_ID is first shown column if it's in the list
        jid_col = next((i for i, h in enumerate(self._headers)
                        if h == 'JOBSCHEDULER_JOB_ID'), None)
        if jid_col is None:
            return None
        # Find position of JOB_ID in shown columns
        shown_pos = next((p for p, ci in enumerate(self._table_col_indices)
                          if ci == jid_col), None)
        if shown_pos is None or shown_pos >= len(row):
            return None
        return str(row[shown_pos])

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        try:
            with JobDatabase(self.db_path, read_only=True) as db:
                job = db.get_job(job_id)
                if job:
                    dep_on, dep_by = db.get_dependencies(job_id)
                    self.query_one("#detail-panel", DetailPanel).show_job(job, dep_on, dep_by)
        except Exception:
            pass

    def action_focus_filter(self) -> None:
        self.query_one("#filter-bar", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._filter_text = event.value
        self._apply_filter()
        self.query_one("#job-table", DataTable).focus()

    def on_key(self, event) -> None:
        if event.key == "escape":
            filter_bar = self.query_one("#filter-bar", Input)
            if filter_bar.has_focus:
                self.query_one("#job-table", DataTable).focus()
                event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter_text = event.value
        if self._debounce_timer is not None:
            self._debounce_timer.stop()
        self._debounce_timer = self.set_timer(0.3, self._apply_filter)

    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(_SORT_CYCLE)
        self._load_data()

    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        self._apply_filter()

    def action_refresh_now(self) -> None:
        self._load_data()

    def watch_theme(self, old_theme: str, new_theme: str) -> None:
        if old_theme == new_theme:
            return
        try:
            _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            existing = _load_config()
            existing["theme"] = new_theme
            _CONFIG_PATH.write_text(json.dumps(existing, indent=2))
        except OSError:
            pass

    def action_confirm_quit(self) -> None:
        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.exit()
        self.push_screen(ConfirmModal("Quit job_tui?"), on_confirm)

    # --- Actions only active with --enable-actions ---

    def action_reset_job(self) -> None:
        if not self.enable_actions:
            return
        job_id = self._selected_job_id()
        if not job_id:
            return

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                with JobDatabase(self.db_path) as db:
                    db.reset_jobs([job_id], status_filter=None, set_status='pending')
            except Exception as e:
                self.query_one("#status-bar", Label).update(f"[red]reset failed: {e}[/red]")
            self._load_data()

        self.push_screen(ConfirmModal(f"Reset {job_id} to pending?"), on_confirm)

    def action_kill_job(self) -> None:
        if not self.enable_actions:
            return
        job_id = self._selected_job_id()
        if not job_id:
            return

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                with JobDatabase(self.db_path) as db:
                    db.request_kill([job_id])
            except Exception as e:
                self.query_one("#status-bar", Label).update(f"[red]kill failed: {e}[/red]")
            self._load_data()

        self.push_screen(ConfirmModal(f"Send kill to {job_id}?"), on_confirm)


def main():
    parser = argparse.ArgumentParser(
        description="Interactive TUI for job scheduler database"
    )
    parser.add_argument('db_path', help='SQLite database file path')
    parser.add_argument('--enable-actions', action='store_true',
                        help='Enable Ctrl+R (reset→pending) and Ctrl+K (kill) actions')
    parser.add_argument('--auto-refresh', action='store_true',
                        help='Enable auto-refresh (default: off; use r to refresh manually)')
    parser.add_argument('--refresh-interval', type=float, default=5.0,
                        help='Auto-refresh interval in seconds (default: 5.0)')
    args = parser.parse_args()

    if not Path(args.db_path).exists():
        sys.exit(f"Error: Database file does not exist: {args.db_path}")

    app = JobTUI(
        db_path=args.db_path,
        enable_actions=args.enable_actions,
        refresh_interval=args.refresh_interval,
        no_auto_refresh=not args.auto_refresh,
    )
    app.run()


if __name__ == "__main__":
    main()
