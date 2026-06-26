"""Standalone ELG stats dashboard — highscores, period stats, goals, records, logs."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import customtkinter as ctk

from CtkSmartScrollableFrame import CtkSmartScrollableFrame
from meeting_app import MeetingApp, format_time
from period_model import (
    PeriodKeys,
    format_highscore_date,
    monday_midnight_local,
    period_keys,
    to_local,
)
from timetable_db import aggregations, collection, status_meeting, user as config_user

# --- Constants ---

SCOPE_WORLD = "world"
SCOPE_TEAM = "team"
SCOPE_USER = "user"

PAGE_STATS = "stats"
PAGE_RECORDS = "records"
PAGE_LOGS = "logs"

RECORDS_PAGE_SIZE = 25
LOGS_PAGE_SIZE = 50
WATCHER_DEBOUNCE_MS = 200

GOAL_COLOR_REACHED = "#00AD00"
GOAL_COLOR_MISSED = "#FF4444"
GOAL_COLOR_NONE = "#4A4A4A"

_SCOPE_LABELS = {
    "personal": "PB",
    "global": "World Record",
    "combined": "Team Record",
}
_METRIC_LABELS = {
    "total_time": "Time",
    "days_active": "Activity",
    "consecutive_days": "Consecutive Days",
    "consecutive_weeks": "Consecutive Weeks",
}

_PERIOD_TYPES = ("Year", "Month", "Week", "Day")
_PERIOD_MODES = ("all", "year", "month", "week", "day")
_PERIOD_MODE_LABELS = {
    "all": "All time",
    "year": "This year",
    "month": "This month",
    "week": "This week",
    "day": "Today",
}

_BTN = {"fg_color": "#000000", "hover_color": "#121212", "text_color": "white"}
_BTN_ACTIVE = {"fg_color": "#2C2C2C", "hover_color": "#3C3C3C", "text_color": "white"}
_MUTED = "#B0B0B0"
_CARD = "#23272B"

ctk.set_appearance_mode("Dark")


# --- Data layer ---


def list_users() -> list[str]:
    users = [u for u in collection.distinct("user") if u]
    return sorted(users)


def fetch_highscores() -> dict | None:
    return aggregations.find_one({"_id": "Highscores"})


def fetch_agg_doc(scope: str, selected_user: str | None) -> dict | None:
    if scope == SCOPE_TEAM:
        doc_id = "Combined"
    elif scope == SCOPE_USER and selected_user:
        doc_id = selected_user
    else:
        return None
    return aggregations.find_one({"_id": doc_id}) or {}


def highscore_slice(highscores: dict | None, scope: str, selected_user: str | None) -> dict | None:
    if not highscores:
        return None
    if scope == SCOPE_WORLD:
        return highscores.get("Global")
    if scope == SCOPE_TEAM:
        return highscores.get("Combined")
    if scope == SCOPE_USER and selected_user:
        return highscores.get(selected_user)
    return None


def bucket_from_agg(agg: dict, mode: str, keys: PeriodKeys) -> dict:
    years = agg.get("years") or {}
    if mode == "year":
        return years.get(keys.year) or {}
    if mode == "month":
        return ((years.get(keys.year) or {}).get("months") or {}).get(keys.month) or {}
    if mode == "week":
        return ((years.get(keys.iso_week_year) or {}).get("weeks") or {}).get(keys.iso_week) or {}
    if mode == "day":
        month_bucket = (years.get(keys.year) or {}).get("months") or {}
        return ((month_bucket.get(keys.month) or {}).get("days") or {}).get(keys.day) or {}
    return {}


def lifetime_totals(agg: dict) -> dict:
    years = agg.get("years") or {}
    total_time = sum(int(y.get("time") or 0) for y in years.values())
    active_years = sum(1 for y in years.values() if int(y.get("time") or 0) > 0)
    return {"time": total_time, "active_years": active_years, "year_count": len(years)}


def fetch_goals_doc() -> dict:
    doc = status_meeting.find_one({"_id": "Goals"}) or {}
    return {k: v for k, v in doc.items() if k != "_id"}


def week_goals(goals_doc: dict, iso_year: str, iso_week: str) -> dict:
    return (goals_doc.get(iso_year) or {}).get(iso_week) or {}


def week_bucket_from_agg(agg: dict, iso_year: str, iso_week: str) -> dict:
    return ((agg.get("years") or {}).get(iso_year) or {}).get("weeks", {}).get(iso_week) or {}


def first_log_timestamp(for_user: str | None = None) -> datetime | None:
    query: dict = {"user": {"$nin": [None, ""]}}
    if for_user:
        query["user"] = for_user
    doc = collection.find_one(query, sort=[("timestamp", 1)], projection={"timestamp": 1})
    return doc["timestamp"] if doc else None


def iter_iso_weeks(start: datetime, end: datetime):
    """Yield (iso_year, iso_week) from start through end inclusive."""
    local_start = to_local(start)
    local_end = to_local(end)
    cursor = monday_midnight_local(local_start)
    end_monday = monday_midnight_local(local_end)
    seen: set[tuple[str, str]] = set()
    while cursor <= end_monday:
        iso_year, iso_week, _ = cursor.isocalendar()
        key = (str(iso_year), str(iso_week))
        if key not in seen:
            seen.add(key)
            yield key
        cursor = cursor + timedelta(days=7)


def classify_user_goal_week(
    username: str,
    iso_year: str,
    iso_week: str,
    goals_doc: dict,
    agg: dict,
) -> str:
    """Return 'none', 'reached', or 'missed'."""
    goals = (week_goals(goals_doc, iso_year, iso_week).get(username) or {})
    goal_hours = goals.get("hours")
    goal_days = goals.get("days")
    if not goal_hours and not goal_days:
        return "none"

    bucket = week_bucket_from_agg(agg, iso_year, iso_week)
    actual_hours = int(bucket.get("time") or 0) / 3600
    actual_days = int(bucket.get("active_days") or 0)

    hours_ok = goal_hours is None or goal_hours == 0 or actual_hours >= float(goal_hours)
    days_ok = goal_days is None or goal_days == 0 or actual_days >= int(goal_days)
    if goal_hours and goal_days:
        reached = hours_ok and days_ok
    elif goal_hours:
        reached = hours_ok
    else:
        reached = days_ok
    return "reached" if reached else "missed"


def compute_goal_summary(
    scope: str,
    selected_user: str | None,
    users: list[str],
    goals_doc: dict,
    first_ts: datetime | None,
) -> dict:
    if not first_ts:
        return {"set": 0, "reached": 0, "missed": 0, "none": 0, "weeks": []}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    weeks = list(iter_iso_weeks(first_ts, now))
    counts = {"set": 0, "reached": 0, "missed": 0, "none": 0}
    week_rows: list[tuple[str, str, str]] = []

    if scope == SCOPE_USER and selected_user:
        agg = fetch_agg_doc(SCOPE_USER, selected_user) or {}
        for iso_year, iso_week in weeks:
            status = classify_user_goal_week(selected_user, iso_year, iso_week, goals_doc, agg)
            counts[status] += 1
            week_rows.append((iso_year, iso_week, status))
    else:
        user_aggs = {u: fetch_agg_doc(SCOPE_USER, u) or {} for u in users}
        for iso_year, iso_week in weeks:
            statuses = [
                classify_user_goal_week(u, iso_year, iso_week, goals_doc, user_aggs[u])
                for u in users
            ]
            had_goal = any(s in ("reached", "missed") for s in statuses)
            if not had_goal:
                team_status = "none"
            elif any(s == "missed" for s in statuses):
                team_status = "missed"
            else:
                team_status = "reached"
            counts[team_status] += 1
            week_rows.append((iso_year, iso_week, team_status))

    counts["set"] = counts["reached"] + counts["missed"]

    return {**counts, "weeks": week_rows}


def flatten_all_records() -> list[dict]:
    doc = status_meeting.find_one({"_id": "Records"}) or {}
    records: list[dict] = []
    for year, year_data in (doc.get("data") or {}).items():
        for week, week_data in (year_data or {}).items():
            for scope_key, scope_data in (week_data or {}).items():
                if scope_key == "personal":
                    for username, user_data in (scope_data or {}).items():
                        for time_type, metrics in (user_data or {}).items():
                            for metric, slot in (metrics or {}).items():
                                if isinstance(slot, dict):
                                    records.append(slot)
                elif scope_key in ("global", "combined"):
                    for time_type, metrics in (scope_data or {}).items():
                        for metric, slot in (metrics or {}).items():
                            if isinstance(slot, dict):
                                records.append(slot)
    records.sort(
        key=lambda x: x.get("last_broken_ts") or datetime.min,
        reverse=True,
    )
    return records


def fetch_logs_page(
    *,
    log_user: str | None,
    page: int,
    page_size: int = LOGS_PAGE_SIZE,
) -> tuple[list[dict], int]:
    query: dict = {"user": {"$nin": [None, ""]}}
    if log_user and log_user != "All":
        query["user"] = log_user
    total = collection.count_documents(query)
    cursor = (
        collection.find(query, projection={"timestamp": 1, "user": 1, "elapsed_time": 1})
        .sort("timestamp", -1)
        .skip(page * page_size)
        .limit(page_size)
    )
    return list(cursor), total


def logs_for_week(iso_year: str, iso_week: str, log_user: str | None = None) -> list[dict]:
    """Fetch logs in an ISO week (Stockholm), optionally filtered by user."""
    query: dict = {"user": {"$nin": [None, ""]}}
    if log_user:
        query["user"] = log_user
    docs = list(
        collection.find(
            query,
            projection={"timestamp": 1, "user": 1, "elapsed_time": 1},
        )
    )
    result = []
    for doc in docs:
        local = to_local(doc["timestamp"])
        y, w, _ = local.isocalendar()
        if str(y) == iso_year and str(w) == iso_week:
            result.append(doc)
    return result


def build_week_chart_data(logs: list[dict], users: list[str] | None = None) -> list[dict]:
    """Per-user weekday hours for chart bars."""
    user_day_seconds: dict[str, list[int]] = defaultdict(lambda: [0] * 7)
    for log in logs:
        u = log["user"]
        if users is not None and u not in users:
            continue
        weekday_idx = to_local(log["timestamp"]).weekday()
        user_day_seconds[u][weekday_idx] += int(log["elapsed_time"])

    rows = []
    for u in sorted(user_day_seconds.keys()):
        day_secs = user_day_seconds[u]
        total_hours = sum(day_secs) / 3600
        rows.append({
            "user": u,
            "total_hours": total_hours,
            "days_hours": [s / 3600 for s in day_secs],
        })
    return rows


def period_label(mode: str, keys: PeriodKeys) -> str:
    if mode == "all":
        return "All time"
    if mode == "year":
        return f"Year {keys.year}"
    if mode == "month":
        return f"{keys.year}-{keys.month}"
    if mode == "week":
        return f"Week {keys.iso_week}, {keys.iso_week_year}"
    return f"{keys.year}-{keys.month}-{keys.day}"


# --- Application ---


class StatsViewer(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("ELG Stats Dashboard")
        self.geometry("1280x800")
        self.minsize(960, 600)

        self._scope = SCOPE_WORLD
        self._page = PAGE_STATS
        self._selected_user = config_user if config_user and config_user != "Unknown" else None
        self._period_mode = "all"
        self._users: list[str] = []
        self._fetch_gen = 0
        self._refresh_job: str | None = None
        self._records_page = 0
        self._logs_page = 0
        self._logs_user_filter = "All"
        self._goals_grid_open = False

        self._scope_buttons: dict[str, ctk.CTkButton] = {}
        self._page_buttons: dict[str, ctk.CTkButton] = {}
        self._user_buttons: dict[str, ctk.CTkButton] = {}

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_sidebar()
        self._main = ctk.CTkFrame(self, fg_color="transparent")
        self._main.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=10)
        self._main.grid_rowconfigure(1, weight=1)
        self._main.grid_columnconfigure(0, weight=1)

        self._header = ctk.CTkLabel(self._main, text="", font=("Arial", 24, "bold"), anchor="w")
        self._header.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._content_host = ctk.CTkFrame(self._main, fg_color="transparent")
        self._content_host.grid(row=1, column=0, sticky="nsew")
        self._content_host.grid_rowconfigure(0, weight=1)
        self._content_host.grid_columnconfigure(0, weight=1)

        self._start_watchers()
        self._load_users_then_show()

    # --- Sidebar ---

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(4, weight=1)
        sidebar.grid_propagate(False)

        ctk.CTkLabel(sidebar, text="Scope", font=("Arial", 14, "bold")).grid(
            row=0, column=0, padx=12, pady=(12, 4), sticky="w"
        )
        scope_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        scope_frame.grid(row=1, column=0, sticky="ew", padx=8)
        for i, (key, label) in enumerate(
            ((SCOPE_WORLD, "World Records"), (SCOPE_TEAM, "Team Stats"), (SCOPE_USER, "Users"))
        ):
            btn = ctk.CTkButton(
                scope_frame, text=label, command=lambda k=key: self._set_scope(k), **_BTN
            )
            btn.grid(row=i, column=0, sticky="ew", pady=3)
            self._scope_buttons[key] = btn

        self._user_list_frame = CtkSmartScrollableFrame(sidebar, height=160, fg_color="#1A1A1A")
        self._user_list_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(8, 4))

        ctk.CTkLabel(sidebar, text="Pages", font=("Arial", 14, "bold")).grid(
            row=3, column=0, padx=12, pady=(8, 4), sticky="w"
        )
        page_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        page_frame.grid(row=4, column=0, sticky="new", padx=8)
        for i, (key, label) in enumerate(
            ((PAGE_STATS, "Stats"), (PAGE_RECORDS, "Record feed"), (PAGE_LOGS, "Logs"))
        ):
            btn = ctk.CTkButton(
                page_frame, text=label, command=lambda k=key: self._set_page(k), **_BTN
            )
            btn.grid(row=i, column=0, sticky="ew", pady=3)
            self._page_buttons[key] = btn

    def _load_users_then_show(self):
        def worker():
            try:
                users = list_users()
                self.after(0, lambda: self._on_users_loaded(users))
            except Exception as exc:
                self.after(0, lambda: self._on_users_loaded([], str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_users_loaded(self, users: list[str], error: str | None = None):
        self._users = users
        if not self._selected_user and users:
            self._selected_user = users[0]
        elif self._selected_user and self._selected_user not in users and users:
            self._selected_user = users[0]
        self._rebuild_user_list()
        self._update_nav_styles()
        self._refresh_current_page(error=error)

    def _rebuild_user_list(self):
        for w in self._user_list_frame.winfo_children():
            w.destroy()
        self._user_buttons.clear()
        if self._scope != SCOPE_USER:
            return
        for i, username in enumerate(self._users):
            is_config = username == config_user
            btn = ctk.CTkButton(
                self._user_list_frame,
                text=username,
                anchor="w",
                command=lambda u=username: self._set_user(u),
                fg_color="#1E3A1E" if is_config and username == self._selected_user else _BTN["fg_color"],
                hover_color=_BTN["hover_color"],
                text_color="white",
                font=("Arial", 13, "bold" if is_config else "normal"),
            )
            btn.grid(row=i, column=0, sticky="ew", pady=1, padx=2)
            self._user_buttons[username] = btn

    def _set_scope(self, scope: str):
        if self._scope == scope:
            return
        self._scope = scope
        self._goals_grid_open = False
        if scope == SCOPE_USER and config_user in self._users:
            self._selected_user = config_user
        self._update_nav_styles()
        self._rebuild_user_list()
        self._refresh_current_page()

    def _set_user(self, username: str):
        if self._selected_user == username:
            return
        self._selected_user = username
        self._scope = SCOPE_USER
        self._goals_grid_open = False
        self._update_nav_styles()
        self._rebuild_user_list()
        self._refresh_current_page()

    def _set_page(self, page: str):
        if self._page == page:
            return
        self._page = page
        self._update_nav_styles()
        self._refresh_current_page()

    def _update_nav_styles(self):
        for key, btn in self._scope_buttons.items():
            btn.configure(**(_BTN_ACTIVE if key == self._scope else _BTN))
        for key, btn in self._page_buttons.items():
            btn.configure(**(_BTN_ACTIVE if key == self._page else _BTN))
        self._user_list_frame.grid() if self._scope == SCOPE_USER else self._user_list_frame.grid_remove()

    def _page_title(self) -> str:
        if self._page == PAGE_RECORDS:
            return "Record feed"
        if self._page == PAGE_LOGS:
            return "Log browser"
        if self._scope == SCOPE_WORLD:
            return "World Records"
        if self._scope == SCOPE_TEAM:
            return "Team Stats"
        return f"User Stats — {self._selected_user or '—'}"

    # --- Content shell ---

    def _clear_content(self):
        for w in self._content_host.winfo_children():
            w.destroy()

    def _section_message(self, parent, text: str, color: str = _MUTED):
        ctk.CTkLabel(parent, text=text, font=("Arial", 16), text_color=color).pack(pady=20)

    def _refresh_current_page(self, error: str | None = None):
        self._header.configure(text=self._page_title())
        self._clear_content()
        if error:
            frame = ctk.CTkFrame(self._content_host, fg_color="transparent")
            frame.grid(sticky="nsew")
            self._section_message(frame, f"Connection error: {error}", "#FF6666")
            return

        if self._page == PAGE_STATS:
            self._show_stats_loading()
            self._async_fetch(self._fetch_stats_payload, self._render_stats_page)
        elif self._page == PAGE_RECORDS:
            self._show_records_loading()
            self._async_fetch(self._fetch_records_payload, self._render_records_page)
        else:
            self._show_logs_loading()
            self._async_fetch(self._fetch_logs_payload, self._render_logs_page)

    def _show_stats_loading(self):
        self._clear_content()
        scroll = CtkSmartScrollableFrame(self._content_host)
        scroll.grid(sticky="nsew")
        self._section_message(scroll, "Loading…")

    def _show_records_loading(self):
        self._show_stats_loading()

    def _show_logs_loading(self):
        self._show_stats_loading()

    def _async_fetch(self, fetch_fn: Callable[[], Any], apply_fn: Callable[[Any], None]):
        self._fetch_gen += 1
        gen = self._fetch_gen

        def worker():
            try:
                payload = fetch_fn()
                err = None
            except Exception as exc:
                payload = None
                err = str(exc)
            self.after(0, lambda: self._apply_fetch(gen, payload, err, apply_fn))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_fetch(self, gen: int, payload: Any, err: str | None, apply_fn: Callable[[Any], None]):
        if gen != self._fetch_gen:
            return
        self._clear_content()
        if err:
            frame = ctk.CTkFrame(self._content_host, fg_color="transparent")
            frame.grid(sticky="nsew")
            self._section_message(frame, f"Error: {err}", "#FF6666")
            return
        apply_fn(payload)

    # --- Fetch payloads ---

    def _fetch_stats_payload(self) -> dict:
        highscores = fetch_highscores()
        keys = period_keys(datetime.now(timezone.utc).replace(tzinfo=None))
        payload = {
            "highscores": highscores,
            "keys": keys,
            "scope": self._scope,
            "selected_user": self._selected_user,
            "period_mode": self._period_mode,
            "users": self._users,
        }
        if self._scope in (SCOPE_TEAM, SCOPE_USER):
            payload["agg"] = fetch_agg_doc(self._scope, self._selected_user)
            payload["goals_doc"] = fetch_goals_doc()
            first_ts = first_log_timestamp(
                self._selected_user if self._scope == SCOPE_USER else None
            )
            payload["goal_summary"] = compute_goal_summary(
                self._scope,
                self._selected_user,
                self._users,
                payload["goals_doc"],
                first_ts,
            )
            if self._scope in (SCOPE_TEAM, SCOPE_USER):
                iso_y, iso_w = keys.iso_week_year, keys.iso_week
                payload["week_logs"] = logs_for_week(
                    iso_y,
                    iso_w,
                    self._selected_user if self._scope == SCOPE_USER else None,
                )
        return payload

    def _fetch_records_payload(self) -> dict:
        all_records = flatten_all_records()
        start = self._records_page * RECORDS_PAGE_SIZE
        end = start + RECORDS_PAGE_SIZE
        return {
            "records": all_records[start:end],
            "total": len(all_records),
            "page": self._records_page,
        }

    def _fetch_logs_payload(self) -> dict:
        logs, total = fetch_logs_page(
            log_user=self._logs_user_filter,
            page=self._logs_page,
        )
        return {"logs": logs, "total": total, "page": self._logs_page}

    # --- Stats page render ---

    def _render_stats_page(self, payload: dict):
        scroll = CtkSmartScrollableFrame(self._content_host)
        scroll.grid(sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        if payload["scope"] == SCOPE_USER and not payload.get("selected_user"):
            self._section_message(scroll, "Select a user from the sidebar.")
            return

        slice_data = highscore_slice(
            payload.get("highscores"),
            payload["scope"],
            payload.get("selected_user"),
        )
        if not slice_data:
            self._section_message(scroll, "No highscore data available.")
            return

        row = 0
        if payload["scope"] == SCOPE_WORLD:
            ctk.CTkLabel(
                scroll, text="All-time world records", font=("Arial", 18, "bold"), anchor="w"
            ).grid(row=row, column=0, sticky="ew", padx=8, pady=(4, 8))
            row += 1
            row = self._render_peaks_block(scroll, row, slice_data, is_global=True)
        else:
            ctk.CTkLabel(
                scroll, text="All-time peaks", font=("Arial", 18, "bold"), anchor="w"
            ).grid(row=row, column=0, sticky="ew", padx=8, pady=(4, 4))
            row += 1
            row = self._render_peaks_block(scroll, row, slice_data, is_global=False)

            row = self._render_period_controls(scroll, row, payload)
            row = self._render_period_stats(scroll, row, payload)
            row = self._render_streaks_live(scroll, row, payload.get("agg") or {})
            row = self._render_charts(scroll, row, payload)
            row = self._render_goals_block(scroll, row, payload)

    def _render_peaks_block(self, parent, row: int, records: dict, *, is_global: bool) -> int:
        for time_type in _PERIOD_TYPES:
            if time_type not in records:
                continue
            frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
            frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
            frame.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(frame, text=time_type, font=("Arial", 16, "bold"), anchor="w").grid(
                row=0, column=0, sticky="w", padx=10, pady=4
            )
            inner_row = 1
            slot = records[time_type]
            if "time" in slot:
                inner_row = self._peak_metric_row(
                    frame, inner_row, "Total Time", format_time(slot["time"].get("value")),
                    slot["time"].get("date"), slot["time"].get("user") if is_global else None,
                )
            if time_type != "Day" and "activity" in slot:
                act = slot["activity"]
                inner_row = self._peak_metric_row(
                    frame, inner_row, "Activity",
                    f"{act.get('active_days', 0)}/{act.get('total_days', 0)} days ({act.get('value', 0):.1%})",
                    act.get("date"), act.get("user") if is_global else None,
                )
            row += 1

        if "consecutive" in records:
            frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
            frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
            ctk.CTkLabel(
                frame, text="Lifetime Consecutive", font=("Arial", 16, "bold"), anchor="w"
            ).grid(row=0, column=0, sticky="w", padx=10, pady=4)
            inner = 1
            for kind, label in (("days", "Days"), ("weeks", "Weeks")):
                streak = records["consecutive"].get(kind) or {}
                inner = self._peak_metric_row(
                    frame, inner, f"Consecutive {label}", str(streak.get("value", 0)),
                    streak.get("date"), streak.get("user") if is_global else None,
                )
            row += 1
        return row

    def _peak_metric_row(
        self, parent, row: int, label: str, value: str,
        date_val, holder: str | None,
    ) -> int:
        ctk.CTkLabel(parent, text=f"{label}:", font=("Arial", 14), anchor="w").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        ctk.CTkLabel(parent, text=value, font=("Arial", 14), anchor="w").grid(
            row=row, column=1, sticky="w", padx=10, pady=2
        )
        if date_val:
            extra = f"Set on: {format_highscore_date(date_val)}"
            if holder:
                extra += f" by {holder}"
            ctk.CTkLabel(parent, text=extra, font=("Arial", 11), text_color=_MUTED, anchor="w").grid(
                row=row + 1, column=0, columnspan=2, sticky="w", padx=10
            )
            return row + 2
        return row + 1

    def _render_period_controls(self, parent, row: int, _payload: dict) -> int:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=8, pady=(12, 4))
        ctk.CTkLabel(frame, text="Period view", font=("Arial", 18, "bold")).pack(side="left", padx=(0, 12))
        label_to_mode = {v: k for k, v in _PERIOD_MODE_LABELS.items()}
        menu = ctk.CTkOptionMenu(
            frame,
            values=list(_PERIOD_MODE_LABELS.values()),
            command=lambda label: self._on_period_mode_changed(label_to_mode[label]),
            **_BTN,
        )
        menu.set(_PERIOD_MODE_LABELS.get(self._period_mode, "All time"))
        menu.pack(side="left")
        return row + 1

    def _on_period_mode_changed(self, mode: str):
        self._period_mode = mode
        self._refresh_current_page()

    def _render_period_stats(self, parent, row: int, payload: dict) -> int:
        agg = payload.get("agg") or {}
        mode = payload["period_mode"]
        keys: PeriodKeys = payload["keys"]

        ctk.CTkLabel(
            parent, text=f"Stats — {period_label(mode, keys)}",
            font=("Arial", 16, "bold"), anchor="w",
        ).grid(row=row, column=0, sticky="ew", padx=8, pady=(8, 4))
        row += 1

        frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
        frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)

        if mode == "all":
            totals = lifetime_totals(agg)
            lines = [
                f"Total logged time: {format_time(totals['time'])}",
                f"Years with activity: {totals['active_years']} / {totals['year_count'] or '—'}",
            ]
        else:
            bucket = bucket_from_agg(agg, mode, keys)
            if not bucket:
                lines = ["No data for this period."]
            else:
                lines = [f"Time: {format_time(bucket.get('time', 0))}"]
                if mode != "day" and "active_days" in bucket:
                    ad = bucket.get("active_days", 0)
                    td = bucket.get("total_days", 0)
                    ratio = bucket.get("activity_ratio", 0)
                    lines.append(f"Activity: {ad}/{td} days ({ratio:.1%})")

        for i, line in enumerate(lines):
            ctk.CTkLabel(frame, text=line, font=("Arial", 14), anchor="w").grid(
                row=i, column=0, sticky="w", padx=10, pady=4
            )
        return row + 1

    def _render_streaks_live(self, parent, row: int, agg: dict) -> int:
        streaks = agg.get("streaks") or {}
        day_c = int((streaks.get("days") or {}).get("current") or 0)
        week_c = int((streaks.get("weeks") or {}).get("current") or 0)
        ctk.CTkLabel(
            parent, text="Current streaks", font=("Arial", 16, "bold"), anchor="w",
        ).grid(row=row, column=0, sticky="ew", padx=8, pady=(12, 4))
        row += 1
        frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
        frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
        ctk.CTkLabel(frame, text=f"Days: {day_c}  |  Weeks: {week_c}", font=("Arial", 14)).grid(
            row=0, column=0, sticky="w", padx=10, pady=8
        )
        return row + 1

    def _render_charts(self, parent, row: int, payload: dict) -> int:
        week_logs = payload.get("week_logs")
        if not week_logs:
            return row

        keys: PeriodKeys = payload["keys"]
        ctk.CTkLabel(
            parent,
            text=f"Week activity — {keys.iso_week_year}-W{keys.iso_week}",
            font=("Arial", 16, "bold"),
            anchor="w",
        ).grid(row=row, column=0, sticky="ew", padx=8, pady=(12, 4))
        row += 1

        chart_data = build_week_chart_data(week_logs)
        if not chart_data:
            frame = ctk.CTkFrame(parent, fg_color="transparent")
            frame.grid(row=row, column=0, sticky="ew", padx=8)
            self._section_message(frame, "No logs this week.")
            return row + 1

        max_hours = max((d["total_hours"] for d in chart_data), default=1.0) or 1.0
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        for entry in chart_data:
            block = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
            block.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
            block.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                block, text=f"{entry['user']} — {entry['total_hours']:.1f} h",
                font=("Arial", 13, "bold"), anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 2))

            bar_row = ctk.CTkFrame(block, fg_color="transparent")
            bar_row.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
            for di, hours in enumerate(entry["days_hours"]):
                col = ctk.CTkFrame(bar_row, fg_color="transparent")
                col.pack(side="left", expand=True, fill="both", padx=1)
                rel_h = hours / max_hours if max_hours else 0
                bar_h = max(int(rel_h * 60), 2 if hours > 0 else 0)
                color = MeetingApp._goal_color(hours, 1) if hours >= 1 else "#3A3A3A"
                ctk.CTkFrame(col, height=bar_h, fg_color=color, width=24).pack(side="bottom")
                ctk.CTkLabel(col, text=day_names[di], font=("Arial", 9), text_color=_MUTED).pack(side="bottom")

            row += 1
        return row

    def _render_goals_block(self, parent, row: int, payload: dict) -> int:
        summary = payload.get("goal_summary") or {}
        ctk.CTkLabel(
            parent, text="Goals (all time)", font=("Arial", 16, "bold"), anchor="w",
        ).grid(row=row, column=0, sticky="ew", padx=8, pady=(12, 4))
        row += 1

        frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
        frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
        text = (
            f"Weeks with goals set: {summary.get('set', 0)}  |  "
            f"Reached: {summary.get('reached', 0)}  |  "
            f"Missed: {summary.get('missed', 0)}  |  "
            f"No goal: {summary.get('none', 0)}"
        )
        ctk.CTkLabel(frame, text=text, font=("Arial", 13), anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=8
        )
        ctk.CTkButton(
            frame,
            text="Hide week grid" if self._goals_grid_open else "Show week grid",
            command=self._toggle_goals_grid,
            width=120,
            **_BTN,
        ).grid(row=0, column=1, padx=10, pady=8)
        row += 1

        if self._goals_grid_open and summary.get("weeks"):
            grid_frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
            grid_frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
            inner = ctk.CTkFrame(grid_frame, fg_color="transparent")
            inner.pack(fill="x", padx=8, pady=8)
            col = 0
            row_g = 0
            last_year = None
            for iso_year, iso_week, status in summary["weeks"]:
                if iso_year != last_year:
                    if last_year is not None:
                        row_g += 1
                        col = 0
                    ctk.CTkLabel(inner, text=iso_year, font=("Arial", 11, "bold"), text_color=_MUTED).grid(
                        row=row_g, column=0, columnspan=20, sticky="w", pady=(4, 2)
                    )
                    row_g += 1
                    col = 0
                    last_year = iso_year
                color = {
                    "reached": GOAL_COLOR_REACHED,
                    "missed": GOAL_COLOR_MISSED,
                    "none": GOAL_COLOR_NONE,
                }.get(status, GOAL_COLOR_NONE)
                cell = ctk.CTkFrame(inner, width=14, height=14, fg_color=color, corner_radius=3)
                cell.grid(row=row_g, column=col, padx=1, pady=1)
                cell.grid_propagate(False)
                col += 1
                if col >= 26:
                    col = 0
                    row_g += 1
            legend = ctk.CTkFrame(grid_frame, fg_color="transparent")
            legend.pack(fill="x", padx=8, pady=(0, 8))
            for label, color in (
                ("Reached", GOAL_COLOR_REACHED),
                ("Missed", GOAL_COLOR_MISSED),
                ("No goal", GOAL_COLOR_NONE),
            ):
                ctk.CTkFrame(legend, width=12, height=12, fg_color=color).pack(side="left", padx=(8, 2))
                ctk.CTkLabel(legend, text=label, font=("Arial", 10), text_color=_MUTED).pack(side="left", padx=(0, 8))
            row += 1
        return row

    def _toggle_goals_grid(self):
        self._goals_grid_open = not self._goals_grid_open
        if self._page == PAGE_STATS and self._scope in (SCOPE_TEAM, SCOPE_USER):
            self._refresh_current_page()

    # --- Record feed ---

    def _render_records_page(self, payload: dict):
        outer = ctk.CTkFrame(self._content_host, fg_color="transparent")
        outer.grid(sticky="nsew")
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        scroll = CtkSmartScrollableFrame(outer)
        scroll.grid(row=0, column=0, sticky="nsew")

        records = payload.get("records") or []
        if not records and payload.get("page", 0) == 0:
            self._section_message(scroll, "No records yet.")
        else:
            for slot in records:
                self._render_record_card(scroll, slot)

        nav = ctk.CTkFrame(outer, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", pady=8)
        total = payload.get("total", 0)
        page = payload.get("page", 0)
        max_page = max(0, (total - 1) // RECORDS_PAGE_SIZE)
        ctk.CTkLabel(
            nav, text=f"Page {page + 1} / {max_page + 1} ({total} records)", font=("Arial", 12)
        ).pack(side="left", padx=8)
        if page > 0:
            ctk.CTkButton(nav, text="← Prev", command=self._records_prev, width=80, **_BTN).pack(side="left", padx=4)
        if (page + 1) * RECORDS_PAGE_SIZE < total:
            ctk.CTkButton(nav, text="Next →", command=self._records_next, width=80, **_BTN).pack(side="left", padx=4)

    def _render_record_card(self, parent, slot: dict):
        scope = slot.get("scope", "")
        time_type = slot.get("time_type", "")
        metric = slot.get("metric", "")
        old_value = slot.get("old_value") or {}
        new_value = slot.get("new_value") or {}
        broken_by = slot.get("broken_by", "?")
        old_holder = slot.get("old_holder")
        ts = slot.get("last_broken_ts")

        scope_label = _SCOPE_LABELS.get(scope, scope)
        metric_label = _METRIC_LABELS.get(metric, metric)
        attribution = "Team" if scope == "combined" else broken_by
        old_str = MeetingApp._format_record_value(metric, old_value)
        new_str = MeetingApp._format_record_value(metric, new_value)

        card = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=5)
        card.pack(fill="x", pady=5, padx=5)
        header = f"{scope_label} — {attribution}"
        if scope == "global" and old_holder:
            header += f" (was {old_holder})"
        ctk.CTkLabel(card, text=header, font=("Arial", 14, "bold"), anchor="w").pack(
            anchor="w", padx=10, pady=(5, 2)
        )
        ctk.CTkLabel(
            card, text=f"{time_type} {metric_label}: {old_str} → {new_str}",
            font=("Arial", 12), text_color="#E0E0E0", anchor="w",
        ).pack(anchor="w", padx=10, pady=(0, 2))
        if ts:
            ctk.CTkLabel(
                card, text=f"Set on: {format_highscore_date(ts)}",
                font=("Arial", 10), text_color=_MUTED, anchor="w",
            ).pack(anchor="w", padx=10, pady=(0, 5))

    def _records_prev(self):
        self._records_page = max(0, self._records_page - 1)
        self._refresh_current_page()

    def _records_next(self):
        self._records_page += 1
        self._refresh_current_page()

    # --- Log browser ---

    def _render_logs_page(self, payload: dict):
        outer = ctk.CTkFrame(self._content_host, fg_color="transparent")
        outer.grid(sticky="nsew")
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        filt = ctk.CTkFrame(outer, fg_color="transparent")
        filt.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(filt, text="User:", font=("Arial", 14)).pack(side="left", padx=(0, 8))
        values = ["All"] + self._users
        menu = ctk.CTkOptionMenu(
            filt,
            values=values,
            command=self._on_logs_user_filter,
            **_BTN,
        )
        menu.set(self._logs_user_filter if self._logs_user_filter in values else "All")
        menu.pack(side="left")

        scroll = CtkSmartScrollableFrame(outer)
        scroll.grid(row=1, column=0, sticky="nsew")

        logs = payload.get("logs") or []
        if not logs:
            self._section_message(scroll, "No logs found.")
        else:
            for log in logs:
                ts = format_highscore_date(log["timestamp"])
                line = f"{ts}  |  {log['user']}  |  {format_time(log['elapsed_time'])}"
                ctk.CTkLabel(scroll, text=line, font=("Arial", 13), anchor="w").pack(
                    anchor="w", fill="x", padx=8, pady=2
                )

        nav = ctk.CTkFrame(outer, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="ew", pady=8)
        total = payload.get("total", 0)
        page = payload.get("page", 0)
        max_page = max(0, (total - 1) // LOGS_PAGE_SIZE)
        ctk.CTkLabel(
            nav, text=f"Page {page + 1} / {max_page + 1} ({total} logs)", font=("Arial", 12)
        ).pack(side="left", padx=8)
        if page > 0:
            ctk.CTkButton(nav, text="← Prev", command=self._logs_prev, width=80, **_BTN).pack(side="left", padx=4)
        if (page + 1) * LOGS_PAGE_SIZE < total:
            ctk.CTkButton(nav, text="Next →", command=self._logs_next, width=80, **_BTN).pack(side="left", padx=4)

    def _on_logs_user_filter(self, value: str):
        self._logs_user_filter = value
        self._logs_page = 0
        self._refresh_current_page()

    def _logs_prev(self):
        self._logs_page = max(0, self._logs_page - 1)
        self._refresh_current_page()

    def _logs_next(self):
        self._logs_page += 1
        self._refresh_current_page()

    # --- Watchers ---

    def _start_watchers(self):
        threading.Thread(target=self._agg_watcher, daemon=True, name="stats_agg_watcher").start()
        threading.Thread(target=self._status_watcher, daemon=True, name="stats_status_watcher").start()
        threading.Thread(target=self._logs_watcher, daemon=True, name="stats_logs_watcher").start()

    def _run_watcher(self, coll, pipeline, callback):
        resume_token = None
        while True:
            try:
                with coll.watch(pipeline, resume_after=resume_token) as stream:
                    for change in stream:
                        resume_token = stream.resume_token
                        callback()
            except Exception:
                time.sleep(1)

    def _schedule_refresh(self):
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(WATCHER_DEBOUNCE_MS, self._debounced_refresh)

    def _debounced_refresh(self):
        self._refresh_job = None
        self._refresh_current_page()

    def _agg_watcher(self):
        pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}]

        def on_change():
            self.after(0, self._schedule_refresh)

        self._run_watcher(aggregations, pipeline, on_change)

    def _status_watcher(self):
        pipeline = [
            {
                "$match": {
                    "operationType": {"$in": ["insert", "update", "replace"]},
                    "documentKey._id": {"$in": ["Goals", "Records"]},
                }
            }
        ]

        def on_change():
            self.after(0, self._schedule_refresh)

        self._run_watcher(status_meeting, pipeline, on_change)

    def _logs_watcher(self):
        pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}]

        def on_change():
            self.after(0, self._schedule_refresh)

        self._run_watcher(collection, pipeline, on_change)


if __name__ == "__main__":
    app = StatsViewer()
    app.mainloop()
