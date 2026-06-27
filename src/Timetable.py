import customtkinter as ctk
import threading
import time
from datetime import datetime, timedelta
from commit_transaction import CommitTransactionManager, TXN_REFRESH_INTERVAL_MS
from CTkStickyPlaceholderEntry import CTkStickyPlaceholderEntry
from notifications import (
    create_broken_records_notification,
    fetch_week_goal_context_at_start,
    format_end_message,
    format_start_message,
    format_time,
    post_notification,
    week_bucket_from_agg,
)
from period_model import to_local
from stats_viewer import week_goals
from timetable_db import aggregations, client, collection, db, status_meeting, user
from utils import flash_error

COLOR_BACKGROUND=   "#000000"
COLOR_SELECTED=     "#191919"
COLOR_PRIMARY=      "#232323"
COLOR_HOVER=        "#333333"
COLOR_DISABLED_TEXT="#666666"
COLOR_TEXT=         "#FFFFFF"

class TimetableFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=COLOR_BACKGROUND)
        self.local_start = self.log_ts = None
        self.elapsed_time = self._monotonic_anchor = 0.0
        self.running = False
        self._commit_plan = None
        self._commit_prepare_generation = 0
        self._plan_refresh_job = None
        self._prepare_retry_job = None
        self._start_notified = False

        self._commit_txn = CommitTransactionManager(collection, aggregations, client, user)

        self.grid_rowconfigure([0, 1], weight=1, uniform='a')
        self.grid_columnconfigure([0, 1], weight=1, uniform='a')

        self.time_label = ctk.CTkLabel(self, text="00:00", font=("Arial", 32), text_color=COLOR_TEXT, fg_color=COLOR_BACKGROUND)
        self.time_label.grid(row=1, column=0, sticky='nsew', columnspan=2)

        self.toggle_run_button = ctk.CTkButton(self, text="Start", fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER, text_color=COLOR_TEXT, font=("Arial", 24), corner_radius=8, command=self.toggle_button)
        self.toggle_run_button.grid(row=0, column=0, sticky='nsew', padx=4, pady=4, columnspan=2)

        self.done_button = ctk.CTkButton(self, text="Done", fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER, text_color=COLOR_TEXT, text_color_disabled=COLOR_DISABLED_TEXT, font=("Arial", 14), corner_radius=8, command=self.show_entry_overlay, state="disabled")

        self.overlay_canvas = ctk.CTkFrame(self, corner_radius=0, fg_color=COLOR_BACKGROUND)
        self.overlay_canvas.grid_rowconfigure([0, 1, 2], weight=1, uniform='a')
        self.overlay_canvas.grid_rowconfigure(2, weight=2)
        self.overlay_canvas.grid_columnconfigure([0, 1], weight=1, uniform='a')

        self.name_entry = CTkStickyPlaceholderEntry(self.overlay_canvas, placeholder_text="Name", font=("Arial", 18),
                     fg_color=COLOR_PRIMARY, border_color=COLOR_HOVER, placeholder_text_color=COLOR_DISABLED_TEXT, text_color=COLOR_TEXT)
        self.name_entry.grid(row=0, column=0, padx=4, pady=4, sticky='nsew', columnspan=2)
        self.name_entry.bind("<KeyPress>", lambda event: self.after_idle(self.validate_inputs))

        self.desc_entry = CTkStickyPlaceholderEntry(self.overlay_canvas, placeholder_text="Description", font=("Arial", 18),
                     fg_color=COLOR_PRIMARY, border_color=COLOR_HOVER, placeholder_text_color=COLOR_DISABLED_TEXT, text_color=COLOR_TEXT)
        self.desc_entry.grid(row=1, column=0, padx=4, pady=4, sticky='nsew', columnspan=2)
        self.desc_entry.bind("<KeyPress>", lambda event: self.after_idle(self.validate_inputs))

        ctk.CTkButton(self.overlay_canvas, text="Continue", fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER,
                      command=self.continue_timer, text_color=COLOR_TEXT, font=("Arial", 14), corner_radius=8
        ).grid(row=2, column=0, padx=4, pady=4, sticky='nsew')

        self.log_button = ctk.CTkButton(self.overlay_canvas, text="Log Entry", fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER,
                                   command=self.submit_entry, text_color=COLOR_TEXT, text_color_disabled=COLOR_DISABLED_TEXT, font=("Arial", 14), corner_radius=8, state="disabled")
        self.log_button.grid(row=2, column=1, padx=4, pady=4, sticky='nsew')

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
            self.toggle_run_button.configure(text="Continue", font=("Arial", 14))
            self.time_label.configure(text=format_time(self.elapsed_time))
            self.done_button.grid(row=0, column=1, sticky='nsew', padx=4, pady=4)
            self.toggle_run_button.grid_configure(columnspan=1)

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
                user, iso_year_s, iso_week_s, self.log_ts
            )
            common = {
                "hours": context["hours"],
                "goal_hours": context["goal_hours"],
                "active_days": context["active_days"],
                "goal_days": context["goal_days"],
            }
            post_notification(
                "session_start",
                user,
                format_start_message(user, for_self=False, **common),
                message_self=format_start_message(user, for_self=True, **common),
            )
        except Exception as exc:
            print(f"Failed to prepare start notification: {exc}")

    def hide_done_button(self, text):
        self.toggle_run_button.configure(text=text, font=("Arial", 24))
        self.toggle_run_button.grid_configure(columnspan=2)
        self.done_button.grid_forget()

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
                slot = f"data.{iso_year}.{iso_week}.personal.{user}.{time_type}.{metric}"
            elif scope == "global":
                slot = f"data.{iso_year}.{iso_week}.global.{time_type}.{metric}"
            else:  # combined
                slot = f"data.{iso_year}.{iso_week}.combined.{time_type}.{metric}"

            defaults = {
                "old_value": old["value"],
                "scope": scope,
                "time_type": time_type,
                "metric": metric,
                "broken_by": user,
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
            user_goals = (week_goals(goals_doc, iso_year_s, iso_week_s).get(user)) or {}
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
                user,
                format_end_message(user, for_self=False, **common),
                message_self=format_end_message(user, for_self=True, **common),
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
                user, global_records, personal_records, combined_records, timestamp
            )
            post_notification("records_broken", user, message)
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
        self.overlay_canvas.grid(row=0, column=0, sticky="nsew", columnspan=2, rowspan=2)
        if self.name_entry.get() == "":
            self.name_entry._activate_placeholder()
        if self.desc_entry.get() == "":
            self.desc_entry._activate_placeholder()
        self._start_prepare_commit()
