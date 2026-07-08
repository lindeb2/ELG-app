import customtkinter as ctk
import threading
import time
from datetime import datetime, timedelta
from commit_transaction import CommitTransactionManager, TXN_REFRESH_INTERVAL_MS
from CTkStickyPlaceholderEntry import CTkStickyPlaceholderEntry
from format_time import format_time
from mongo_doc_lookup import week_bucket_from_agg, week_goals
from notifications import (
    create_broken_records_notification,
    fetch_week_goal_context_at_start,
    format_end_message,
    format_start_message,
    post_notification,
)
from period_model import to_local
from settings_ui_constants import ACCENT
from timetable_db import aggregations, client, collection, db, get_user, status_meeting
from utils import flash_error

COLOR_BACKGROUND=   "#000000"
COLOR_SELECTED=     "#191919"
COLOR_PRIMARY=      "#232323"
COLOR_HOVER=        "#333333"
COLOR_DISABLED_TEXT="#666666"
COLOR_TEXT=         "#FFFFFF"

_PAD = 6
_ROW_TOP_PAD = 0
_PAD_ROWS = (0, 2, 4)
_PAD_COLS = (0, 2, 4)
_ROW_BUTTONS = 1
_ROW_TIMER = 3
_COL_LEFT = 1
_COL_RIGHT = 3
_COL_FULL_SPAN = 3
_PRIMARY_BTN_FONT = ("Arial", 24)
_ACTION_BTN_FONT = ("Arial", 14)

_OVERLAY_PAD_ROWS = (0, 2, 4, 6)
_OVERLAY_ROW_NAME = 1
_OVERLAY_ROW_DESC = 3
_OVERLAY_ROW_BUTTONS = 5

_FOCUS_HIGHLIGHT_WIDTH = 1


def _configure_padded_grid(
    frame,
    *,
    pad_rows: tuple[int, ...],
    pad_cols: tuple[int, ...],
    content_rows: tuple[int, ...],
    content_cols: tuple[int, ...],
    content_row_weights: tuple[int, ...] | None = None,
    content_row_uniform: str | None = None,
    content_col_uniform: str | None = None,
) -> None:
    for row in pad_rows:
        frame.grid_rowconfigure(row, weight=0, minsize=_PAD)
    for i, row in enumerate(content_rows):
        weight = content_row_weights[i] if content_row_weights else 1
        row_kwargs: dict = {"weight": weight}
        if content_row_uniform is not None:
            row_kwargs["uniform"] = content_row_uniform
        frame.grid_rowconfigure(row, **row_kwargs)
    for col in pad_cols:
        frame.grid_columnconfigure(col, weight=0, minsize=_PAD)
    for col in content_cols:
        col_kwargs: dict = {"weight": 1}
        if content_col_uniform is not None:
            col_kwargs["uniform"] = content_col_uniform
        frame.grid_columnconfigure(col, **col_kwargs)


class TimetableFrame(ctk.CTkFrame):
    def __init__(self, parent, shell=None):
        super().__init__(parent, fg_color=COLOR_BACKGROUND)
        self._shell = shell
        self.local_start = self.log_ts = None
        self.elapsed_time = self._monotonic_anchor = 0.0
        self.running = False
        self._commit_plan = None
        self._commit_prepare_generation = 0
        self._plan_refresh_job = None
        self._prepare_retry_job = None
        self._start_notified = False
        self._keyboard_focus_indicators_active = False
        self._focus_highlight_defaults = {}

        self._user = get_user()
        self._commit_txn = CommitTransactionManager(collection, aggregations, client, self._user)

        _configure_padded_grid(
            self,
            pad_rows=_PAD_ROWS,
            pad_cols=_PAD_COLS,
            content_rows=(_ROW_BUTTONS, _ROW_TIMER),
            content_cols=(_COL_LEFT, _COL_RIGHT),
            content_row_uniform="main",
            content_col_uniform="main",
        )

        self.time_label = ctk.CTkLabel(self, text="00:00", font=("Arial", 32), text_color=COLOR_TEXT, fg_color=COLOR_BACKGROUND)
        self.time_label.grid(row=_ROW_TIMER, column=_COL_LEFT, sticky="nsew", columnspan=_COL_FULL_SPAN)

        self.toggle_run_button = ctk.CTkButton(self, text="Start", fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER, text_color=COLOR_TEXT, font=_PRIMARY_BTN_FONT, corner_radius=8, command=self.toggle_button)
        self.toggle_run_button.grid(row=_ROW_BUTTONS, column=_COL_LEFT, sticky="nsew", columnspan=_COL_FULL_SPAN)

        self.done_button = ctk.CTkButton(self, text="Done", fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER, text_color=COLOR_TEXT, text_color_disabled=COLOR_DISABLED_TEXT, font=_ACTION_BTN_FONT, corner_radius=8, command=self.show_entry_overlay, state="disabled")

        self.overlay_canvas = ctk.CTkFrame(self, corner_radius=0, fg_color=COLOR_BACKGROUND)
        _configure_padded_grid(
            self.overlay_canvas,
            pad_rows=_OVERLAY_PAD_ROWS,
            pad_cols=_PAD_COLS,
            content_rows=(_OVERLAY_ROW_NAME, _OVERLAY_ROW_DESC, _OVERLAY_ROW_BUTTONS),
            content_cols=(_COL_LEFT, _COL_RIGHT),
            content_row_weights=(2, 2, 3),
            content_row_uniform="overlay",
            content_col_uniform="overlay",
        )

        self.name_entry = CTkStickyPlaceholderEntry(self.overlay_canvas, placeholder_text="Name", font=("Arial", 18),
                     fg_color=COLOR_PRIMARY, border_color=COLOR_HOVER, placeholder_text_color=COLOR_DISABLED_TEXT, text_color=COLOR_TEXT)
        self.name_entry.grid(row=_OVERLAY_ROW_NAME, column=_COL_LEFT, sticky="nsew", columnspan=_COL_FULL_SPAN)
        self.name_entry.bind("<KeyPress>", lambda event: self.after_idle(self.validate_inputs))

        self.desc_entry = CTkStickyPlaceholderEntry(self.overlay_canvas, placeholder_text="Description", font=("Arial", 18),
                     fg_color=COLOR_PRIMARY, border_color=COLOR_HOVER, placeholder_text_color=COLOR_DISABLED_TEXT, text_color=COLOR_TEXT)
        self.desc_entry.grid(row=_OVERLAY_ROW_DESC, column=_COL_LEFT, sticky="nsew", columnspan=_COL_FULL_SPAN)
        self.desc_entry.bind("<KeyPress>", lambda event: self.after_idle(self.validate_inputs))

        self.continue_button = ctk.CTkButton(self.overlay_canvas, text="Continue", fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER,
                      command=self.continue_timer, text_color=COLOR_TEXT, font=_ACTION_BTN_FONT, corner_radius=8)
        self.continue_button.grid(row=_OVERLAY_ROW_BUTTONS, column=_COL_LEFT, sticky="nsew")

        self.log_button = ctk.CTkButton(self.overlay_canvas, text="Log Entry", fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER,
                                   command=self.submit_entry, text_color=COLOR_TEXT, text_color_disabled=COLOR_DISABLED_TEXT, font=_ACTION_BTN_FONT, corner_radius=8, state="disabled")
        self.log_button.grid(row=_OVERLAY_ROW_BUTTONS, column=_COL_RIGHT, sticky="nsew")

        self._setup_keyboard_navigation()

        self.sync_top_padding()
        self.after_idle(self.toggle_run_button.focus_set)

    # --- Keyboard navigation ---
    #
    # Three screens, each with its own fixed set of focusable controls:
    #   1. Idle/running   -> [toggle_run_button]                                  (Start / Pause)
    #   2. Paused         -> [toggle_run_button, done_button]                     (Continue / Done)
    #   3. Entry overlay  -> [name_entry, desc_entry, continue_button, log_button]
    #
    # Tab/Shift+Tab cycle within the current screen's controls. Space activates a
    # focused button. Enter in either text box submits the log entry. Whenever a
    # screen becomes active, focus is set explicitly (see hide_done_button,
    # toggle_button, and show_entry_overlay) so Tab/Space always have somewhere to
    # start from rather than relying on whatever last had focus.
    #
    # Accent focus borders appear after the user has pressed Tab at least once—not on
    # text fields. The lone Start/Pause button is never highlighted; when Continue
    # and Done are shown together, both can be highlighted.

    def _focusable_buttons(self):
        return (self.toggle_run_button, self.done_button, self.continue_button, self.log_button)

    def _focusable_entries(self):
        return (self.name_entry, self.desc_entry)

    def _all_focusable_widgets(self):
        return self._focusable_buttons() + self._focusable_entries()

    def _visible_focus_order(self):
        """The ordered, enabled controls for whichever screen is currently showing."""
        if self.overlay_canvas.winfo_ismapped():
            widgets = (self.name_entry, self.desc_entry, self.continue_button, self.log_button)
        elif self.done_button.winfo_ismapped():
            widgets = (self.toggle_run_button, self.done_button)
        else:
            widgets = (self.toggle_run_button,)
        return [w for w in widgets if self._widget_enabled(w)]

    @staticmethod
    def _widget_enabled(widget) -> bool:
        try:
            return widget.cget("state") != "disabled"
        except Exception:
            return True

    def _should_show_focus_highlight(self, widget) -> bool:
        if not self._keyboard_focus_indicators_active:
            return False
        if widget in self._focusable_entries():
            return False
        if widget is self.toggle_run_button:
            return self.done_button.winfo_ismapped()
        return True

    def _focus_step(self, current, direction):
        """Move focus to the next/previous control in the current screen; wraps around."""
        self._keyboard_focus_indicators_active = True
        order = self._visible_focus_order()
        if not order:
            return "break"
        idx = order.index(current) if current in order else (0 if direction > 0 else -1)
        order[(idx + direction) % len(order)].focus_set()
        return "break"

    def _submit_via_return(self, _event=None):
        """Enter (not Shift+Enter) in either text box submits the log entry."""
        if self.log_button.cget("state") == "normal":
            self.submit_entry()
        else:
            flash_error(self.log_button)
        return "break"

    def _install_focus_highlight(self, widget) -> None:
        """Draw a thin accent-colored border around focused controls when appropriate."""
        default_border_color = widget.cget("border_color")
        default_border_width = widget.cget("border_width")
        self._focus_highlight_defaults[widget] = (default_border_width, default_border_color)

        def on_focus_in(_event=None):
            if not self._should_show_focus_highlight(widget):
                return
            widget.configure(border_width=_FOCUS_HIGHLIGHT_WIDTH, border_color=ACCENT)

        def on_focus_out(_event=None):
            self._clear_focus_highlight(widget)

        widget.bind("<FocusIn>", on_focus_in, add="+")
        widget.bind("<FocusOut>", on_focus_out, add="+")

    def _clear_focus_highlight(self, widget) -> None:
        default = self._focus_highlight_defaults.get(widget)
        if default is None:
            return
        default_border_width, default_border_color = default
        widget.configure(border_width=default_border_width, border_color=default_border_color)


    def _setup_keyboard_navigation(self) -> None:
        for widget in self._all_focusable_widgets():
            widget.bind("<Tab>", lambda event, w=widget: self._focus_step(w, 1))
            widget.bind("<Shift-Tab>", lambda event, w=widget: self._focus_step(w, -1))
            widget.bind("<ISO_Left_Tab>", lambda event, w=widget: self._focus_step(w, -1))
            widget.bind("<Shift-ISO_Left_Tab>", lambda event, w=widget: self._focus_step(w, -1))

        for button in self._focusable_buttons():
            self._install_focus_highlight(button)

        for button in self._focusable_buttons():
            button.bind("<space>", lambda event, b=button: b.invoke())

        for entry in self._focusable_entries():
            entry.bind("<Return>", self._submit_via_return, add="+")
            entry.bind("<Shift-Return>", lambda event: None, add="+")

    def sync_top_padding(self) -> None:
        shell = self._shell
        if shell is not None and hasattr(shell, "content_top_pad_active"):
            top_pad = _PAD if shell.content_top_pad_active() else 0
        else:
            top_pad = _PAD

        self.grid_rowconfigure(_ROW_TOP_PAD, weight=0, minsize=top_pad)
        self.overlay_canvas.grid_rowconfigure(_ROW_TOP_PAD, weight=0, minsize=top_pad)

    def _notify_session_changed(self) -> None:
        shell = self._shell
        if shell is not None and hasattr(shell, "on_timetable_session_changed"):
            shell.on_timetable_session_changed()

    def discard_session(self) -> None:
        if self.running:
            self.elapsed_time += time.perf_counter() - self._monotonic_anchor
            self.running = False
        self._clear_commit_state()
        self._cancel_commit_jobs()
        self._commit_txn.abort_async()
        self.log_ts = self.local_start = self._commit_plan = None
        self.elapsed_time = 0.0
        self._monotonic_anchor = 0.0
        self._start_notified = False
        self.time_label.configure(text="00:00")
        self.hide_done_button("Start")
        self.done_button.configure(state="disabled")
        self.name_entry.delete(0, "end")
        self.desc_entry.delete(0, "end")
        self.name_entry._activate_placeholder()
        self.desc_entry._activate_placeholder()
        self.overlay_canvas.grid_forget()
        self._notify_session_changed()

    def toggle_button(self):
        if self.log_ts is None:
            threading.Thread(target=self.get_log_db_timestamp, name="get_log_db_timestamp", daemon=True).start()
        if not self.running:
            self._monotonic_anchor = time.perf_counter()
            self.running = True
            self.update_timer()
            self.hide_done_button("Pause")
        else:
            self.elapsed_time += time.perf_counter() - self._monotonic_anchor
            self.running = False
            self.toggle_run_button.configure(text="Continue", font=_ACTION_BTN_FONT)
            self.time_label.configure(text=format_time(self.elapsed_time))
            self.done_button.grid(row=_ROW_BUTTONS, column=_COL_RIGHT, sticky="nsew")
            self.toggle_run_button.grid_configure(column=_COL_LEFT, columnspan=1)
            self.toggle_run_button.focus_set()
        self._notify_session_changed()

    def get_log_db_timestamp(self):
        """Sets self.log_ts."""
        try:
            t0 = time.perf_counter()
            rows = list(db["Timetable"].aggregate([{"$project": {"server_time": "$$NOW"}}]))
            t1 = time.perf_counter()
            server_time = rows[0]["server_time"]
            rtt = (t1 - t0) / 2
            server_time -= timedelta(seconds=rtt)
            if self.local_start is not None:
                elapsed_local = t1 - self.local_start
                server_time -= timedelta(seconds=elapsed_local)
            self.log_ts = server_time
            self.done_button.configure(state="normal")
            if not self._start_notified:
                self._start_notified = True
                threading.Thread(
                    target=self._send_start_notification,
                    name="session_start_notify",
                    daemon=True,
                ).start()
        except Exception:
            if self.local_start == None:
                self.local_start = t0

    def _send_start_notification(self):
        try:
            local = to_local(self.log_ts)
            iso_year, iso_week, _ = local.isocalendar()
            iso_year_s, iso_week_s = str(iso_year), str(iso_week)
            context = fetch_week_goal_context_at_start(
                self._user, iso_year_s, iso_week_s, self.log_ts
            )
            common = {
                "hours": context["hours"],
                "goal_hours": context["goal_hours"],
                "active_days": context["active_days"],
                "goal_days": context["goal_days"],
            }
            post_notification(
                "session_start",
                self._user,
                format_start_message(self._user, for_self=False, **common),
                message_self=format_start_message(self._user, for_self=True, **common),
            )
        except Exception as exc:
            print(f"Failed to prepare start notification: {exc}")

    def hide_done_button(self, text):
        self.toggle_run_button.configure(text=text, font=_PRIMARY_BTN_FONT)
        self.toggle_run_button.grid_configure(column=_COL_LEFT, columnspan=_COL_FULL_SPAN)
        self.done_button.grid_forget()
        self._clear_focus_highlight(self.toggle_run_button)
        self.toggle_run_button.focus_set()

    def update_timer(self):
        if self.running:
            now = time.perf_counter()
            self.elapsed_time += now - self._monotonic_anchor
            self._monotonic_anchor = now
            self.time_label.configure(text=format_time(self.elapsed_time))
            time_until_next_second = 1.0 - (self.elapsed_time % 1.0)
            delay = int(time_until_next_second * 1000)
            self.after(delay, self.update_timer)

    def _cancel_commit_jobs(self):
        if self._plan_refresh_job is not None:
            self.after_cancel(self._plan_refresh_job)
            self._plan_refresh_job = None
        if self._prepare_retry_job is not None:
            self.after_cancel(self._prepare_retry_job)
            self._prepare_retry_job = None

    def _clear_commit_state(self):
        self._cancel_commit_jobs()
        self._commit_txn.abort_async()
        self._commit_plan = None
        self.log_button.configure(state="disabled")

    def _schedule_plan_refresh(self):
        self._cancel_commit_jobs()
        self._plan_refresh_job = self.after(
            TXN_REFRESH_INTERVAL_MS, self._on_plan_refresh_due
        )

    def _on_plan_refresh_due(self):
        self._plan_refresh_job = None
        if self._commit_plan is None:
            return
        generation = self._commit_prepare_generation
        log_ts = self.log_ts
        elapsed = int(self.elapsed_time)
        self._commit_txn.refresh_async(
            log_ts,
            elapsed,
            on_ready=lambda plan, _changed: self.after(0, lambda: self._on_commit_plan_refreshed(plan, generation)),
            on_error=lambda _exc: self.after(0, lambda: self._on_commit_refresh_failed(generation)),
        )

    def _on_commit_plan_refreshed(self, plan, generation):
        if generation != self._commit_prepare_generation:
            return
        self._commit_plan = plan
        self._schedule_plan_refresh()
        self.validate_inputs()

    def _on_commit_refresh_failed(self, generation):
        if generation != self._commit_prepare_generation:
            return
        self._prepare_retry_job = self.after(1000, self._on_plan_refresh_due)

    def _start_prepare_commit(self):
        self._commit_prepare_generation += 1
        generation = self._commit_prepare_generation
        log_ts = self.log_ts
        elapsed = int(self.elapsed_time)
        self.log_button.configure(state="disabled")

        self._commit_txn.begin_async(
            log_ts,
            elapsed,
            on_ready=lambda plan: self.after(0, lambda: self._on_commit_plan_ready(plan, generation)),
            on_error=lambda _exc: self.after(0, lambda: self._on_commit_prepare_failed(generation)),
        )

    def _on_commit_plan_ready(self, plan, generation):
        if generation != self._commit_prepare_generation:
            return
        self._commit_plan = plan
        self._schedule_plan_refresh()
        self.validate_inputs()

    def _on_commit_prepare_failed(self, generation):
        if generation != self._commit_prepare_generation:
            return
        self._prepare_retry_job = self.after(1000, self._start_prepare_commit)

    def submit_entry(self):
        self.log_button.configure(state="disabled")
        self._commit_txn.finalize_async(
            self.name_entry.get().strip(),
            self.desc_entry.get().strip(),
            on_success=lambda ts, broken: self.after(0, lambda: self._on_commit_success(ts, broken)),
            on_error=lambda _exc: self.after(0, lambda: self._on_commit_finalize_failed()),
        )

    def _on_commit_finalize_failed(self):
        flash_error(self.log_button)
        self.validate_inputs()

    def _persist_broken_records(self, broken_records, timestamp):
        """
        Write broken records to Status Meeting > Records.
        Uses $mergeObjects so old_value is preserved on subsequent breaks of the same slot
        within the same week: the first write sets old_value; later writes only update
        new_value and last_broken_ts.
        """
        if not broken_records:
            return
        local = to_local(timestamp)
        iso_year, iso_week, _ = local.isocalendar()

        set_fields = {}
        for record in broken_records:
            old = record["old_record"]
            new = record["new_record"]
            scope = old["scope"]
            time_type = old["time_type"]
            metric = old["metric"]

            if scope == "personal":
                slot = f"data.{iso_year}.{iso_week}.personal.{self._user}.{time_type}.{metric}"
            elif scope == "global":
                slot = f"data.{iso_year}.{iso_week}.global.{time_type}.{metric}"
            else:  # combined
                slot = f"data.{iso_year}.{iso_week}.combined.{time_type}.{metric}"

            defaults = {
                "old_value": old["value"],
                "scope": scope,
                "time_type": time_type,
                "metric": metric,
                "broken_by": self._user,
            }
            if scope == "global":
                defaults["old_holder"] = old.get("user")

            set_fields[slot] = {
                "$mergeObjects": [
                    defaults,
                    {"$ifNull": [f"${slot}", {}]},
                    {"new_value": new["value"], "last_broken_ts": timestamp},
                ]
            }

        try:
            status_meeting.update_one(
                {"_id": "Records"},
                [{"$set": set_fields}],
                upsert=True,
            )
        except Exception as e:
            print(f"Failed to persist broken records: {e}")

    def _send_end_notification(self, timestamp, plan, log_name: str):
        try:
            local = to_local(timestamp)
            iso_year, iso_week, _ = local.isocalendar()
            iso_year_s, iso_week_s = str(iso_year), str(iso_week)
            week_bucket = week_bucket_from_agg(plan.projected_user, iso_year_s, iso_week_s)
            personal_hours = int(week_bucket.get("time") or 0) / 3600
            active_days = int(week_bucket.get("active_days") or 0)
            goals_doc = status_meeting.find_one({"_id": "Goals"}) or {}
            user_goals = (week_goals(goals_doc, iso_year_s, iso_week_s).get(self._user)) or {}
            goal_hours = int(user_goals.get("hours") or 0)
            goal_days = int(user_goals.get("days") or 0)
            common = {
                "hours": personal_hours,
                "goal_hours": goal_hours,
                "active_days": active_days,
                "goal_days": goal_days,
            }
            post_notification(
                "session_end",
                self._user,
                format_end_message(self._user, for_self=False, **common),
                message_self=format_end_message(self._user, for_self=True, **common),
            )
        except Exception as exc:
            print(f"Failed to prepare end notification: {exc}")

    def _on_commit_success(self, timestamp, broken_records):
        plan = self._commit_plan
        log_name = self.name_entry.get().strip()
        if plan is not None:
            threading.Thread(
                target=self._send_end_notification,
                args=(timestamp, plan, log_name),
                daemon=True,
            ).start()

        if broken_records:
            global_records = [r for r in broken_records if r["old_record"]["scope"] == "global"]
            personal_records = [r for r in broken_records if r["old_record"]["scope"] == "personal"]
            combined_records = [r for r in broken_records if r["old_record"]["scope"] == "combined"]
            message = create_broken_records_notification(
                self._user, global_records, personal_records, combined_records, timestamp
            )
            post_notification("records_broken", self._user, message)
            threading.Thread(
                target=self._persist_broken_records,
                args=(broken_records, timestamp),
                daemon=True,
            ).start()

        self._cancel_commit_jobs()
        self.log_ts = self.local_start = self._commit_plan = None
        self.elapsed_time = 0.0
        self._start_notified = False
        self.time_label.configure(text="00:00")
        self.hide_done_button("Start")
        self.done_button.configure(state="disabled")
        self.name_entry.delete(0, "end")
        self.desc_entry.delete(0, "end")
        self.name_entry._activate_placeholder()
        self.desc_entry._activate_placeholder()
        self.overlay_canvas.grid_forget()
        self._notify_session_changed()

    def continue_timer(self):
        self._clear_commit_state()
        self.overlay_canvas.grid_forget()
        self.toggle_button()

    def validate_inputs(self, *args):
        if self._commit_plan and self.name_entry.get().strip() and self.desc_entry.get().strip():
            self.log_button.configure(state="normal")
        else:
            self.log_button.configure(state="disabled")

    def show_entry_overlay(self):
        self.overlay_canvas.grid(row=0, column=0, sticky="nsew", columnspan=5, rowspan=5)
        if self.name_entry.get() == "":
            self.name_entry._activate_placeholder()
        if self.desc_entry.get() == "":
            self.desc_entry._activate_placeholder()
        self._start_prepare_commit()
        self.name_entry.focus_set()
