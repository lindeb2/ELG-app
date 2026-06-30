"""Standalone ELG stats dashboard — highscores, period stats, goals, records, logs."""
from __future__ import annotations

import threading
import time
import tkinter as tk
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import customtkinter as ctk

from zoneinfo import ZoneInfo

from CtkSmartScrollableFrame import CtkSmartScrollableFrame
from meeting_app import MeetingFrame, format_time
from period_model import (
    APP_TIMEZONE,
    PeriodKeys,
    as_utc,
    format_highscore_date,
    monday_midnight_local,
    period_keys,
    to_local,
)
from timetable_db import aggregations, collection, get_user, status_meeting

_TZ = ZoneInfo(APP_TIMEZONE)

# --- Constants ---

VIEW_WORLD = "world"
VIEW_TEAM = "team"
VIEW_USER = "user"
VIEW_RECORDS = "records"
VIEW_LOGS = "logs"

STATS_VIEWS = frozenset({VIEW_WORLD, VIEW_TEAM, VIEW_USER})

RECORDS_PAGE_SIZE = 25
LOGS_PAGE_SIZE = 500
LOGS_PAGE_SIZE_OPTIONS = ("10", "100", "250", "500", "1000", "all")
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
    "year": "Year",
    "month": "Month",
    "week": "ISO week",
    "day": "Day",
}

_DATE_FILTER_MODES = ("none", "after", "before", "between")
_DATE_FILTER_LABELS = {
    "none": "Any date",
    "after": "After",
    "before": "Before",
    "between": "Between",
}
_ELAPSED_FILTER_MODES = ("none", "gt", "lt", "between")
_ELAPSED_FILTER_LABELS = {
    "none": "Any duration",
    "gt": "Greater than",
    "lt": "Less than",
    "between": "Between",
}
_SORT_OPTIONS = {
    "timestamp_desc": "Newest first",
    "timestamp_asc": "Oldest first",
    "elapsed_desc": "Longest first",
    "elapsed_asc": "Shortest first",
    "user_asc": "User A–Z",
    "user_desc": "User Z–A",
}

_BTN = {"fg_color": "#000000", "hover_color": "#121212", "text_color": "white"}
_BTN_ACTIVE = {"fg_color": "#2C2C2C", "hover_color": "#3C3C3C", "text_color": "white"}
_MENU = {
    "fg_color": "#000000",
    "button_color": "#121212",
    "button_hover_color": "#2C2C2C",
    "text_color": "white",
}
_MUTED = "#B0B0B0"
_CARD = "#23272B"

# --- Data layer ---


def list_users() -> list[str]:
    users = [u for u in collection.distinct("user") if u]
    return sorted(users)


def fetch_highscores() -> dict | None:
    return aggregations.find_one({"_id": "Highscores"})


def _doc_lookup(doc: dict, key: str | int):
    """MongoDB subdocuments may use str or int keys."""
    if not isinstance(doc, dict):
        return None
    candidates: list[str | int] = [key]
    if isinstance(key, str) and key.isdigit():
        ik = int(key)
        candidates.extend([ik, key.zfill(2)])
    elif isinstance(key, int):
        candidates.extend([str(key), str(key).zfill(2)])
    seen: set[str | int] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate in doc:
            return doc[candidate]
    return None


def fetch_agg_doc(view: str, selected_user: str | None) -> dict | None:
    if view == VIEW_TEAM:
        doc_id = "Combined"
    elif view == VIEW_USER and selected_user:
        doc_id = selected_user
    else:
        return None
    return aggregations.find_one({"_id": doc_id}) or {}


def highscore_slice(highscores: dict | None, view: str, selected_user: str | None) -> dict | None:
    if not highscores:
        return None
    if view == VIEW_WORLD:
        return highscores.get("Global")
    if view == VIEW_TEAM:
        return highscores.get("Combined")
    if view == VIEW_USER and selected_user:
        return highscores.get(selected_user)
    return None


def bucket_from_agg(agg: dict, mode: str, keys: PeriodKeys) -> dict:
    years = agg.get("years") or {}
    if mode == "year":
        result = _doc_lookup(years, keys.year)
        return result if isinstance(result, dict) else {}
    if mode == "month":
        year_bucket = _doc_lookup(years, keys.year) or {}
        months = year_bucket.get("months") if isinstance(year_bucket, dict) else {}
        result = _doc_lookup(months or {}, keys.month)
        return result if isinstance(result, dict) else {}
    if mode == "week":
        year_bucket = _doc_lookup(years, keys.iso_week_year) or {}
        weeks = year_bucket.get("weeks") if isinstance(year_bucket, dict) else {}
        result = _doc_lookup(weeks or {}, keys.iso_week)
        return result if isinstance(result, dict) else {}
    if mode == "day":
        year_bucket = _doc_lookup(years, keys.year) or {}
        month_bucket = _doc_lookup((year_bucket.get("months") if isinstance(year_bucket, dict) else {}) or {}, keys.month)
        days = month_bucket.get("days") if isinstance(month_bucket, dict) else {}
        result = _doc_lookup(days or {}, keys.day)
        return result if isinstance(result, dict) else {}
    return {}


def lifetime_totals(agg: dict) -> dict:
    years = agg.get("years") or {}
    total_time = sum(int(y.get("time") or 0) for y in years.values())
    active_years = sum(1 for y in years.values() if int(y.get("time") or 0) > 0)
    return {"time": total_time, "active_years": active_years, "year_count": len(years)}


def week_goals(goals_doc: dict, iso_year: str, iso_week: str) -> dict:
    year_bucket = _doc_lookup(goals_doc, iso_year) or {}
    result = _doc_lookup(year_bucket, iso_week)
    return result if isinstance(result, dict) else {}


def week_bucket_from_agg(agg: dict, iso_year: str, iso_week: str) -> dict:
    year_bucket = _doc_lookup(agg.get("years") or {}, iso_year) or {}
    weeks = year_bucket.get("weeks") if isinstance(year_bucket, dict) else {}
    result = _doc_lookup(weeks or {}, iso_week)
    return result if isinstance(result, dict) else {}


def last_log_timestamp(for_user: str | None = None) -> datetime | None:
    query: dict = {"user": {"$nin": [None, ""]}}
    if for_user:
        query["user"] = for_user
    doc = collection.find_one(query, sort=[("timestamp", -1)], projection={"timestamp": 1})
    return doc["timestamp"] if doc else None


def display_streak_current(agg: dict, view: str, selected_user: str | None) -> tuple[int, int]:
    """Show 0 when the stored streak is not active in the current day/week."""
    streaks = agg.get("streaks") or {}
    day_stored = int((streaks.get("days") or {}).get("current") or 0)
    week_stored = int((streaks.get("weeks") or {}).get("current") or 0)
    if day_stored == 0 and week_stored == 0:
        return 0, 0

    if view == VIEW_USER and selected_user:
        last_ts = last_log_timestamp(selected_user)
    elif view == VIEW_TEAM:
        last_ts = last_log_timestamp()
    else:
        return day_stored, week_stored

    if not last_ts:
        return 0, 0

    now_keys = period_keys(datetime.now(timezone.utc).replace(tzinfo=None))
    last_keys = period_keys(last_ts)
    day_active = (
        last_keys.year == now_keys.year
        and last_keys.month == now_keys.month
        and last_keys.day == now_keys.day
    )
    week_active = (
        last_keys.iso_week_year == now_keys.iso_week_year
        and last_keys.iso_week == now_keys.iso_week
    )
    return (day_stored if day_active else 0, week_stored if week_active else 0)


def parse_local_datetime(text: str) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            local = datetime.strptime(text, fmt).replace(tzinfo=_TZ)
            return as_utc(local).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def parse_elapsed_seconds(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if ":" in text:
        parts = text.split(":")
        try:
            parts = [int(p) for p in parts]
        except ValueError:
            return None
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def period_keys_from_selection(
    mode: str,
    year: str,
    month: str,
    week: str,
    day: str,
) -> PeriodKeys:
    if mode == "all":
        return period_keys(datetime.now(timezone.utc).replace(tzinfo=None))
    y, m, d = int(year), int(month), int(day)
    if mode == "year":
        local = datetime(y, 1, 1, tzinfo=_TZ)
    elif mode == "month":
        local = datetime(y, m, 1, tzinfo=_TZ)
    elif mode == "week":
        local = datetime.fromisocalendar(y, int(week), 1).replace(tzinfo=_TZ)
    else:
        local = datetime(y, m, d, tzinfo=_TZ)
    return period_keys(as_utc(local).replace(tzinfo=None))


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
    goals = week_goals(goals_doc, iso_year, iso_week).get(username) or {}
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
    view: str,
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

    if view == VIEW_USER and selected_user:
        agg = fetch_agg_doc(VIEW_USER, selected_user) or {}
        for iso_year, iso_week in weeks:
            status = classify_user_goal_week(
                selected_user, iso_year, iso_week, goals_doc, agg
            )
            counts[status] += 1
            week_rows.append((iso_year, iso_week, status))
    else:
        user_aggs = {u: fetch_agg_doc(VIEW_USER, u) or {} for u in users}
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


def build_logs_query(
    *,
    users: set[str] | None,
    date_mode: str,
    date_after: str,
    date_before: str,
    elapsed_mode: str,
    elapsed_min: str,
    elapsed_max: str,
    search: str,
) -> dict:
    query: dict = {"user": {"$nin": [None, ""]}}
    if users:
        query["user"] = {"$in": sorted(users)}

    if search.strip():
        pattern = {"$regex": search.strip(), "$options": "i"}
        query["$or"] = [{"name": pattern}, {"description": pattern}]

    ts_filter: dict = {}
    after_dt = parse_local_datetime(date_after)
    before_dt = parse_local_datetime(date_before)
    if date_mode == "after" and after_dt:
        ts_filter["$gte"] = after_dt
    elif date_mode == "before" and before_dt:
        ts_filter["$lt"] = before_dt
    elif date_mode == "between":
        if after_dt:
            ts_filter["$gte"] = after_dt
        if before_dt:
            ts_filter["$lt"] = before_dt
    if ts_filter:
        query["timestamp"] = ts_filter

    elapsed_filter: dict = {}
    e_min = parse_elapsed_seconds(elapsed_min)
    e_max = parse_elapsed_seconds(elapsed_max)
    if elapsed_mode == "gt" and e_min is not None:
        elapsed_filter["$gt"] = e_min
    elif elapsed_mode == "lt" and e_max is not None:
        elapsed_filter["$lt"] = e_max
    elif elapsed_mode == "between":
        if e_min is not None:
            elapsed_filter["$gte"] = e_min
        if e_max is not None:
            elapsed_filter["$lte"] = e_max
    if elapsed_filter:
        query["elapsed_time"] = elapsed_filter

    return query


def logs_sort_spec(sort_key: str) -> list[tuple[str, int]]:
    mapping = {
        "timestamp_desc": [("timestamp", -1)],
        "timestamp_asc": [("timestamp", 1)],
        "elapsed_desc": [("elapsed_time", -1), ("timestamp", -1)],
        "elapsed_asc": [("elapsed_time", 1), ("timestamp", -1)],
        "user_asc": [("user", 1), ("timestamp", -1)],
        "user_desc": [("user", -1), ("timestamp", -1)],
    }
    return mapping.get(sort_key, [("timestamp", -1)])


def fetch_logs_page(
    *,
    filters: dict,
    page: int,
    page_size: int | None = LOGS_PAGE_SIZE,
) -> tuple[list[dict], int]:
    filter_args = dict(filters)
    sort_key = filter_args.pop("sort", "timestamp_desc")
    query = build_logs_query(**filter_args)
    total = collection.count_documents(query)
    cursor = (
        collection.find(
            query,
            projection={
                "timestamp": 1,
                "user": 1,
                "elapsed_time": 1,
                "name": 1,
                "description": 1,
            },
        )
        .sort(logs_sort_spec(sort_key))
    )
    if page_size is not None:
        cursor = cursor.skip(page * page_size).limit(page_size)
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


# --- Application ---


class StatsFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent)

        self._view = VIEW_WORLD
        config_user = get_user()
        self._selected_user = config_user if config_user and config_user != "Unknown" else None
        now_keys = period_keys(datetime.now(timezone.utc).replace(tzinfo=None))
        self._period_mode = "all"
        self._period_year = now_keys.year
        self._period_month = now_keys.month
        self._period_week = now_keys.iso_week
        self._period_day = now_keys.day
        self._users: list[str] = []
        self._fetch_gen = 0
        self._refresh_job: str | None = None
        self._refresh_paused = False
        self._records_page = 0
        self._logs_page = 0
        self._logs_page_size = LOGS_PAGE_SIZE
        self._goals_grid_open = False
        self._log_user_checks: dict[str, ctk.BooleanVar] = {}
        self._log_date_mode = "none"
        self._log_date_after = ""
        self._log_date_before = ""
        self._log_elapsed_mode = "none"
        self._log_elapsed_min = ""
        self._log_elapsed_max = ""
        self._log_search = ""
        self._log_sort = "timestamp_desc"

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
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

    def _is_view_active(self) -> bool:
        try:
            return self.winfo_ismapped() and self.winfo_viewable()
        except tk.TclError:
            return False

    def pause_refresh(self) -> None:
        self._refresh_paused = True
        self._fetch_gen += 1
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None

    def resume_refresh(self) -> None:
        if not self._refresh_paused:
            return
        self._refresh_paused = False
        if self._is_view_active():
            self._refresh_current_page()

    # --- Sidebar ---

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(2, weight=1)
        sidebar.grid_propagate(False)

        ctk.CTkLabel(sidebar, text="Views", font=("Arial", 14, "bold")).grid(
            row=0, column=0, padx=12, pady=(12, 4), sticky="w"
        )
        nav_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_frame.grid(row=1, column=0, sticky="ew", padx=8)
        for i, (key, label) in enumerate(
            (
                (VIEW_WORLD, "World Records"),
                (VIEW_TEAM, "Team Stats"),
                (VIEW_USER, "Users"),
                (VIEW_RECORDS, "Record feed"),
                (VIEW_LOGS, "Logs"),
            )
        ):
            btn = ctk.CTkButton(
                nav_frame, text=label, command=lambda k=key: self._set_view(k), **_BTN
            )
            btn.grid(row=i, column=0, sticky="ew", pady=3)
            self._nav_buttons[key] = btn

        self._user_list_frame = CtkSmartScrollableFrame(sidebar, height=200, fg_color="#1A1A1A")
        self._user_list_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(8, 12))

    def _load_users_then_show(self):
        def worker():
            try:
                users = list_users()
                self.after_idle(lambda: self._on_users_loaded(users))
            except Exception as exc:
                self.after_idle(lambda: self._on_users_loaded([], str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_users_loaded(self, users: list[str], error: str | None = None):
        if not self._is_view_active():
            self._users = users
            if not self._selected_user and users:
                self._selected_user = users[0]
            elif self._selected_user and self._selected_user not in users and users:
                self._selected_user = users[0]
            for u in users:
                if u not in self._log_user_checks:
                    self._log_user_checks[u] = ctk.BooleanVar(value=False)
            return
        self._users = users
        if not self._selected_user and users:
            self._selected_user = users[0]
        elif self._selected_user and self._selected_user not in users and users:
            self._selected_user = users[0]
        for u in users:
            if u not in self._log_user_checks:
                self._log_user_checks[u] = ctk.BooleanVar(value=False)
        self._rebuild_user_list()
        self._update_nav_styles()
        self._refresh_current_page(error=error)

    def _rebuild_user_list(self):
        for w in self._user_list_frame.winfo_children():
            w.destroy()
        self._user_buttons.clear()
        if self._view != VIEW_USER:
            return
        for i, username in enumerate(self._users):
            is_config = username == get_user()
            is_sel = username == self._selected_user
            btn = ctk.CTkButton(
                self._user_list_frame,
                text=username,
                anchor="w",
                command=lambda u=username: self._set_user(u),
                fg_color="#1E3A1E" if is_config and is_sel else (
                    "#2C2C2C" if is_sel else _BTN["fg_color"]
                ),
                hover_color=_BTN["hover_color"],
                text_color="white",
                font=("Arial", 13, "bold" if is_config else "normal"),
            )
            btn.grid(row=i, column=0, sticky="ew", pady=1, padx=2)
            self._user_buttons[username] = btn

    def _set_view(self, view: str):
        if self._view == view and view != VIEW_USER:
            return
        self._view = view
        self._goals_grid_open = False
        config_user = get_user()
        if view == VIEW_USER and config_user in self._users:
            self._selected_user = config_user
        self._update_nav_styles()
        self._rebuild_user_list()
        self._refresh_current_page()

    def _set_user(self, username: str):
        if self._selected_user == username and self._view == VIEW_USER:
            return
        self._selected_user = username
        self._view = VIEW_USER
        self._goals_grid_open = False
        self._update_nav_styles()
        self._rebuild_user_list()
        self._refresh_current_page()

    def _update_nav_styles(self):
        for key, btn in self._nav_buttons.items():
            active = key == self._view
            btn.configure(**(_BTN_ACTIVE if active else _BTN))
        if self._view == VIEW_USER:
            self._user_list_frame.grid()
        else:
            self._user_list_frame.grid_remove()

    def _page_title(self) -> str:
        if self._view == VIEW_RECORDS:
            return "Record feed"
        if self._view == VIEW_LOGS:
            return "Log browser"
        if self._view == VIEW_WORLD:
            return "World Records"
        if self._view == VIEW_TEAM:
            return "Team Stats"
        return f"User Stats — {self._selected_user or '—'}"

    # --- Content shell ---

    def _clear_content(self):
        for w in self._content_host.winfo_children():
            w.destroy()

    def _section_message(self, parent, text: str, color: str = _MUTED):
        ctk.CTkLabel(parent, text=text, font=("Arial", 16), text_color=color).pack(pady=20)

    def _refresh_current_page(self, error: str | None = None):
        if self._refresh_paused or not self._is_view_active():
            return
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None
        self._header.configure(text=self._page_title())
        self._clear_content()
        if error:
            frame = ctk.CTkFrame(self._content_host, fg_color="transparent")
            frame.grid(sticky="nsew")
            self._section_message(frame, f"Connection error: {error}", "#FF6666")
            return

        if self._view == VIEW_WORLD:
            self._show_stats_loading()
            self._async_fetch(self._fetch_stats_payload, self._render_stats_page)
        elif self._view == VIEW_RECORDS:
            self._show_records_loading()
            self._async_fetch(self._fetch_records_payload, self._render_records_page)
        elif self._view == VIEW_LOGS:
            self._show_logs_loading()
            self._async_fetch(self._fetch_logs_payload, self._render_logs_page)
        else:
            self._show_stats_loading()
            self._async_fetch(self._fetch_stats_payload, self._render_stats_page)

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
            self.after_idle(lambda: self._apply_fetch(gen, payload, err, apply_fn))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_fetch(self, gen: int, payload: Any, err: str | None, apply_fn: Callable[[Any], None]):
        if gen != self._fetch_gen or self._refresh_paused or not self._is_view_active():
            return
        self._clear_content()
        if err:
            frame = ctk.CTkFrame(self._content_host, fg_color="transparent")
            frame.grid(sticky="nsew")
            self._section_message(frame, f"Error: {err}", "#FF6666")
            return
        try:
            apply_fn(payload)
        except Exception as exc:
            frame = ctk.CTkFrame(self._content_host, fg_color="transparent")
            frame.grid(sticky="nsew")
            self._section_message(frame, f"Display error: {exc}", "#FF6666")

    # --- Fetch payloads ---

    def _fetch_stats_payload(self) -> dict:
        highscores = fetch_highscores()
        keys = period_keys_from_selection(
            self._period_mode,
            self._period_year,
            self._period_month,
            self._period_week,
            self._period_day,
        )
        payload = {
            "highscores": highscores,
            "keys": keys,
            "view": self._view,
            "selected_user": self._selected_user,
            "period_mode": self._period_mode,
            "users": self._users,
        }
        if self._view in (VIEW_TEAM, VIEW_USER):
            payload["agg"] = fetch_agg_doc(self._view, self._selected_user)
            doc = status_meeting.find_one({"_id": "Author Goals"}) or {}
            payload["goals_doc"] = {k: v for k, v in doc.items() if k != "_id"}
            first_ts = first_log_timestamp(
                self._selected_user if self._view == VIEW_USER else None
            )
            payload["goal_summary"] = compute_goal_summary(
                self._view,
                self._selected_user,
                self._users,
                payload["goals_doc"],
                first_ts,
            )
            chart_keys = period_keys(datetime.now(timezone.utc).replace(tzinfo=None))
            if self._period_mode == "week":
                chart_keys = keys
            iso_y, iso_w = chart_keys.iso_week_year, chart_keys.iso_week
            payload["week_logs"] = logs_for_week(
                iso_y,
                iso_w,
                self._selected_user if self._view == VIEW_USER else None,
            )
            payload["chart_week_label"] = f"{iso_y}-W{iso_w}"
        return payload

    def _log_filters_dict(self) -> dict:
        selected_users = {
            u for u, var in self._log_user_checks.items() if var.get()
        }
        return {
            "users": selected_users if selected_users else None,
            "date_mode": self._log_date_mode,
            "date_after": self._log_date_after,
            "date_before": self._log_date_before,
            "elapsed_mode": self._log_elapsed_mode,
            "elapsed_min": self._log_elapsed_min,
            "elapsed_max": self._log_elapsed_max,
            "search": self._log_search,
            "sort": self._log_sort,
        }

    def _fetch_logs_payload(self) -> dict:
        logs, total = fetch_logs_page(
            filters=self._log_filters_dict(),
            page=self._logs_page,
            page_size=self._logs_page_size,
        )
        return {
            "logs": logs,
            "total": total,
            "page": self._logs_page,
            "page_size": self._logs_page_size,
        }

    def _fetch_records_payload(self) -> dict:
        all_records = flatten_all_records()
        start = self._records_page * RECORDS_PAGE_SIZE
        end = start + RECORDS_PAGE_SIZE
        return {
            "records": all_records[start:end],
            "total": len(all_records),
            "page": self._records_page,
        }

    # --- Stats page render ---

    def _render_stats_page(self, payload: dict):
        scroll = CtkSmartScrollableFrame(self._content_host)
        scroll.grid(sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        if payload["view"] == VIEW_USER and not payload.get("selected_user"):
            self._section_message(scroll, "Select a user from the sidebar.")
            return

        slice_data = highscore_slice(
            payload.get("highscores"),
            payload["view"],
            payload.get("selected_user"),
        )
        if not slice_data:
            self._section_message(scroll, "No highscore data available.")
            return

        row = 0
        is_global = payload["view"] == VIEW_WORLD
        row = self._render_peaks_block(scroll, row, slice_data, is_global=is_global)

        if payload["view"] in (VIEW_TEAM, VIEW_USER):
            row = self._render_period_controls(scroll, row)
            row = self._render_period_stats(scroll, row, payload)
            row = self._render_streaks_live(scroll, row, payload)
            row = self._render_charts(scroll, row, payload)
            row = self._render_goals_block(scroll, row, payload)

    def _render_peaks_block(self, parent, row: int, records: dict, *, is_global: bool) -> int:
        for time_type in _PERIOD_TYPES:
            if time_type not in records:
                continue
            frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
            frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
            frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(frame, text=time_type, font=("Arial", 16, "bold"), anchor="w").grid(
                row=0, column=0, columnspan=2, sticky="w", padx=10, pady=4
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

        consecutive = records.get("consecutive") or {}
        for kind, label in (("days", "Consecutive Days"), ("weeks", "Consecutive Weeks")):
            streak = consecutive.get(kind) or {}
            frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
            frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
            frame.grid_columnconfigure(1, weight=1)
            self._peak_metric_row(
                frame, 0, label, str(streak.get("value", 0)),
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

    def _render_period_controls(self, parent, row: int) -> int:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=8, pady=(12, 4))
        frame.grid_columnconfigure(8, weight=1)

        ctk.CTkLabel(frame, text="Period", font=("Arial", 16, "bold")).grid(
            row=0, column=0, padx=(0, 8), pady=4, sticky="w"
        )
        label_to_mode = {v: k for k, v in _PERIOD_MODE_LABELS.items()}
        mode_menu = ctk.CTkOptionMenu(
            frame,
            values=list(_PERIOD_MODE_LABELS.values()),
            command=lambda label: self._on_period_mode_changed(label_to_mode[label]),
            width=110,
            **_MENU,
        )
        mode_menu.set(_PERIOD_MODE_LABELS.get(self._period_mode, "All time"))
        mode_menu.grid(row=0, column=1, padx=4, pady=4)

        if self._period_mode != "all":
            ctk.CTkLabel(frame, text="Year", font=("Arial", 12)).grid(row=0, column=2, padx=(12, 4))
            self._period_year_entry = ctk.CTkEntry(frame, width=60)
            self._period_year_entry.insert(0, self._period_year)
            self._period_year_entry.grid(row=0, column=3, padx=2)

        col = 4
        if self._period_mode in ("month", "day"):
            ctk.CTkLabel(frame, text="Month", font=("Arial", 12)).grid(row=0, column=col, padx=(8, 4))
            col += 1
            self._period_month_entry = ctk.CTkEntry(frame, width=40)
            self._period_month_entry.insert(0, self._period_month)
            self._period_month_entry.grid(row=0, column=col, padx=2)
            col += 1

        if self._period_mode == "week":
            ctk.CTkLabel(frame, text="Week", font=("Arial", 12)).grid(row=0, column=col, padx=(8, 4))
            col += 1
            self._period_week_entry = ctk.CTkEntry(frame, width=40)
            self._period_week_entry.insert(0, self._period_week)
            self._period_week_entry.grid(row=0, column=col, padx=2)
            col += 1

        if self._period_mode == "day":
            ctk.CTkLabel(frame, text="Day", font=("Arial", 12)).grid(row=0, column=col, padx=(8, 4))
            col += 1
            self._period_day_entry = ctk.CTkEntry(frame, width=40)
            self._period_day_entry.insert(0, self._period_day)
            self._period_day_entry.grid(row=0, column=col, padx=2)
            col += 1

        if self._period_mode != "all":
            ctk.CTkButton(
                frame, text="Apply", width=70, command=self._apply_period_selection, **_BTN
            ).grid(row=0, column=col, padx=(12, 4), pady=4)

        return row + 1

    def _on_period_mode_changed(self, mode: str):
        self._period_mode = mode
        self._refresh_current_page()

    def _apply_period_selection(self):
        try:
            self._period_year = self._period_year_entry.get().strip()
            if self._period_mode in ("month", "day"):
                self._period_month = f"{int(self._period_month_entry.get().strip()):02d}"
            if self._period_mode == "week":
                self._period_week = str(int(self._period_week_entry.get().strip()))
            if self._period_mode == "day":
                self._period_day = f"{int(self._period_day_entry.get().strip()):02d}"
        except (ValueError, AttributeError):
            return
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

    def _render_streaks_live(self, parent, row: int, payload: dict) -> int:
        agg = payload.get("agg") or {}
        day_c, week_c = display_streak_current(
            agg, payload["view"], payload.get("selected_user")
        )
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
        week_label = payload.get("chart_week_label", f"{keys.iso_week_year}-W{keys.iso_week}")
        ctk.CTkLabel(
            parent,
            text=f"Week activity — {week_label}",
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
                color = MeetingFrame._goal_color(hours, 1) if hours >= 1 else "#3A3A3A"
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
        if self._view in (VIEW_TEAM, VIEW_USER):
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
        old_str = MeetingFrame._format_record_value(metric, old_value)
        new_str = MeetingFrame._format_record_value(metric, new_value)

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
        outer.grid_rowconfigure(2, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        filt_outer = CtkSmartScrollableFrame(outer, height=220, fg_color="#1A1A1A")
        filt_outer.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        row_f = 0
        ctk.CTkLabel(filt_outer, text="Users (none selected = all)", font=("Arial", 12, "bold")).grid(
            row=row_f, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 2)
        )
        row_f += 1
        user_row = ctk.CTkFrame(filt_outer, fg_color="transparent")
        user_row.grid(row=row_f, column=0, columnspan=4, sticky="ew", padx=8)
        for i, username in enumerate(self._users):
            var = self._log_user_checks.setdefault(username, ctk.BooleanVar(value=False))
            ctk.CTkCheckBox(
                user_row, text=username, variable=var, font=("Arial", 12),
            ).grid(row=i // 3, column=i % 3, sticky="w", padx=4, pady=2)
        row_f += 1

        ctk.CTkLabel(filt_outer, text="Search (name or description)", font=("Arial", 12, "bold")).grid(
            row=row_f, column=0, sticky="w", padx=8, pady=(8, 2)
        )
        row_f += 1
        search_entry = ctk.CTkEntry(filt_outer, placeholder_text="Search text…")
        search_entry.insert(0, self._log_search)
        search_entry.grid(row=row_f, column=0, columnspan=3, sticky="ew", padx=8, pady=2)
        row_f += 1

        ctk.CTkLabel(filt_outer, text="Date (Stockholm, YYYY-MM-DD or YYYY-MM-DD HH:MM)", font=("Arial", 12, "bold")).grid(
            row=row_f, column=0, columnspan=4, sticky="w", padx=8, pady=(8, 2)
        )
        row_f += 1
        date_mode_menu = ctk.CTkOptionMenu(
            filt_outer,
            values=list(_DATE_FILTER_LABELS.values()),
            command=lambda l: setattr(self, "_log_date_mode", {v: k for k, v in _DATE_FILTER_LABELS.items()}[l]),
            width=120,
            **_MENU,
        )
        date_mode_menu.set(_DATE_FILTER_LABELS.get(self._log_date_mode, "Any date"))
        date_mode_menu.grid(row=row_f, column=0, padx=8, pady=2, sticky="w")
        date_after_entry = ctk.CTkEntry(filt_outer, placeholder_text="From / after", width=160)
        date_after_entry.insert(0, self._log_date_after)
        date_after_entry.grid(row=row_f, column=1, padx=4, pady=2)
        date_before_entry = ctk.CTkEntry(filt_outer, placeholder_text="To / before", width=160)
        date_before_entry.insert(0, self._log_date_before)
        date_before_entry.grid(row=row_f, column=2, padx=4, pady=2)
        row_f += 1

        ctk.CTkLabel(filt_outer, text="Duration (seconds or MM:SS or HH:MM:SS)", font=("Arial", 12, "bold")).grid(
            row=row_f, column=0, columnspan=4, sticky="w", padx=8, pady=(8, 2)
        )
        row_f += 1
        elapsed_mode_menu = ctk.CTkOptionMenu(
            filt_outer,
            values=list(_ELAPSED_FILTER_LABELS.values()),
            command=lambda l: setattr(self, "_log_elapsed_mode", {v: k for k, v in _ELAPSED_FILTER_LABELS.items()}[l]),
            width=120,
            **_MENU,
        )
        elapsed_mode_menu.set(_ELAPSED_FILTER_LABELS.get(self._log_elapsed_mode, "Any duration"))
        elapsed_mode_menu.grid(row=row_f, column=0, padx=8, pady=2, sticky="w")
        elapsed_min_entry = ctk.CTkEntry(filt_outer, placeholder_text="Min", width=100)
        elapsed_min_entry.insert(0, self._log_elapsed_min)
        elapsed_min_entry.grid(row=row_f, column=1, padx=4, pady=2)
        elapsed_max_entry = ctk.CTkEntry(filt_outer, placeholder_text="Max", width=100)
        elapsed_max_entry.insert(0, self._log_elapsed_max)
        elapsed_max_entry.grid(row=row_f, column=2, padx=4, pady=2)
        row_f += 1

        ctk.CTkLabel(filt_outer, text="Sort", font=("Arial", 12, "bold")).grid(
            row=row_f, column=0, sticky="w", padx=8, pady=(8, 2)
        )
        row_f += 1
        sort_menu = ctk.CTkOptionMenu(
            filt_outer,
            values=list(_SORT_OPTIONS.values()),
            command=lambda l: setattr(self, "_log_sort", {v: k for k, v in _SORT_OPTIONS.items()}[l]),
            width=160,
            **_MENU,
        )
        sort_menu.set(_SORT_OPTIONS.get(self._log_sort, "Newest first"))
        sort_menu.grid(row=row_f, column=0, padx=8, pady=2, sticky="w")

        def apply_filters():
            self._log_search = search_entry.get().strip()
            self._log_date_after = date_after_entry.get().strip()
            self._log_date_before = date_before_entry.get().strip()
            self._log_elapsed_min = elapsed_min_entry.get().strip()
            self._log_elapsed_max = elapsed_max_entry.get().strip()
            self._logs_page = 0
            self._refresh_current_page()

        ctk.CTkButton(
            filt_outer, text="Apply filters", command=apply_filters, width=120, **_BTN
        ).grid(row=row_f, column=1, padx=8, pady=8, sticky="w")

        scroll = CtkSmartScrollableFrame(outer)
        scroll.grid(row=2, column=0, sticky="nsew")

        logs = payload.get("logs") or []
        if not logs:
            self._section_message(scroll, "No logs match the current filters.")
        else:
            for log in logs:
                ts = format_highscore_date(log["timestamp"])
                name = log.get("name") or "—"
                desc = log.get("description") or ""
                desc_part = f"  |  {desc}" if desc else ""
                line = (
                    f"{ts}  |  {log['user']}  |  {format_time(log['elapsed_time'])}"
                    f"  |  {name}{desc_part}"
                )
                ctk.CTkLabel(scroll, text=line, font=("Arial", 12), anchor="w", wraplength=900).pack(
                    anchor="w", fill="x", padx=8, pady=2
                )

        nav = ctk.CTkFrame(outer, fg_color="transparent")
        nav.grid(row=3, column=0, sticky="ew", pady=8)
        total = payload.get("total", 0)
        page = payload.get("page", 0)
        page_size = payload.get("page_size", self._logs_page_size)
        if page_size is None:
            nav_text = f"Showing all {total} logs"
        else:
            max_page = max(0, (total - 1) // page_size) if total else 0
            nav_text = f"Page {page + 1} / {max_page + 1} ({total} logs)"
        ctk.CTkLabel(nav, text=nav_text, font=("Arial", 12)).pack(side="left", padx=8)
        if page_size is not None:
            if page > 0:
                ctk.CTkButton(nav, text="← Prev", command=self._logs_prev, width=80, **_BTN).pack(side="left", padx=4)
            if (page + 1) * page_size < total:
                ctk.CTkButton(nav, text="Next →", command=self._logs_next, width=80, **_BTN).pack(side="left", padx=4)
        ctk.CTkLabel(nav, text="Per page:", font=("Arial", 12), text_color=_MUTED).pack(side="right", padx=(8, 4))
        page_size_menu = ctk.CTkOptionMenu(
            nav,
            values=list(LOGS_PAGE_SIZE_OPTIONS),
            command=self._set_logs_page_size,
            width=72,
            **_MENU,
        )
        page_size_menu.set("all" if page_size is None else str(page_size))
        page_size_menu.pack(side="right", padx=8)

    def _logs_prev(self):
        self._logs_page = max(0, self._logs_page - 1)
        self._refresh_current_page()

    def _logs_next(self):
        self._logs_page += 1
        self._refresh_current_page()

    def _set_logs_page_size(self, value: str):
        page_size = None if value == "all" else int(value)
        if page_size == self._logs_page_size:
            return
        self._logs_page_size = page_size
        self._logs_page = 0
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
        if self._refresh_paused or not self._is_view_active():
            return
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(WATCHER_DEBOUNCE_MS, self._debounced_refresh)

    def _debounced_refresh(self):
        self._refresh_job = None
        self._refresh_current_page()

    def _agg_watcher(self):
        pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}]

        def on_change():
            self.after_idle(self._schedule_refresh)

        self._run_watcher(aggregations, pipeline, on_change)

    def _status_watcher(self):
        pipeline = [
            {
                "$match": {
                    "operationType": {"$in": ["insert", "update", "replace"]},
                    "documentKey._id": {"$in": ["Author Goals", "Records"]},
                }
            }
        ]

        def on_change():
            self.after_idle(self._schedule_refresh)

        self._run_watcher(status_meeting, pipeline, on_change)

    def _logs_watcher(self):
        pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}]

        def on_change():
            self.after_idle(self._schedule_refresh)

        self._run_watcher(collection, pipeline, on_change)


