import customtkinter as ctk
from pymongo import MongoClient, UpdateOne
import datetime
import os
import json
import threading
import tkinter
import math
import time
from concurrent.futures import ThreadPoolExecutor
from CtkSmartScrollableFrame import CtkSmartScrollableFrame
from CTkPieChart import CTkPieChart
from CTkFlexToolTip import *
from utils import flash_error
import random
from openai import OpenAI
from collections import defaultdict
from pymongo import ReturnDocument

from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError, NetworkTimeout, AutoReconnect
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from period_model import APP_TIMEZONE, coerce_highscore_datetime, format_highscore_date, to_local, utc_naive_after_calendar_days

# TODO: Set up color scheme or theme
# Improvements: Sync, No-activity weeks, Dry Dropdown, server-side slide_5

# MongoDB connection
client = MongoClient("mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/?retryWrites=true&w=majority&appName=ELG-timetable")
db = client['ELG-Database']
main_collection = db['Timetable']  # Only used once
status_meeting_collection = db['Status Meeting']  # Collection for meeting status
aggregations_collection = db['Timetable Aggregations']
WEEK_PIPELINE = [{
    "$set": {"new_week": {"$dateTrunc": {
        "date": {"$dateSubtract": {"startDate": "$$NOW", "unit": "day", "amount": 3, "timezone": APP_TIMEZONE}},
        "unit": "week",
        "startOfWeek": "monday",
        "timezone": APP_TIMEZONE}}}},{
    "$set": {
        "slide": {"$cond": {
            "if": {"$ne": ["$week", "$new_week"]},
            "then": 0,
            "else": "$slide"}},
        "week": "$new_week"}},{
    "$unset": "new_week"}
]

STATUS_MEETING_WATCH_PIPELINE = [
    {"$match": {"operationType": {"$in": ["update", "replace"]}}},
    {"$match": {"$expr": {"$or": [
        {"$ne": ["$documentKey._id", "Users"]},
        {"$ne": ["$operationType", "update"]},
        {"$gt": [{"$size": {"$ifNull": ["$updateDescription.removedFields", []]}}, 0]},
        {"$gt": [{"$size": {"$filter": {
            "input": {"$objectToArray": {"$ifNull": ["$updateDescription.updatedFields", {}]}},
            "as": "field",
            "cond": {"$not": {"$regexMatch": {
                "input": "$$field.k",
                "regex": "^data\\.[^.]+\\.last_seen$",}}},}}},
            0,]},]}}},]

# Initialize the customtkinter theme with dark mode
ctk.set_appearance_mode("Dark")

def _get_user_name():
    """Get user name from configfile"""
    try:
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
        with open(config_path, "r") as f:
            config = json.load(f)
            return config.get("user", "Unknown")
    except FileNotFoundError:
        print("Could not fetch username from config.")
        return "Unknown"

def format_time(seconds):
    """Converts seconds to MM:SS or HH:MM:SS."""
    if not seconds:
        return "00:00"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    if hours >= 24:
        return f"{hours} hours"
    elif hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes:02d}:{seconds:02d}"

class MeetingApp(ctk.CTk):
    ### INIT ###
    def __init__(self):
        super().__init__()

        self.fullscreen = False
        self._init_complete = False
        self._presence_update_event = threading.Event()
        self._pending_changes = {}
        self._sync_lock = threading.Lock()
        self._watchers_ready_count = 0
        self._watchers_ready_event = threading.Event()
        self.start_watchers()
        self._watchers_ready_event.wait()
        self._update_event = threading.Event()
        self._lock = threading.Lock()
        self.update_buffer = {"slide": None, "goals": defaultdict(dict)}
        threading.Thread(target=self.update_db, name="DB_updater", daemon=True).start()

        self.current_slide, self.current_week_start, self.next_week_start, self.local_target_timestamp = self.fetch_state()
        self.current_year, self.current_week, self.next_year, self.next_week = self._calculate_week_info()
        threading.Thread(target=self.week_check, name="WeekSync", daemon=True).start()
        self.user_name = _get_user_name()

        with ThreadPoolExecutor() as executor:
            future_logs = executor.submit(self._fetch_logs)
            future_points = executor.submit(self._fetch_discussion_points)
            future_curr_goals = executor.submit(self.s4_fetch_goals, self.current_year, self.current_week)
            future_next_goals = executor.submit(self.s4_fetch_goals, self.next_year, self.next_week)
            future_highscores = executor.submit(lambda: aggregations_collection.find_one({"_id": "Highscores"}))
            future_users = executor.submit(self.fetch_online_user_info)

            self.logs = future_logs.result()
            self.discussion_points = future_points.result()
            self._current_week_goals = future_curr_goals.result()
            self._next_week_goals = future_next_goals.result()
            self.highscores_data = future_highscores.result()
            self.online_users_info = future_users.result()
            
        threading.Thread(target=self.update_presence, name="update_presence", daemon=True).start()

        self.slide_map = self._create_slide_map()

        self.online_users = self.build_online_users()
        self.input_mode_users = self.build_input_mode_users()

        self._setup_slides_scaffold()
        self.initialize_slides()
        self.show_slide()

        self._complete_initialization()

    @staticmethod
    def fetch_state():
        """Fetches the week anchor from the db. Shows Mon-Sun until next Wed, rolls Thu."""
        projection = {"_id": 0, "slide": 1, "week": 1, "server_time": "$$NOW"}
        doc = status_meeting_collection.find_one_and_update({"_id": "State"}, WEEK_PIPELINE, projection=projection, return_document=ReturnDocument.AFTER)
        local_now = time.time()
        slide = doc["slide"]
        current_week_start = doc["week"]
        next_week_start = utc_naive_after_calendar_days(current_week_start, 7)
        local_target_timestamp = local_now + (
            utc_naive_after_calendar_days(next_week_start, 3) - doc["server_time"]
        ).total_seconds()
        return slide, current_week_start, next_week_start, local_target_timestamp

    def _calculate_week_info(self):
        """Calculates current and next week's year and week numbers."""
        current_year, current_week, _ = to_local(self.current_week_start).isocalendar()
        next_year, next_week, _ = to_local(self.next_week_start).isocalendar()

        return str(current_year), str(current_week), str(next_year), str(next_week)

    def _fetch_discussion_points(self):
        """Returns discussion points for the current year and week."""
        return status_meeting_collection.find_one({"_id": "Discussion Points"},
            projection={f"{self.current_year}.{self.current_week}": 1}
        ).get(self.current_year, {}).get(self.current_week, [])

    def _create_slide_map(self):
        slide_map = [0]
        if self.logs or self._current_week_goals:
            slide_map.append(1)
        if self.logs:
            slide_map.append(2)
        if self.discussion_points:
            slide_map.append(3)
        slide_map.append(4)
        slide_map.append(5)
        return slide_map

    def _fetch_logs(self):
        """Returns a list of all logs for the current week."""
        return list(main_collection.find({
            "timestamp": {
                "$gte": self.current_week_start,
                "$lt": self.next_week_start}}))

    def _setup_slides_scaffold(self):
        """Sets up the foundational scaffold and overlay for all slides, including navigation and overlays."""
        # Configure window
        self.title("Weekly Status Meeting")
        self.geometry("1200x800")

        # Bind keys and mouse
        self.bind("<F11>", self.toggle_fullscreen)
        self.bind("<Escape>", self.exit_fullscreen)
        self.bind("<Right>", self.handle_arrow)
        self.bind("<Left>", self.handle_arrow)
        self.bind("<Return>", self.handle_return)

        # main container for all slides
        self.main_container = ctk.CTkFrame(self)
        self.main_container.place(relx=0, rely=0, relwidth=1, relheight=1)

        # slide frames
        self.slide_frames = []
        for _ in range(max(self.slide_map) + 1):
            frame = ctk.CTkFrame(self.main_container)
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.slide_frames.append(frame)

        # slide dots indicator frame (always on top)
        self.indicator_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.indicator_frame.place(relx=0.5, rely=0.9644, anchor="s")

        self.slide_dots = []

        # Create frame for user count label
        self.users_frame = ctk.CTkFrame(
            self,
            width=105,
            corner_radius=0,
            fg_color='transparent',
            bg_color='transparent'
        )
        self.users_frame.place(relx=0.98, rely=0.9644, anchor="se")

        # user count label
        self.users_count_label = ctk.CTkLabel(
            self.users_frame,
            text=f"Participants ({len(self.online_users)})",
            font=("Arial", 12),
            text_color="white",
            width=105
        )
        self.users_count_label.pack(side="bottom")

        # Create frame for usernames labels
        self.users_list_frame = ctk.CTkFrame(
            self,
            width=105,
            corner_radius=0,
            fg_color='transparent',
            bg_color='transparent'
        )
        self.update_users_list()

        # Bind hover event
        self.users_count_label.bind("<Enter>", self.show_users_list)

    def initialize_slides(self):
        """Initialize all slides in slide_map."""
        for slide_id in self.slide_map:
            getattr(self, f"slide_{slide_id}")()
            self.create_slide_dot()

    def start_watchers(self):
        for func in (self.status_meeting_watcher, self.timetable_watcher):
            threading.Thread(target=func, name=func.__name__, daemon=True).start()

    def _run_watcher(self, collection, pipeline, callback, **watch_kwargs):
        resume_token = None
        ready_signalled = False
        while True:
            # noinspection PyBroadException
            try:
                with collection.watch(pipeline, resume_after=resume_token, **watch_kwargs) as stream:
                    if not ready_signalled:
                        with self._sync_lock:
                            self._watchers_ready_count += 1
                            if self._watchers_ready_count >= 2:
                                self._watchers_ready_event.set()
                        ready_signalled = True
                    for change in stream:
                        resume_token = stream.resume_token
                        callback(change)
            except Exception:
                time.sleep(1)

    def status_meeting_watcher(self):
        def handle_status_change(change):
            doc_id = change["documentKey"]["_id"]
            if doc_id == "State":
                self._store_or_process_change('state', change)
            elif doc_id == "Goals":
                self._store_or_process_change('goals', change)
            elif doc_id == "Users":
                self._store_or_process_change('users', change)
            elif doc_id == "Discussion Points":
                self._store_or_process_change('points', change)
            elif doc_id == "End Strings":
                self._store_or_process_change('strings', change)

        self._run_watcher(status_meeting_collection, STATUS_MEETING_WATCH_PIPELINE, handle_status_change)

    def timetable_watcher(self):
        pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}]

        def handle_timetable_change(change):
            self._store_or_process_change("logs", change)

        self._run_watcher(main_collection, pipeline, handle_timetable_change, full_document='updateLookup')

    def week_check(self):
        while True:
            try:
                if time.time() >= self.local_target_timestamp:
                    status_meeting_collection.update_one({"_id": "State"}, WEEK_PIPELINE)
            except Exception:
                pass
            time.sleep(1)

    def _complete_initialization(self):
        with self._sync_lock:
            users_batch = self._pending_changes.pop("users", [])
            other_pending = {name: change for name, change in self._pending_changes.items() if name != "users" and change}

            self._handle_users_change(sorted(users_batch, key=lambda c: c["clusterTime"].time))

            for name, change in other_pending.items():
                getattr(self, f"_handle_{name}_change")(change)

            self._init_complete = True

    ### ALL SLIDES ###
    def show_slide(self, old_slide=None):
        """Show the index self.current_slide in self.slide_map."""
        self.update_slide_indicator_dots()
        slide_number = self.slide_map[self.current_slide]
        self.slide_frames[slide_number].lift()
        if old_slide == self.slide_map.index(4):
            self._presence_update_event.set()

    def update_slide_indicator_dots(self):
        active_style = {"text": "●", "text_color": "white"}
        inactive_style = {"text": "○", "text_color": "gray"}
        for i, dot in enumerate(self.slide_dots):
            config = active_style if i == self.current_slide else inactive_style
            dot.configure(**config)

    def update_users_list(self):
        """Update the labels in users_list_frame to match self.online_users."""
        # Remove all existing labels (but not the frame itself)
        for widget in self.users_list_frame.winfo_children():
            widget.destroy()
        font = ctk.CTkFont(family="Arial", size=12)
        max_width = 105 - 10
        for user in sorted(self.online_users):
            display_text = self.truncate_text_to_pixels(user, font, max_width)
            user_label = ctk.CTkLabel(
                self.users_list_frame,
                text=display_text,
                font=("Arial", 12),
                text_color="white",
                width=105
            )
            user_label.pack(side="top")
            user_label.bind("<Leave>", self.hide_users_list)

    def show_users_list(self, _event):
        """Show the list of users when hovering."""
        self.users_list_frame.place(relx=0.98, rely=0.9644, anchor="se")
        self.users_list_frame.lift()

    def hide_users_list(self, event):
        """Hide the users list (just place_forget the frame)."""
        widget_under_mouse = self.winfo_containing(event.x_root, event.y_root)
        if widget_under_mouse and widget_under_mouse.master == event.widget.master:
            return
        self.users_list_frame.place_forget()

    def toggle_fullscreen(self, _event):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self._pre_fullscreen_geometry = self.geometry()  # Store current window position before going fullscreen

            # Get the current window position to determine which display it's on
            window_x = self.winfo_x()
            window_y = self.winfo_y()

            # Set fullscreen - this should automatically use the display where the window is located
            self.attributes("-fullscreen", True)

            # This ensures it doesn't jump to the primary display
            self.update_idletasks()  # Ensure the fullscreen change is processed
            self.geometry(f"+{window_x}+{window_y}")
        else:
            self.attributes("-fullscreen", False)
            if hasattr(self, '_pre_fullscreen_geometry'):
                self.geometry(self._pre_fullscreen_geometry)

    def exit_fullscreen(self, _event):
        if self.fullscreen:
            self.fullscreen = False
            self.attributes("-fullscreen", False)
            if hasattr(self, '_pre_fullscreen_geometry'):
                self.geometry(self._pre_fullscreen_geometry)

    def update_db(self):
        while True:
            try:
                self._update_event.wait()
                self._update_event.clear()
                with self._lock:
                    current_buffer = self.update_buffer
                    self.update_buffer = {"slide": None, "goals": defaultdict(dict)}
                operations = []

                if current_buffer["slide"] is not None:
                    operations.append(UpdateOne(
                        {"_id": "State"},
                        {"$set": {"slide": current_buffer["slide"]}}))

                if goals_buffer := current_buffer["goals"]:
                    set_ops = {}
                    unset_ops = {}
                    prefix = f"{self.next_year}.{self.next_week}"

                    for user, changes in goals_buffer.items():
                        user_path = f"{prefix}.{user}"

                        if changes == {}:
                            unset_ops[user_path] = ""
                            continue

                        for field, val in changes.items():
                            full_path = f"{user_path}.{field}"
                            if val > 0:
                                set_ops[full_path] = val
                            elif val == 0:
                                unset_ops[full_path] = ""

                    update_doc = {}
                    if set_ops:
                        update_doc["$set"] = set_ops
                    if unset_ops:
                        update_doc["$unset"] = unset_ops

                    operations.append(UpdateOne({"_id": "Goals"}, update_doc, upsert=True))
                status_meeting_collection.bulk_write(operations, ordered=False)

            except Exception as e:
                print(f"Error in update worker: {e}")
                time.sleep(1)

    def handle_arrow(self, event):
        """Handle left and right arrow key presses."""
        if isinstance(self.focus_get(), ctk.CTkEntry):
            return

        target = max(0, min(len(self.slide_map) - 1, self.current_slide + (1 if event.keysym == "Right" else -1)))
        if target == self.current_slide:
            return

        old_slide, self.current_slide = self.current_slide, target
        self.show_slide(old_slide)
        with self._lock:
            self.update_buffer["slide"] = self.current_slide # type: ignore
        self._update_event.set()

    def handle_return(self, _event):
        """Handle Return key press"""
        if self.current_slide == self.slide_map.index(4):
            if self.s4_in_input:
                self.s4_update_hourly_goal(show_summary=True)
            else:
                self.s4_show_input()

    ### HELPERS ###
    def _presence_update_pipeline(self, sel_user):
        user_path = f"data.{self.user_name}"
        return [{
            "$set": {
                f"{user_path}.last_seen": "$$NOW",
                f"{user_path}.selected_user": sel_user,}},
            {"$set": {"data": {"$arrayToObject": {"$filter": {
                "input": {"$objectToArray": {"$ifNull": ["$data", {}]}},
                "as": "entry",
                "cond": {"$gte": [
                    "$$entry.v.last_seen",
                    {"$subtract": ["$$NOW", 2000]},]},}}}}},]

    def fetch_online_user_info(self):
        """Set presence, prune offline users, return the Users doc."""
        doc = status_meeting_collection.find_one_and_update({"_id": "Users"},
            self._presence_update_pipeline(None),
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        return {user: info.get("selected_user") for user, info in (doc.get("data")).items()}

    def build_online_users(self):
        """Returns the set of online usernames."""
        return set(self.online_users_info)

    def build_input_mode_users(self) -> set[str]:
        """Returns users that another participant is entering goals for."""
        return {sel for user, sel in self.online_users_info.items() if user != self.user_name and sel}

    def create_slide_dot(self):
        dot = ctk.CTkLabel(
            self.indicator_frame,
            text="○",
            font=("Arial", 20),
            text_color="gray"
        )
        dot.pack(side="left", padx=5)
        self.slide_dots.append(dot)

    @staticmethod
    def truncate_text_to_pixels(text, font, max_width):
        """Truncate text to fit within max_width pixels, adding ellipsis if needed."""
        text_width = font.measure(text)
        display_text = text
        if text_width > max_width:
            while text_width > max_width and len(display_text) > 3:
                display_text = display_text[:-1]
                text_width = font.measure(display_text + "...")
            display_text = display_text + "..."
        return display_text

    @staticmethod
    def _goal_color(actual, goal) -> str:
        """Returns green if actual meets goal, red otherwise."""
        return "#00AD00" if actual >= goal else "#FF0000"

    def _update_if_changed(self, attr_name, new_value):
        old_value = getattr(self, attr_name)
        if old_value == new_value:
            return False
        setattr(self, attr_name, new_value)
        return True

    ### SYNC ###
    def _store_or_process_change(self, name, change):
        with self._sync_lock:
            if not self._init_complete:
                if name == "users":
                    self._pending_changes.setdefault("users", []).append(change)
                else:
                    self._pending_changes[name] = change
                return
        self.after(0, getattr(self, f"_handle_{name}_change"), change)

    def update_presence(self):
        while True:
            self._presence_update_event.wait(timeout=1.0)
            self._presence_update_event.clear()
            slide_map = getattr(self, "slide_map", None)
            if slide_map and self.current_slide == slide_map.index(4) and getattr(self, "s4_in_input", False):
                sel_user = self.s4_selected_user_var.get()  # type: ignore[attr-defined]
            else:
                sel_user = None
            try:
                status_meeting_collection.update_one(
                    {"_id": "Users"},
                    self._presence_update_pipeline(sel_user),
                    upsert=True,
                )
            except Exception as e:
                print(f"Presence update failed: {e}")

    def _handle_users_change(self, changes):
        if not isinstance(changes, list):
            changes = [changes]
        for change in changes:
            desc = change.get("updateDescription", {})
            for path in desc.get("removedFields", []):
                self.online_users_info.pop(path.split(".", 1)[1])
            for path, value in desc.get("updatedFields", {}).items():
                _, user, field = (path.split(".", 2) + [None])[:3]
                if field: # New sel_user
                    selected_user = value
                else:  # New user
                    selected_user = value.get("selected_user")
                self.online_users_info[user] = selected_user

        users_changed = self._update_if_changed("online_users", self.build_online_users())
        input_mode_changed = self._update_if_changed("input_mode_users", self.build_input_mode_users())
        if users_changed:
            self.users_count_label.configure(text=f"Participants ({len(self.online_users)})")
            self.update_users_list()
            self.s4_update_selectable_users()
        if users_changed or input_mode_changed:
            self.s4_update_display_ui()

    def _handle_state_change(self, change):
        updated_fields = change["updateDescription"]["updatedFields"]
        if "slide" in updated_fields:
            old_slide = self.current_slide
            if self._update_if_changed('current_slide', updated_fields["slide"]):
                self.show_slide(old_slide)
        if "week" in updated_fields:
            if self._update_if_changed('current_week_start', updated_fields["week"]):
                local_now = time.time()
                server_time = change["clusterTime"].as_datetime().replace(tzinfo=None)
                week_anchor = updated_fields["week"]
                next_week_start = utc_naive_after_calendar_days(week_anchor, 7)
                self.local_target_timestamp = local_now + (
                    utc_naive_after_calendar_days(next_week_start, 3) - server_time
                ).total_seconds()
                self.current_year, self.current_week, self.next_year, self.next_week = self._calculate_week_info()
                # TODO: logic if new week

    def _handle_goals_change(self, _change):
        if not self._update_if_changed('_cached_next_week_goals', self.s4_fetch_goals(self.next_year, self.next_week)):
            return
        self.s4_update_display_ui()
        self.s4_update_input_ui(False)

    def _handle_logs_change(self, change):
        full_doc = change['fullDocument']
        if self.current_week_start <= full_doc['timestamp'] < self.next_week_start:
            self.ensure_slide(1, 2)
            self.logs = self._fetch_logs()
            self.hours_graph_data, self.days_charts_data, self.team_hours_bar_data = self.s1_build_all_charts_data()
            self.s2_set_logs_by_user()
            self.s1_update_hours_graph()
            self.s1_update_days_charts()
            self.s1_update_team_hours_bar()
            self.s2_update_selectable_users()
            if change.get("operationType") == "insert" and self.s2_selected_user_var.get() == full_doc.get("user"):
                self.s2_create_log_widget(full_doc)
            else:
                self.s2_create_user_log_widgets()

    def _handle_points_change(self, _change):
        if not self._update_if_changed("discussion_points", self._fetch_discussion_points()):
            return
        self.ensure_slide(3)
        self.s3_update_discussion_points()

    def _handle_strings_change(self, change):
        updated = change.get("updateDescription", {}).get("updatedFields", {})
        data_path = f"data.{self.current_year}.{self.current_week}"

        for key, value in updated.items():
            if (key == data_path) or (key == "lock_timestamp" and value is None):
                self._s5_get_data_event.set()
                break

    def ensure_slide(self, *slide_ids: int):
        if all(sid in self.slide_map for sid in slide_ids):
            return

        old_slide = self.slide_map[self.current_slide]

        for slide_id in slide_ids:
            if slide_id in self.slide_map:
                continue
            self.slide_map.append(slide_id)
            self.create_slide_dot()
            getattr(self, f"slide_{slide_id}")()

        self.slide_map.sort()
        self.current_slide = self.slide_map.index(old_slide)
        self.update_slide_indicator_dots()

    ### SLIDE 0 ###
    def slide_0(self):
        """Welcome"""
        title = ctk.CTkLabel(
            self.slide_frames[0],
            text=f"Weekly Status Meeting\nWeek {self.current_week}",
            font=("Arial", 80, "bold"),
            text_color="white"
        )
        title.place(relx=0.5, rely=0.5, anchor="center")

    ### SLIDE 1 ###
    def slide_1(self):
        """Statistics"""
        title = ctk.CTkLabel(
            self.slide_frames[1],
            text="Current Week's Goals and Achievements",
            font=("Arial", 60, "bold"),
            text_color="white"
        )
        title.place(relx=0.028125, rely=0.05, anchor="nw")
        self.current_week_frame = ctk.CTkFrame(self.slide_frames[1])
        self.current_week_frame.place(relx=0.5, rely=0.5, relwidth=0.8, relheight=0.7, anchor="center")
        self.current_week_frame.grid_rowconfigure((0, 1, 2), weight=1, uniform="row_group")
        self.current_week_frame.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=1, uniform="col_group")
        self.current_week_frame.grid_columnconfigure(3, weight=1, minsize=140)  # Team Hours Bar
        self.hours_graph_data, self.days_charts_data, self.team_hours_bar_data = self.s1_build_all_charts_data()
        # Hours Graph Frame
        self.hours_graph_frame = ctk.CTkFrame(self.current_week_frame, fg_color="#181C20", corner_radius=0)
        self.hours_graph_frame.grid(row=0, column=0, rowspan=2, columnspan=3, sticky="nsew")
        self.hours_graph_frame.grid_rowconfigure(0, weight=1)
        self.hours_graph_frame.grid_columnconfigure(0, weight=0)  # static_area
        self.hours_graph_frame.grid_columnconfigure(1, weight=1)  # scrollable_area
        # Static Area (Y-axis)
        self.static_area = ctk.CTkFrame(self.hours_graph_frame, fg_color="#181C20", corner_radius=0)
        self.static_area.grid(row=0, column=0, sticky="nsew")
        # Scrollable area
        self.scrollable_area = CtkSmartScrollableFrame(self.hours_graph_frame, fg_color="transparent", corner_radius=0,
                                                       orientation="horizontal")
        self.scrollable_area.grid(row=0, column=1, sticky="nsew", padx=(0, 15), pady=(15, 0))
        self.scrollable_area.grid_rowconfigure(0, weight=1)
        self.scrollable_area.grid_rowconfigure(1, weight=0)

        # Bind to scrollbar grid events to detect scrollbar state changes
        def on_scrollbar_state_change(_):
            is_visible = self.scrollable_area._scrollbar.winfo_viewable()
            if is_visible:
                self.static_area.grid_configure(pady=(0, 16))
            else:
                self.static_area.grid_configure(pady=0)

        self.scrollable_area._scrollbar.bind('<Map>', on_scrollbar_state_change)  # Scrollbar appears
        self.scrollable_area._scrollbar.bind('<Unmap>', on_scrollbar_state_change)  # Scrollbar disappears
        # Data Plot & Names Row
        self.data_plot = ctk.CTkFrame(self.scrollable_area, fg_color="#23272B", corner_radius=0)
        self.data_plot.grid(row=0, column=0, sticky="nsew")
        self.names_row = ctk.CTkFrame(self.scrollable_area, fg_color="#181C20", corner_radius=0)
        self.names_row.grid(row=1, column=0, sticky="ew")
        self.s1_create_hours_graph()

        self.days_chart_border = ctk.CTkFrame(
            self.current_week_frame,
            fg_color="#181C20",
            corner_radius=0
        )
        self.days_chart_border.grid(row=2, column=0, rowspan=1, columnspan=3, sticky="nsew")
        self.days_chart_border.grid_rowconfigure(0, weight=1)
        self.days_chart_border.grid_columnconfigure(0, weight=1)
        # Scrollable area inside the border frame
        self.days_chart_frame = CtkSmartScrollableFrame(
            self.days_chart_border,
            fg_color="transparent",
            corner_radius=0,
            orientation="horizontal"
        )
        self.days_chart_frame.grid(row=0, column=0, sticky="nsew", padx=(25, 15), pady=15)
        self.days_chart_frame.grid_rowconfigure(0, weight=1)
        self.days_chart_frame.grid_rowconfigure(1, weight=0, minsize=18)
        self.days_chart_data_row = ctk.CTkFrame(self.days_chart_frame, fg_color="#181C20", corner_radius=0)
        self.days_chart_names_row = ctk.CTkFrame(self.days_chart_frame, fg_color="#181C20", corner_radius=0)
        self.days_chart_data_row.grid(row=0, column=0, sticky="nsew")
        self.days_chart_names_row.grid(row=1, column=0, sticky="ew")
        self.s1_create_days_charts()

        # Frame - 1
        self.team_hours_parent = ctk.CTkFrame(
            self.current_week_frame,
            fg_color="#181C20",
            corner_radius=0
        )
        self.team_hours_parent.grid(row=0, column=3, rowspan=3, columnspan=1, sticky="nsew")
        self.team_hours_parent.grid_rowconfigure(0, weight=0)
        self.team_hours_parent.grid_rowconfigure(1, weight=1)
        self.team_hours_parent.grid_columnconfigure(0, weight=1)
        self.s1_create_team_hours_bar()

        self.s1_create_achievements_panel()

    def s1_build_all_charts_data(self):
        """Returns hours_graph_data, days_charts_data & team_hours_bar_data."""
        # 1) Get base data
        user_day_seconds = defaultdict(lambda: [0 for _ in range(7)])
        for log in self.logs:
            user = log["user"]
            dt = log["timestamp"]
            weekday_idx = to_local(dt).weekday()
            elapsed_seconds = log["elapsed_time"]
            user_day_seconds[user][weekday_idx] += elapsed_seconds

        all_users = user_day_seconds.keys() | self._current_week_goals.keys()

        base_user_data = []
        max_hours = 0.0
        for user in all_users:
            day_seconds = user_day_seconds.get(user, [0] * 7)
            total_hours = sum(day_seconds) / 3600
            days_hours = [secs / 3600 for secs in day_seconds]
            goal_data = self._current_week_goals.get(user, {})
            goal_hours = goal_data.get("hours", 0)
            goal_days = goal_data.get("days", 0)
            base_user_data.append({
                "user": user,
                "total_hours": total_hours,
                "days_hours": days_hours,
                "goal_hours": goal_hours,
                "goal_days": goal_days,
            })
            max_hours = max(max_hours, total_hours, goal_hours)

        # 2) Build data
        # 2.1) Hours Graph
        # [Inclusive max_value ranges] [range of amount of big] [range of amount of all]
        if max_hours <= 5:  # [1, 5]    [1, 5]  [2, 10]
            big_interval, small_interval = 1, 0.5
        elif max_hours <= 10:  # [6, 10]   [3, 5]  [6, 10]
            big_interval, small_interval = 2, 1
        elif max_hours <= 20:  # [15, 20]  [3, 5]  [15, 20]
            big_interval, small_interval = 5, 1
        else:  # [30, 168] [3, 16] [15, 84]
            big_interval, small_interval = 10, 2
        max_value = (int(max_hours) // big_interval + 1) * big_interval
        big_intervals, small_intervals = [], []
        for val in range(0, max_value * 2 + 1, int(small_interval * 2)):
            if val % (big_interval * 2) == 0:
                big_intervals.append(val // 2)  # Int
            else:
                small_intervals.append(val / 2)  # Float

        hours_graph_users_data = []
        for data in base_user_data:
            total_hours = data["total_hours"]
            rel_height = total_hours / max_value
            if total_hours > 0:
                day_segment_rel_heights = [h / total_hours for h in data["days_hours"]]
                tops = []
                accum = 1.0
                for seg in day_segment_rel_heights:
                    if seg > 0:
                        accum -= seg
                        tops.append(accum)
                    else:
                        tops.append(None)
            else:
                day_segment_rel_heights = [0.0] * 7
                tops = [None] * 7
            header_string = f" {self.s1_format_bar_header(total_hours, data['goal_hours'], decimals=1)} "
            if data["goal_hours"]:
                goal_rel_y = 1.0 - (data["goal_hours"] / max_value)
                goal_color = self._goal_color(total_hours, data["goal_hours"])
                goal_line_string = f"{data['goal_hours']} hours"
                goal_data = (goal_rel_y, goal_color, goal_line_string)
            else:
                goal_data = None
            day_bar_header_strings = [
                self.s1_format_bar_header(h, data["goal_hours"]) for h in data["days_hours"]
            ]
            hours_graph_users_data.append({
                "user": data["user"],
                "total_hours": data["total_hours"],
                "goal_hours": data["goal_hours"],
                "rel_height": rel_height,
                "day_segment_rel_heights": day_segment_rel_heights,
                "day_bar_top_rel_ys": tops,
                "header_string": header_string,
                "goal_data": goal_data,
                "day_bar_header_strings": day_bar_header_strings,
            })

        # 2.2) Days Chart
        days_charts_user_data = []
        for data in base_user_data:
            days_hours = data["days_hours"]
            goal_days = data["goal_days"]
            active_days = [i for i, h in enumerate(days_hours) if h]
            inactive_days = [i for i in range(7) if i not in active_days]
            day_order = active_days + inactive_days
            if goal_days > 0:
                goal_color = self._goal_color(len(active_days), goal_days)
                goal_data = (goal_days - 1, goal_color)
            else:
                goal_data = None
            days_charts_user_data.append({
                "user": data["user"],
                "total_hours": data["total_hours"],
                "days_hours": days_hours,
                "goal_days": goal_days,
                "day_order": day_order,
                "goal_data": goal_data,
            })

        # 2.3) Team Hours Bar
        team_hours = sum(d["total_hours"] for d in hours_graph_users_data)
        team_goal = sum(d["goal_hours"] for d in hours_graph_users_data)
        rel_height_team = min(team_hours / team_goal, 1.0) if team_goal else 1.0
        team_hours_bar_user_data = []
        others = []
        if team_hours > 0:
            for d in base_user_data:
                total_hours = d["total_hours"]
                if total_hours <= 0:
                    continue
                ratio = total_hours / team_hours
                percent = ratio * 100
                default_text = f"{d['user']}\n{int(percent)}%"
                hover_text = f"{math.floor(total_hours * 10) / 10.0:g} h"
                entry = {
                    "ratio": ratio,
                    "default_text": default_text,
                    "hover_text": hover_text,
                }
                if percent >= 10:
                    team_hours_bar_user_data.append(entry)
                else:
                    others.append(entry)
            if len(others) > 1:
                others_total_hours = sum(e['total_hours'] for e in others)
                others_ratio = others_total_hours / team_hours
                others_percent = others_ratio * 100
                others = [{
                    "ratio": others_ratio,
                    "default_text": f"Others\n{int(others_percent)}%",
                    "hover_text": f"{math.floor(others_total_hours * 10) / 10.0:g} h",
                }]
        team_header_string = self.s1_format_bar_header(team_hours, team_goal, decimals=0)
        if team_goal > 0:
            team_goal_color = self._goal_color(team_hours, team_goal)
            if team_hours < team_goal:
                team_rel_y = 0
                team_y_offset = 10
            else:
                team_rel_y = 1.0 - (team_goal / team_hours)
                team_y_offset = 5
            goal_data_team = (team_rel_y, team_goal_color, team_y_offset)
        else:
            goal_data_team = None

        # 3) Sort
        hours_graph_users_data.sort(
            key=lambda x: (
                -x["total_hours"],
                -x["goal_hours"],
                x["user"],
            )
        )
        days_charts_user_data.sort(
            key=lambda x: (
                -sum(bool(h) for h in x["days_hours"]),  # Active days count
                -x["goal_days"],
                x["user"],
            )
        )
        team_hours_bar_user_data.sort(key=lambda x: x["ratio"], reverse=True)
        team_hours_bar_user_data.extend(others)

        # 4) Build return dictionaries
        hours_graph_data = {
            "user_data": hours_graph_users_data,
            "big_intervals": big_intervals,
            "small_intervals": small_intervals,
            "max_value": max_value
        }
        days_charts_data = {
            "user_data": days_charts_user_data,
        }
        team_hours_bar_data = {
            "user_data": team_hours_bar_user_data,
            "team_goal": team_goal,
            "rel_height": rel_height_team,
            "header_string": team_header_string,
            "goal_data": goal_data_team,
        }

        return hours_graph_data, days_charts_data, team_hours_bar_data

    @staticmethod
    def draw_horizontal_line(parent, rel_y, height, anchor, color="black"):
        line = ctk.CTkFrame(parent, fg_color=color, height=height, corner_radius=0)
        line.place(relx=0, rely=rel_y, relwidth=1.0, anchor=anchor)

    def s1_create_hours_graph(self):
        # Unpack data
        big_intervals = self.hours_graph_data["big_intervals"]
        small_intervals = self.hours_graph_data["small_intervals"]
        max_value = self.hours_graph_data["max_value"]
        user_data = self.hours_graph_data["user_data"]

        top_margin = 14
        bottom_margin = 19

        # Create inner frame for intermediate labels
        self.inner_frame = ctk.CTkFrame(self.static_area, fg_color="transparent", corner_radius=0, width=25)
        self.inner_frame.pack(fill="both", expand=True, pady=(top_margin, bottom_margin))

        # Place 0- & Max-label outside to avoid clipping
        min_label = ctk.CTkLabel(self.static_area, text=f"{big_intervals[0]}", font=("Arial", 16), text_color="#E0E0E0",
                                 fg_color="transparent", height=10)
        min_label.place(relx=1.0, rely=1.0, anchor="e", x=-4, y=-bottom_margin)
        max_label = ctk.CTkLabel(self.static_area, text=f"{big_intervals[-1]}", font=("Arial", 16),
                                 text_color="#E0E0E0", fg_color="transparent", height=10)
        max_label.place(relx=1.0, rely=0.0, anchor="e", x=-4, y=top_margin)

        # Place Intermediate labels inside using rel_y
        for value in big_intervals[1:-1]:
            rel_y = 1.0 - (value / max_value)  # 0 < rel_y < 1
            label = ctk.CTkLabel(self.inner_frame, text=f"{value}", font=("Arial", 16), text_color="#E0E0E0",
                                 fg_color="transparent", height=10)
            label.place(relx=1.0, rely=rel_y, anchor="e", x=-4)

        # Grid lines on data_plot
        for value, color in [(v, "#444950") for v in big_intervals] + [(v, "#2C313A") for v in small_intervals]:
            rel_y = 1.0 - (value / max_value)
            self.draw_horizontal_line(self.data_plot, rel_y, 2, "sw", color)

        bar_container_frames = []
        column_width = 130  # 100 + 15 * 2
        goal_line_width = 110  # 100 + 5*2
        max_name_width = 90  # 100-2*5

        for col_idx, data in enumerate(user_data):
            user_name = data["user"]
            header_string = data["header_string"]
            rel_height = data["rel_height"]
            day_bar_top_rel_ys = data["day_bar_top_rel_ys"]
            day_segment_rel_heights = data["day_segment_rel_heights"]
            goal_data = data["goal_data"]
            day_bar_header_strings = data["day_bar_header_strings"]

            # Bar Container Frame
            bar_container_frame = ctk.CTkFrame(
                self.data_plot,
                fg_color="transparent",
                corner_radius=0,
                width=100
            )
            bar_container_frame.pack(side="left", fill="y", expand=True, padx=15)
            bar_container_frames.append(bar_container_frame)

            # Grid lines on bar_container_frame
            for value, color in [(v, "#444950") for v in big_intervals] + [(v, "#2C313A") for v in small_intervals]:
                rel_y = 1.0 - (value / max_value)
                self.draw_horizontal_line(bar_container_frame, rel_y, 2, "sw", color)

            # Name Label
            font = ctk.CTkFont(family="Arial", size=16)

            display_text = self.truncate_text_to_pixels(user_name, font, max_name_width)
            user_label = ctk.CTkLabel(
                self.names_row,
                text=display_text,
                font=("Arial", 16),
                text_color="#E0E0E0",
                justify="center",
                fg_color="transparent",
                height=0,
                width=100
            )
            user_label.pack(side="left", fill="y", expand=True)  # placed correctly??

            # Goal lines
            if goal_data:
                rel_y, color, goal_line_string = goal_data
                x = col_idx * column_width + column_width // 2
                goal_line = ctk.CTkFrame(self.data_plot, fg_color=color, height=6, corner_radius=3,
                                         width=goal_line_width, border_color="black", border_width=1)
                goal_line.place(x=x, rely=rel_y, anchor="s", y=2)
                CTkFlexToolTip(goal_line, message=goal_line_string, delay=0.2, bg_color="#696969", corner_radius=5,
                               static_anchor="e", padding=(5, 4), alpha=1, x_offset=3, y_offset=0, border_width=1,
                               border_color="black", text_color="black")

            # Data
            if rel_height == 0:
                continue
            # Side border frame for week bar
            week_bar_side_border = ctk.CTkFrame(
                bar_container_frames[col_idx],
                fg_color="black",
                corner_radius=0
            )
            week_bar_side_border.place(
                relx=0.0,
                rely=1.0,
                relwidth=1.0,
                relheight=rel_height,
                anchor="sw",
            )

            # Week bar
            week_bar = ctk.CTkFrame(
                week_bar_side_border,
                fg_color="#0000C6",
                corner_radius=0,
            )
            week_bar.pack(side="left", fill="both", expand=True, padx=2)

            # Top & Bottom borderlines
            self.draw_horizontal_line(week_bar_side_border, 0, 2, "nw")
            self.draw_horizontal_line(week_bar_side_border, 1.0, 2, "sw")

            # Week Bar Label
            bar_top_rely = 1.0 - rel_height
            week_bar_label = ctk.CTkLabel(
                bar_container_frames[col_idx],
                text=header_string,
                font=ctk.CTkFont(family="Arial", size=14, weight="bold"),
                text_color="#E0E0E0",
                fg_color="transparent",
                height=20
            )
            week_bar_label.update_idletasks()
            bar_container_height = bar_container_frames[col_idx].winfo_height()
            label_anchor = "s"
            label_rely = bar_top_rely
            if bar_top_rely * bar_container_height - 18 < 0:  # 18 = 20 - 2   (label-height - 2)
                label_anchor = "n"
                label_rely = 0
            week_bar_label.place(relx=0.5, rely=label_rely, anchor=label_anchor)

            def update_week_bar_label(event, label=week_bar_label, bar_top_rely=bar_top_rely):
                bar_container_height = event.widget.winfo_height()
                toggled = bar_top_rely * bar_container_height - 18 < 0
                # Only update if the toggled state has changed
                if toggled != getattr(label, '_placement_mode', None):
                    if toggled:
                        label_anchor = "n"
                        label_rely = 0
                    else:
                        label_anchor = "s"
                        label_rely = bar_top_rely
                    label.place_configure(relx=0.5, rely=label_rely, anchor=label_anchor) # type: ignore[arg-type]
                    label._placement_mode = toggled

            bar_container_frames[col_idx].bind("<Configure>", update_week_bar_label)

            # Days Segments
            for i in range(7):
                seg_height = day_segment_rel_heights[i]
                if seg_height > 0:
                    day_bar = ctk.CTkFrame(
                        week_bar,
                        fg_color="#0000C6",
                        corner_radius=0,
                    )
                    day_bar.place(
                        relx=0,
                        rely=day_bar_top_rel_ys[i],
                        relwidth=1.0,
                        relheight=seg_height
                    )

                    # Hover label
                    day_bar._default_string = \
                        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][i]
                    day_bar._detail_string = day_bar_header_strings[i]

                    def on_day_bar_enter(day_bar):
                        if (active := getattr(self, "_active_day_header", None)) and active.winfo_exists():
                            if active.master == day_bar:
                                return  # Return if already active
                            active.destroy()  # Destroy existing global
                        bar_height = day_bar.winfo_height()
                        font_size = min(max(int(bar_height * 0.8), 1), 12)
                        day_header = ctk.CTkLabel(
                            day_bar,
                            text=day_bar._default_string,
                            font=("Arial", font_size, "bold"),
                            text_color="white",
                            fg_color="#0000C6",
                            height=0
                        )
                        day_header.place(relx=0.5, rely=0.5, anchor="center", y=-1)

                        def on_day_bar_click(event):
                            widget = event.widget
                            bar = widget
                            while bar is not None and not hasattr(bar, '_detail_string'):
                                bar = getattr(bar, 'master', None)
                            if bar is None:
                                return
                            label = getattr(bar, '_hover_label', None) or widget
                            if not label.winfo_exists():
                                return
                            label.configure(text=bar._detail_string)

                        def propagate_click(widget):
                            widget.bind("<Button-1>", on_day_bar_click)
                            for child in widget.winfo_children():
                                propagate_click(child)

                        propagate_click(day_bar)
                        day_bar._hover_label = day_header
                        self._active_day_header = day_header

                    day_bar.bind("<Enter>", lambda _, bar=day_bar: on_day_bar_enter(bar))

                    def on_day_bar_leave(event, bar=day_bar):
                        widget_under_mouse = self.winfo_containing(event.x_root, event.y_root)
                        if widget_under_mouse is not None:
                            parent = widget_under_mouse
                            while parent is not None:
                                if parent == bar:
                                    return  # Still inside bar or its children
                                parent = parent.master if hasattr(parent, 'master') else None
                        if hasattr(bar, '_hover_label'):
                            bar._hover_label.destroy()
                            del bar._hover_label
                        # Also clear the global reference if it was this label
                        if hasattr(self, '_active_day_header') and self._active_day_header == getattr(bar, '_hover_label', None): # type: ignore[attr-defined]
                            del self._active_day_header

                    def propagate_hover(widget, bar):
                        widget.bind("<Leave>", lambda e, bar=bar: on_day_bar_leave(e, bar))
                        for child in widget.winfo_children():
                            propagate_hover(child, bar)

                    propagate_hover(day_bar, day_bar)
            # Day separator lines
            for i in range(6):
                if day_bar_top_rel_ys[i] is not None:
                    self.draw_horizontal_line(week_bar, day_bar_top_rel_ys[i], 2, "w")

    def s1_delete_hours_graph(self):
        for container in [self.data_plot, self.names_row, self.static_area]:
            for widget in container.winfo_children():
                widget.destroy()

    def s1_update_hours_graph(self):
        self.s1_delete_hours_graph()
        self.s1_create_hours_graph()

    def s1_create_days_charts(self):
        self._days_chart_user_cols = []
        day_colors = [
            "#FF0000",  # Monday
            "#FF8426",  # Tuesday
            "#E6DA00",  # Wednesday
            "#00D118",  # Thursday
            "#0CE1F0",  # Friday
            "#1F44FF",  # Saturday
            "#8E00BD",  # Sunday
        ]
        for data in self.days_charts_data["user_data"]:
            user = data["user"]
            user_col = ctk.CTkFrame(
                self.days_chart_data_row,
                fg_color="#181C20",
                corner_radius=0,
                width=130
            )
            user_col.pack(side="left", fill="y", expand=True)
            user_col.grid_propagate(False)
            self._days_chart_user_cols.append(user_col)
            font = ctk.CTkFont(family="Arial", size=16)
            max_width = 100 - 10
            display_text = self.truncate_text_to_pixels(user, font, max_width)
            user_label = ctk.CTkLabel(
                self.days_chart_names_row,
                text=display_text,
                font=("Arial", 16),
                text_color="#E0E0E0",
                justify="center",
                fg_color="#181C20",
                height=18,
                width=100
            )
            user_label.pack(side="left", fill="y", expand=True)

        def show_pie(idx):
            for i, pie_tuple in enumerate(self._days_chart_pie_charts): # type: ignore[attr-defined]
                if isinstance(pie_tuple, tuple):
                    pie_chart, pie_background = pie_tuple
                    if pie_chart.winfo_exists() and pie_background.winfo_exists():
                        if i == idx:
                            pie_background.lift()
                            pie_chart.lift()
                        else:
                            pie_chart.lower()
                            pie_background.lower()

        def hide_all_pies():
            for pie_tuple in self._days_chart_pie_charts: # type: ignore[attr-defined]
                if isinstance(pie_tuple, tuple):
                    pie_chart, pie_background = pie_tuple
                    if pie_chart.winfo_exists() and pie_background.winfo_exists():
                        pie_chart.lower()
                        pie_background.lower()
                        if hasattr(pie_chart, 'change_text_mode'):
                            pie_chart.change_text_mode('percentage')

        def propagate_pie_hover(widget, idx):
            """Recursively bind hover events to widget and all its children."""
            widget.bind("<Enter>", lambda e, i=idx: show_pie(i))
            widget.bind("<Leave>", lambda e: hide_all_pies())
            for child in widget.winfo_children():
                propagate_pie_hover(child, idx)

        def update_day_bars(_):
            row_height = self.days_chart_data_row.winfo_height()
            if hasattr(self.days_chart_data_row,
                       '_last_height') and self.days_chart_data_row._last_height == row_height:
                return  # No change, skip update
            self.days_chart_data_row._last_height = row_height
            segment_height = int((row_height + 6 * 3) / 7)  # Uniform (6 times 3 pixels overlap)
            if segment_height % 2 == 1:  # Corrects tkinter flaw, if odd make even
                segment_height -= 1
            self._days_chart_pie_charts = []
            for col_idx, user_col in enumerate(self._days_chart_user_cols):
                user_data = self.days_charts_data["user_data"][col_idx]
                days_hours = user_data["days_hours"]
                goal_days = user_data["goal_days"]
                total_hours = user_data["total_hours"]
                day_order = user_data["day_order"]
                goal_data = user_data["goal_data"]

                # Destroy old
                for child in user_col.winfo_children():
                    child.destroy()
                # Bars
                y = row_height - segment_height
                bar_tops = []
                for bar_idx, i in enumerate(day_order):
                    is_active = days_hours[i] > 0
                    day_bar_boarder = ctk.CTkFrame(
                        user_col,
                        fg_color=day_colors[i] if is_active else "#181C20",
                        corner_radius=40,
                        width=80,
                        height=segment_height,
                        border_width=3,
                        border_color="black"
                    )
                    day_bar_boarder.place(relx=0.5, y=y, anchor="n")
                    # Bottom-lines of bars (excluding first) to cover anti-aliased pixels
                    if bar_idx > 0:
                        corner_radius = min(segment_height / 2, 40)
                        offset = 1.2 * corner_radius  # Linear scaling from 0->0 to 15->18
                        line_length = 80 - (corner_radius * 2) + offset
                        line_y = y + segment_height - 2  # bottom of current bar
                        black_canvas = tkinter.Canvas(
                            user_col,
                            width=line_length,
                            height=2,
                            bg="black",
                            highlightthickness=0,
                            relief="flat"
                        )
                        black_canvas.place(relx=0.5, y=line_y, anchor="n")
                        black_canvas.create_rectangle(0, 0, 0, 0, fill="#15181C", outline="#15181C")  # Top-left pixel
                        black_canvas.create_rectangle(line_length - 1, 0, line_length - 1, 0, fill="#15181C",
                                                      outline="#15181C")  # Top-right pixel
                    bar_tops.append(y)
                    y -= segment_height - 3
                # Goal line
                if goal_days > 0:
                    goal_idx, goal_color = goal_data
                    goal_y = bar_tops[goal_idx]
                    goal_y = max(goal_y - 2, 0)
                    goal_line = ctk.CTkFrame(
                        user_col,
                        fg_color=goal_color,
                        height=6,
                        width=88,
                        corner_radius=3,
                        border_width=1,
                        border_color="black"
                    )
                    goal_line.place(relx=0.5, y=goal_y, anchor="n")
                # Pie-Chart
                if total_hours == 0:
                    self._days_chart_pie_charts.append(None)
                else:
                    # Calculate dynamic radius based on parent height
                    user_col.update_idletasks()
                    parent_height = user_col.winfo_height()
                    radius = min(130, parent_height)
                    pie_chart = CTkPieChart(
                        user_col,
                        line_width=65,
                        border_width=25,
                        border_color="black",
                        segment_border_width=25,
                        segment_border_color="black",
                        text_mode="percentage",
                        radius=radius,
                    )
                    for i, hours in enumerate(days_hours):
                        if hours == 0:
                            continue
                        weekday_abbr = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"][i]
                        pie_chart.add(str(i), hours, color=day_colors[i], text_color="black", custom_text=weekday_abbr)
                    pie_chart.bind("<Button-1>", lambda e, chart=pie_chart: chart.change_text_mode('toggle'))
                    pie_background = ctk.CTkFrame(user_col, fg_color="#181C20", corner_radius=0)
                    pie_background.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
                    pie_background.lower()
                    pie_chart.place(relx=0.5, rely=0.5, anchor="center")
                    pie_chart.lower()
                    self._days_chart_pie_charts.append((pie_chart, pie_background))
                # Bind hover events to the pie chart
                propagate_pie_hover(user_col, col_idx)

        self.days_chart_data_row.bind("<Configure>", update_day_bars)
        update_day_bars(None)

    def s1_delete_days_charts(self):
        for container in [self.days_chart_data_row, self.days_chart_names_row]:
            for widget in container.winfo_children():
                widget.destroy()
        delattr(self.days_chart_data_row, '_last_height')
        self.days_chart_data_row.unbind("<Configure>")

    def s1_update_days_charts(self):
        self.s1_delete_days_charts()
        self.s1_create_days_charts()

    def s1_create_team_hours_bar(self):
        team_goal = self.team_hours_bar_data["team_goal"]
        header_string = self.team_hours_bar_data["header_string"]
        rel_height = self.team_hours_bar_data["rel_height"]
        user_data = self.team_hours_bar_data["user_data"]
        goal_data = self.team_hours_bar_data["goal_data"]

        # Header - 1.1
        header_label = ctk.CTkLabel(
            self.team_hours_parent,
            text=header_string,
            font=("Arial", 18, "bold"),
            text_color="white",
            height=0
        )
        header_label.grid(row=0, column=0, sticky="ew", pady=(5, 2))

        # Outer Bar Frame - 1.2
        team_hours_outer = ctk.CTkFrame(
            self.team_hours_parent,
            fg_color="transparent",
            corner_radius=0,
        )
        team_hours_outer.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 15))

        # Border Frame - 1.2.1
        team_hours_bar_boarder_frame = ctk.CTkFrame(
            team_hours_outer,
            fg_color="transparent",
            corner_radius=0
        )
        team_hours_bar_boarder_frame.pack(fill="both", expand=True, padx=5, pady=0)

        # Bar Border - 1.2.1.1
        team_hours_bar_boarder = ctk.CTkFrame(
            team_hours_bar_boarder_frame,
            fg_color="black",
            corner_radius=0
        )
        team_hours_bar_boarder.place(relx=0, rely=1.0, relwidth=1.0, relheight=rel_height, anchor="sw")
        # Bar - 1.2.1.1.1
        team_hours_bar = ctk.CTkFrame(
            team_hours_bar_boarder,
            fg_color="transparent",
            corner_radius=0
        )
        team_hours_bar.pack(fill="both", expand=True, padx=3, pady=0)
        team_hours_bar.pack_propagate(False)
        team_hours_bar.update_idletasks()
        # user bars
        y_offset = 0.0
        bar_boundaries = []
        for data in user_data:
            ratio = data['ratio'] # type: ignore
            default_text = data['default_text'] # type: ignore
            hover_text = data['hover_text'] # type: ignore

            user_bar = ctk.CTkFrame(
                team_hours_bar,
                fg_color="#0000C6",
                corner_radius=0,
            )
            user_bar.place(relx=0, rely=y_offset, relwidth=1.0, relheight=ratio, anchor="nw")
            # Label
            label = ctk.CTkLabel(
                user_bar,
                text=default_text,
                font=("Arial", 14, "bold"),
                text_color="#E0E0E0",
                fg_color="transparent"
            )
            label.place(relx=0.5, rely=0.5, anchor="center")

            # Hover
            def on_enter(_, lbl=label, txt=hover_text):
                lbl.configure(text=txt)

            def on_leave(_, lbl=label, txt=default_text):
                lbl.configure(text=txt)

            user_bar.bind("<Enter>", on_enter)
            user_bar.bind("<Leave>", on_leave)
            label.bind("<Enter>", on_enter)
            label.bind("<Leave>", on_leave)
            bar_boundaries.append(y_offset)
            y_offset += ratio
        # Lines
        self.draw_horizontal_line(team_hours_bar_boarder, 0, 3, "nw")  # Top
        self.draw_horizontal_line(team_hours_bar_boarder, 1.0, 3, "sw")  # Bottom
        for boundary in bar_boundaries[1:]:  # Between users
            self.draw_horizontal_line(team_hours_bar_boarder, boundary, 3, "w")
        # Goal line
        if team_goal > 0:
            rel_y, goal_color, y_offset = goal_data
            goal_line = ctk.CTkFrame(team_hours_outer, fg_color=goal_color, height=10, corner_radius=5,
                                     border_color="black", border_width=2)
            goal_line.place(relx=0.5, rely=rel_y, anchor="s", y=y_offset, relwidth=1)
            CTkFlexToolTip(goal_line, message=f"{team_goal} hours", delay=0.2, bg_color="#696969", corner_radius=5,
                           static_anchor="e", padding=(5, 4), alpha=1, x_offset=3, y_offset=0, border_width=1,
                           border_color="black", text_color="black")

    def s1_delete_team_hours_bar(self):
        for widget in self.team_hours_parent.winfo_children():
            widget.destroy()

    def s1_update_team_hours_bar(self):
        self.s1_delete_team_hours_bar()
        self.s1_create_team_hours_bar()

    def s1_create_achievements_panel(self):
        # --- RECORDS AND ACHIEVEMENTS DISPLAY (columns 4-5) ---
        # Create records frame spanning columns 4-5
        self.records_frame = ctk.CTkFrame(
            self.current_week_frame,
            fg_color="#181C20",
            corner_radius=0
        )
        self.records_frame.grid(row=0, column=4, rowspan=3, columnspan=2, sticky="nsew")
        self.records_frame.grid_rowconfigure(0, weight=0)  # Header
        self.records_frame.grid_rowconfigure(1, weight=1)  # Content
        self.records_frame.grid_columnconfigure(0, weight=1)
        # Header
        records_header = ctk.CTkLabel(
            self.records_frame,
            text="Records & Achievements",
            font=("Arial", 18, "bold"),
            text_color="white",
            height=0
        )
        records_header.grid(row=0, column=0, sticky="ew", pady=(5, 2))
        # Create scrollable content area
        self.records_content = CtkSmartScrollableFrame(
            self.records_frame,
            fg_color="transparent",
            corner_radius=0
        )
        self.records_content.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 15))
        # Populate records and achievements
        self.s1_update_records_display()

    def s1_update_records_display(self):
        # TODO: Fix records and achievements

        """Update the records and achievements display for the current week (using self.current_year_num and self.current_week_num)."""
        # Clear existing content
        for widget in self.records_content.winfo_children():
            widget.destroy()

        # Get highscores data
        highscores = self.highscores_data
        if not highscores:
            no_records_label = ctk.CTkLabel(
                self.records_content,
                text="No records found",
                font=("Arial", 16),
                text_color="#B0B0B0"
            )
            no_records_label.pack(pady=20)
            return

        # Track records found
        records_found = []

        # Helper: check if a highscore date is in the current week
        def is_in_current_week(date_value):
            dt = coerce_highscore_datetime(date_value)
            if dt is None:
                return False
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return self.current_week_start <= dt < self.next_week_start

        # Check personal records
        for user in self.online_users:
            if user in highscores:
                user_records = highscores[user]
                for time_type in ["Day", "Week", "Month", "Year"]:
                    if time_type in user_records:
                        # Check time records
                        if "time" in user_records[time_type] and user_records[time_type]["time"]["date"]:
                            date_str = user_records[time_type]["time"]["date"]
                            if is_in_current_week(date_str):
                                records_found.append({
                                    "type": "Personal Best",
                                    "user": user,
                                    "time_type": time_type,
                                    "metric": "Time",
                                    "value": format_time(user_records[time_type]["time"]["value"]),
                                    "date": date_str
                                })
                        # Check activity records (except for Day)
                        if time_type != "Day" and "activity" in user_records[time_type] and \
                                user_records[time_type]["activity"]["date"]:
                            date_str = user_records[time_type]["activity"]["date"]
                            if is_in_current_week(date_str):
                                activity_ratio = user_records[time_type]["activity"]["value"]
                                active_days = user_records[time_type]["activity"]["active_days"]
                                total_days = user_records[time_type]["activity"]["total_days"]
                                records_found.append({
                                    "type": "Personal Best",
                                    "user": user,
                                    "time_type": time_type,
                                    "metric": "Activity",
                                    "value": f"{active_days}/{total_days} days ({activity_ratio:.1%})",
                                    "date": date_str
                                })
                if "consecutive" in user_records:
                    for streak_kind, label in (("days", "Consecutive days"), ("weeks", "Consecutive weeks")):
                        record = user_records["consecutive"].get(streak_kind)
                        if record and record.get("date") and is_in_current_week(record["date"]):
                            records_found.append({
                                "type": "Personal Best",
                                "user": user,
                                "time_type": "Lifetime",
                                "metric": label,
                                "value": str(record["value"]),
                                "date": record["date"],
                            })
        # Check world records
        if "Global" in highscores:
            global_records = highscores["Global"]
            for time_type in ["Day", "Week", "Month", "Year"]:
                if time_type in global_records:
                    # Check time records
                    if "time" in global_records[time_type] and global_records[time_type]["time"]["date"]:
                        date_str = global_records[time_type]["time"]["date"]
                        if is_in_current_week(date_str):
                            records_found.append({
                                "type": "World Record",
                                "user": global_records[time_type]["time"].get("user", "?"),
                                "time_type": time_type,
                                "metric": "Time",
                                "value": format_time(global_records[time_type]["time"]["value"]),
                                "date": date_str
                            })
                    # Check activity records (except for Day)
                    if time_type != "Day" and "activity" in global_records[time_type] and \
                            global_records[time_type]["activity"]["date"]:
                        date_str = global_records[time_type]["activity"]["date"]
                        if is_in_current_week(date_str):
                            activity_ratio = global_records[time_type]["activity"]["value"]
                            active_days = global_records[time_type]["activity"]["active_days"]
                            total_days = global_records[time_type]["activity"]["total_days"]
                            records_found.append({
                                "type": "World Record",
                                "user": global_records[time_type]["activity"].get("user", "?"),
                                "time_type": time_type,
                                "metric": "Activity",
                                "value": f"{active_days}/{total_days} days ({activity_ratio:.1%})",
                                "date": date_str
                            })
            if "consecutive" in global_records:
                for streak_kind, label in (("days", "Consecutive days"), ("weeks", "Consecutive weeks")):
                    record = global_records["consecutive"].get(streak_kind)
                    if record and record.get("date") and is_in_current_week(record["date"]):
                        records_found.append({
                            "type": "World Record",
                            "user": record.get("user", "?"),
                            "time_type": "Lifetime",
                            "metric": label,
                            "value": str(record["value"]),
                            "date": record["date"],
                        })
        # Check team records
        if "Combined" in highscores:
            combined_records = highscores["Combined"]
            for time_type in ["Day", "Week", "Month", "Year"]:
                if time_type in combined_records:
                    # Check time records
                    if "time" in combined_records[time_type] and combined_records[time_type]["time"]["date"]:
                        date_str = combined_records[time_type]["time"]["date"]
                        if is_in_current_week(date_str):
                            records_found.append({
                                "type": "Team Record",
                                "user": "All users",
                                "time_type": time_type,
                                "metric": "Time",
                                "value": format_time(combined_records[time_type]["time"]["value"]),
                                "date": date_str
                            })
                    # Check activity records (except for Day)
                    if time_type != "Day" and "activity" in combined_records[time_type] and \
                            combined_records[time_type]["activity"]["date"]:
                        date_str = combined_records[time_type]["activity"]["date"]
                        if is_in_current_week(date_str):
                            activity_ratio = combined_records[time_type]["activity"]["value"]
                            active_days = combined_records[time_type]["activity"]["active_days"]
                            total_days = combined_records[time_type]["activity"]["total_days"]
                            records_found.append({
                                "type": "Team Record",
                                "user": "All users",
                                "time_type": time_type,
                                "metric": "Activity",
                                "value": f"{active_days}/{total_days} days ({activity_ratio:.1%})",
                                "date": date_str
                            })
            if "consecutive" in combined_records:
                for streak_kind, label in (("days", "Consecutive days"), ("weeks", "Consecutive weeks")):
                    record = combined_records["consecutive"].get(streak_kind)
                    if record and record.get("date") and is_in_current_week(record["date"]):
                        records_found.append({
                            "type": "Team Record",
                            "user": "All users",
                            "time_type": "Lifetime",
                            "metric": label,
                            "value": str(record["value"]),
                            "date": record["date"],
                        })
        # Sort records by date (newest first)
        records_found.sort(
            key=lambda x: coerce_highscore_datetime(x["date"]) or datetime.datetime.min,
            reverse=True,
        )
        # Display records
        if records_found:
            for record in records_found:
                # Create record frame
                record_frame = ctk.CTkFrame(self.records_content, fg_color="#23272B", corner_radius=5)
                record_frame.pack(fill="x", pady=5, padx=5)
                # Record type and user
                type_user_label = ctk.CTkLabel(
                    record_frame,
                    text=f"{record['type']} by {record['user']}",
                    font=("Arial", 14, "bold"),
                    text_color="white"
                )
                type_user_label.pack(anchor="w", padx=10, pady=(5, 2))
                # Time type and metric
                metric_label = ctk.CTkLabel(
                    record_frame,
                    text=f"{record['time_type']} {record['metric']}: {record['value']}",
                    font=("Arial", 12),
                    text_color="#E0E0E0"
                )
                metric_label.pack(anchor="w", padx=10, pady=(0, 2))
                # Date
                date_label = ctk.CTkLabel(
                    record_frame,
                    text=f"Set on: {format_highscore_date(record['date'])}",
                    font=("Arial", 10),
                    text_color="#B0B0B0"
                )
                date_label.pack(anchor="w", padx=10, pady=(0, 5))
        else:
            no_records_label = ctk.CTkLabel(
                self.records_content,
                text="No records set this week",
                font=("Arial", 16),
                text_color="#B0B0B0"
            )
            no_records_label.pack(pady=20)

    @staticmethod
    def s1_format_bar_header(hours, goal_hours, decimals=1):
        """Format the header string for hours and percentage of goal, with configurable decimals."""
        if hours:
            factor = 10 ** decimals
            formatted_hours = int(hours * factor) / factor
            formatted_hours = f"{formatted_hours:g} h"
            if goal_hours:
                percentage_of_goal = (hours / goal_hours) * 100
                formatted_percentage = f" ({int(percentage_of_goal)}%)"  # rounded down, no decimals
            else:
                formatted_percentage = ""
            return f"{formatted_hours}{formatted_percentage}"
        else:
            return ""

    ### SLIDE 2 ###
    def slide_2(self):
        """Logs"""
        title = ctk.CTkLabel(
            self.slide_frames[2],
            text="user Logs",
            font=("Arial", 60, "bold"),
            text_color="white"
        )
        title.place(relx=0.028125, rely=0.05, anchor="nw")
        # Main Frame
        self.logs_user_container = ctk.CTkFrame(self.slide_frames[2])
        self.logs_user_container.place(relx=0.5, rely=0.5, relwidth=0.8, relheight=0.7, anchor="center")
        # Outer Logs Frame
        self.logs_frame = ctk.CTkFrame(self.logs_user_container)
        self.logs_frame.pack(side="bottom", fill="both", expand=True, padx=10, pady=(1, 10))
        # Selected User Name, default to self
        self.s2_selected_user_var = ctk.StringVar(value=self.user_name)
        # Selected User - dropdown button
        self.s2_selected_user_label = ctk.CTkLabel(
            self.logs_user_container,
            textvariable=self.s2_selected_user_var,
            font=("Arial", 40, "bold"),
            text_color="white",
            height=38,
        )
        self.s2_selected_user_label._label.configure(cursor="hand2")
        self.s2_selected_user_label._canvas.configure(cursor="hand2")
        self.s2_selected_user_label.pack(ipadx=6, anchor="w", padx=6, pady=(6, 0))
        self.s2_selected_user_label.bind("<Enter>", self.s2_selected_user_enter)
        self.s2_selected_user_label.bind("<Leave>", self.s2_selected_user_leave)
        self.s2_selected_user_label.bind("<Button-1>", self.s2_show_dropdown)
        self.s2_user_list_frame = ctk.CTkFrame(
            self.logs_user_container,
            fg_color='gray20',
            corner_radius=0
        )
        self.s2_create_selectable_users()
        # Scrollable Frame
        self.logs_scrollable_frame = CtkSmartScrollableFrame(
            self.logs_frame,
            fg_color="transparent",
            corner_radius=0
        )
        self.logs_scrollable_frame.pack(fill="both", expand=True, padx=5, pady=8)
        # Currently Expanded Log
        self.expanded_log_widget = None
        self.log_widgets = []
        self.selected_log_index = -1

        self.s2_set_logs_by_user()
        self.s2_create_user_log_widgets()

        # Bind arrow keys for navigation
        self.bind("<Up>", self.s2_arrow_up)
        self.bind("<Down>", self.s2_arrow_down)

    def s2_create_selectable_users(self):
        """Create the dropdown list of users for logs (slide 2)."""
        # Create list
        self.s2_selectable_users = sorted({log.get("user") for log in self.logs})
        # Update selected if invalid
        if self.s2_selected_user_var.get() not in self.s2_selectable_users:
            self.s2_selected_user_var.set(self.s2_selectable_users[0])
        # Create UI
        for user in self.s2_selectable_users:
            user_frame = ctk.CTkFrame(
                self.s2_user_list_frame,
                fg_color="transparent",
                height=30
            )
            user_frame.pack(fill="x", padx=5)
            user_label = ctk.CTkLabel(
                user_frame,
                text=user,
                font=("Arial", 16),
                text_color="white",
                anchor="w",
                fg_color="transparent"
            )
            user_label._label.configure(cursor="hand2")
            user_label._canvas.configure(cursor="hand2")
            user_label.pack(fill="both", expand=True)
            for widget in (user_frame, user_label):
                widget.bind("<Enter>", lambda e: self.s2_dropdown_enter(e))
                widget.bind("<Leave>", self.s2_dropdown_leave)
                widget.bind("<Button-1>", lambda e, u=user: self.s2_dropdown_select(e, u))

    def s2_destroy_selectable_users(self):
        """Destroy all dropdown user items for logs (slide 2)."""
        for item in self.s2_user_list_frame.winfo_children():
            item.destroy()

    def s2_update_selectable_users(self):
        """Refresh the dropdown user list for logs (slide 2)."""
        self.s2_destroy_selectable_users()
        self.s2_create_selectable_users()

    def s2_selected_user_enter(self, _event):
        """Handle hover enter on logs user label"""
        self.s2_selected_user_label.configure(fg_color="gray20")

    def s2_selected_user_leave(self, _event):
        """Handle hover leave on logs user label"""
        if not self.s2_user_list_frame.winfo_ismapped():
            self.s2_selected_user_label.configure(fg_color="transparent")

    def s2_show_dropdown(self, _event):
        """Show dropdown for logs user selection"""
        self.s2_user_list_frame.place(x=6, y=52, anchor="nw")  # 52=6+38+8
        self.bind("<Button-1>", self.s2_hide_dropdown)

    def s2_hide_dropdown(self, event=None):
        """Hide the logs user dropdown"""
        self.s2_user_list_frame.place_forget()
        self.unbind("<Button-1>")
        if event and self.winfo_containing(event.x_root, event.y_root).master != self.s2_selected_user_label:
            self.s2_selected_user_label.configure(fg_color="transparent")

    @staticmethod
    def s2_dropdown_enter(event):
        """Handle hover on dropdown item"""
        event.widget.master.configure(fg_color="#2C2C2C")

    @staticmethod
    def s2_dropdown_leave(event):
        """Handle stop hover on dropdown item"""
        event.widget.master.configure(fg_color="transparent")

    def s2_dropdown_select(self, _event, user):
        """Handle selection of a user from logs dropdown"""
        self.s2_selected_user_var.set(user)
        self.s2_hide_dropdown()
        self.s2_create_user_log_widgets()

    def s2_set_logs_by_user(self):
        """Creates or updates self.logs_by_user"""
        # Copy, Sort & Group Logs
        logs = list(self.logs)  # Shallow copy
        logs.sort(key=lambda x: x.get("timestamp", datetime.datetime.min))
        self.logs_by_user = defaultdict(list)
        for log in logs:
            self.logs_by_user[log["user"]].append(log)

    def s2_create_user_log_widgets(self):
        """Create log widgets for the specified user and store references for navigation"""
        user = self.s2_selected_user_var.get()
        # Clear Existing Logs
        for widget in self.logs_scrollable_frame.winfo_children():
            widget.destroy()
        self.log_widgets = []
        self.expanded_log_widget = None
        self.selected_log_index = -1
        # Create New Logs
        for log in self.logs_by_user.get(user):
            self.s2_create_log_widget(log)

    def s2_create_log_widget(self, log):
        """Create a single log widget with expand/collapse functionality. If return_frame is True, return the log_frame."""
        # Extract log data
        name = log.get("name", "No title")
        dt = log["timestamp"]
        weekday = dt.strftime("%A")
        start_time_formatted = dt.strftime("%H:%M")
        elapsed_time = log.get("elapsed_time", 0)
        description = log.get("description", "No description")
        elapsed_time_formatted = format_time(elapsed_time)
        # Frame
        log_frame = ctk.CTkFrame(
            self.logs_scrollable_frame,
            fg_color="#23272B",
            corner_radius=8,
            bg_color="#333333"
        )
        log_frame.pack(fill="x", pady=2, padx=5)
        # Collapsed view
        collapsed_frame = ctk.CTkFrame(log_frame, fg_color="transparent")
        collapsed_frame.pack(fill="x", padx=10, pady=5)
        collapsed_frame.grid_columnconfigure(0, weight=1)  # Name (expanding)
        collapsed_frame.grid_columnconfigure(1, weight=0)  # DateTime (fixed width)
        collapsed_frame.grid_columnconfigure(2, weight=0)  # Elapsed time (fixed width)
        # Labels
        name_label = ctk.CTkLabel(
            collapsed_frame,
            text=name,
            font=("Arial", 20, "bold"),
            text_color="white",
            anchor="w"
        )
        name_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        datetime_label = ctk.CTkLabel(
            collapsed_frame,
            text=f"{weekday} {start_time_formatted}",
            font=("Arial", 18),
            text_color="#E0E0E0",
            width=160,
            anchor="w",
        )
        datetime_label.grid(row=0, column=1, sticky="w", padx=(0, 10))
        elapsed_time_label = ctk.CTkLabel(
            collapsed_frame,
            text=elapsed_time_formatted,
            font=("Arial", 18),
            text_color="#E0E0E0",
            width=80,
            anchor="e",
        )
        elapsed_time_label.grid(row=0, column=2)
        # Create expanded view
        expanded_frame = ctk.CTkFrame(log_frame, fg_color="transparent")
        # Description Label
        description_label = ctk.CTkLabel(
            expanded_frame,
            text=description,
            font=("Arial", 18),
            text_color="#E0E0E0",
            anchor="w",
            wraplength=600,  # Wrap long descriptions
            justify="left"
        )
        description_label.pack(fill="x", padx=10, pady=(0, 7))
        # Store references for expand/collapse
        log_frame._collapsed_frame = collapsed_frame
        log_frame._expanded_frame = expanded_frame
        log_frame._is_expanded = False

        # Bind click event to toggle expand/collapse
        def on_log_click(_):
            self.s2_toggle_log_expansion(log_frame) # type: ignore[attr-defined]

        # Bind click to all child widgets
        for widget in [log_frame, collapsed_frame, datetime_label, name_label, elapsed_time_label, expanded_frame,
                       description_label]:
            widget.bind("<Button-1>", on_log_click)
            widget.configure(cursor="hand2")

        self.log_widgets.append(log_frame)

    def s2_toggle_log_expansion(self, log_frame):
        """Toggle the expansion state of a log widget"""
        # If already expanded, collapse
        if log_frame._is_expanded:
            log_frame._expanded_frame.pack_forget()
            log_frame._is_expanded = False
            self.expanded_log_widget = None
            return
        # Collapse previous log
        if self.expanded_log_widget:
            self.expanded_log_widget._expanded_frame.pack_forget()
            self.expanded_log_widget._is_expanded = False
        # Expand log
        log_frame._expanded_frame.pack(fill="x", padx=10, pady=(0, 5))
        log_frame._is_expanded = True
        self.expanded_log_widget = log_frame
        # Update selected_log_index to match the clicked log
        if hasattr(self, 'log_widgets') and log_frame in self.log_widgets:
            self.selected_log_index = self.log_widgets.index(log_frame)
        # Scroll the expanded log into view
        if hasattr(self, 'logs_scrollable_frame'):
            self.logs_scrollable_frame.scroll_widget_into_view(log_frame, margin=4)

    def s2_arrow_up(self, _event):
        if not hasattr(self, 'log_widgets') or not self.log_widgets:
            return
        if self.selected_log_index > -1:
            self.selected_log_index -= 1
        else:
            self.selected_log_index = -1
        self.s2_update_log_selection()

    def s2_arrow_down(self, _event):
        if not hasattr(self, 'log_widgets') or not self.log_widgets:
            return
        if self.selected_log_index < len(self.log_widgets):
            self.selected_log_index += 1
        else:
            self.selected_log_index = len(self.log_widgets)
        self.s2_update_log_selection()

    def s2_update_log_selection(self):
        # -1 = before first, 0...N-1 = logs, N = after last
        if self.selected_log_index == -1 or self.selected_log_index == len(self.log_widgets):
            # Collapse
            if self.expanded_log_widget:
                self.expanded_log_widget._expanded_frame.pack_forget()
                self.expanded_log_widget._is_expanded = False
                self.expanded_log_widget = None
        elif -1 < self.selected_log_index < len(self.log_widgets):
            log_frame = self.log_widgets[self.selected_log_index]
            if self.expanded_log_widget and self.expanded_log_widget != log_frame:
                # Collapse
                self.expanded_log_widget._expanded_frame.pack_forget()
                self.expanded_log_widget._is_expanded = False
            if not log_frame._is_expanded:
                # Expand
                log_frame._expanded_frame.pack(fill="x", padx=10, pady=(0, 5))
                log_frame._is_expanded = True
                self.expanded_log_widget = log_frame
            # Scroll the expanded log into view
            if hasattr(self, 'logs_scrollable_frame'):
                self.logs_scrollable_frame.scroll_widget_into_view(log_frame, margin=4)

    ### SLIDE 3 ###
    def slide_3(self):
        """Discussion points"""
        title = ctk.CTkLabel(
            self.slide_frames[3],
            text="Discussion Points",
            font=("Arial", 60, "bold"),
            text_color="white"
        )
        title.place(relx=0.028125, rely=0.05, anchor="nw")
        self.discussion_frame = ctk.CTkFrame(self.slide_frames[3])
        self.discussion_frame.place(relx=0.5, rely=0.5, relwidth=0.8, relheight=0.7, anchor="center")
        self.s3_create_discussion_points()

    def s3_create_discussion_points(self):
        for point in self.discussion_points:
            title = point.get("title", "")
            description = point.get("description", None)
            point_label = ctk.CTkLabel(
                self.discussion_frame,
                text=f"• {title}",
                font=("Arial", 40, "bold"),
                text_color="white"
            )
            point_label.pack(anchor="w", padx=30, pady=(25, 0))
            if description:
                CTkFlexToolTip(point_label, message=description, delay=0.2, bg_color="#696969", corner_radius=5,
                               static_anchor="e", padding=(5, 4), alpha=1, x_offset=8, y_offset=0, border_width=1,
                               border_color="black", text_color="black")

    def s3_delete_discussion_points(self):
        for w in self.discussion_frame.winfo_children():
            w.destroy()

    def s3_update_discussion_points(self):
        self.s3_delete_discussion_points()
        self.s3_create_discussion_points()

    ### SLIDE 4 ###
    def slide_4(self):
        """Next week's goals"""
        self.s4_in_input = True

        title = ctk.CTkLabel(
            self.slide_frames[4],
            text="Next Week's Goals",
            font=("Arial", 60, "bold"),
            text_color="white"
        )
        title.place(relx=0.028125, rely=0.05, anchor="nw")

        def _s4_create_input_screen():
            self.goals_input_screen = ctk.CTkFrame(self.slide_frames[4])
            self.goals_input_screen.place(relx=0.5, rely=0.5, relwidth=0.8, relheight=0.7, anchor="center")
            main_frame = ctk.CTkFrame(self.goals_input_screen, fg_color="transparent")
            main_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            main_frame.grid_columnconfigure(0, weight=1, uniform='pad_col')  # Column 0
            main_frame.grid_columnconfigure(1, weight=0, uniform='input_col', minsize=310)  # Column 1 - left_column
            main_frame.grid_columnconfigure(2, weight=1, uniform='pad_col')  # Column 2
            main_frame.grid_columnconfigure(3, weight=0, uniform='input_col', minsize=310)  # Column 3 - right_column
            main_frame.grid_columnconfigure(4, weight=1, uniform='pad_col')  # Column 4
            main_frame.grid_rowconfigure(0, weight=1)

            left_column = ctk.CTkFrame(main_frame, fg_color="transparent")
            left_column.grid(row=0, column=1, sticky="nsew")
            right_column = ctk.CTkFrame(main_frame, fg_color="transparent")
            right_column.grid(row=0, column=3, sticky="nsew")

            def _create_days():
                days_label = ctk.CTkLabel(
                    left_column,
                    text="Days Active",
                    font=("Arial", 40),
                    text_color="white"
                )
                days_label.place(relx=0.5, rely=0.5, anchor="s", y=-60)
                days_buttons_frame = ctk.CTkFrame(left_column)
                days_buttons_frame.place(relx=0.5, rely=0.5, anchor="center")

                self.days_buttons = []
                self.selected_days = 0

                for i in range(7):
                    button_frame = ctk.CTkFrame(
                        days_buttons_frame,
                        fg_color="black",
                        width=40,
                        height=80
                    )
                    button_frame.pack(side="left", padx=2)
                    btn = ctk.CTkButton(
                        button_frame,
                        text="",
                        width=36,
                        height=76,
                        fg_color="#e74c3c",
                        hover=False,
                        command=lambda x=i + 1: self.s4_update_days_goal(x)
                    )
                    btn.place(relx=0.5, rely=0.5, anchor="center")
                    self.days_buttons.append(btn)

            def _create_hours():
                hours_label = ctk.CTkLabel(
                    right_column,
                    text="Total Hours",
                    font=("Arial", 40),
                    text_color="white"
                )
                hours_label.place(relx=0.5, rely=0.5, anchor="s", y=-60)
                self.hours_entry = ctk.CTkEntry(
                    right_column,
                    width=304,
                    height=80,
                    font=("Arial", 40),
                    placeholder_text="int.MaxValue",
                    justify="center"
                )
                self.hours_entry.place(relx=0.5, rely=0.5, anchor="center")
                self.hours_entry.bind("<KeyRelease>", lambda e: self.s4_update_hourly_goal())

            def _create_user_selection():
                self.s4_selected_user_var = ctk.StringVar(value=self.user_name) # Default to self

                self.s4_selected_user_label = ctk.CTkLabel(
                    self.goals_input_screen,
                    textvariable=self.s4_selected_user_var,
                    font=("Arial", 40, "bold"),
                    text_color="white",
                    height=38
                )
                self.s4_selected_user_label._canvas.configure(cursor="hand2")
                self.s4_selected_user_label._label.configure(cursor="hand2")
                self.s4_selected_user_label.pack(ipadx=6, anchor="w", padx=6, pady=(6, 0))
                self.s4_selected_user_label.bind("<Enter>", self.s4_selected_user_enter)
                self.s4_selected_user_label.bind("<Leave>", self.s4_selected_user_leave)
                self.s4_selected_user_label.bind("<Button-1>", self.s4_show_dropdown)
                self.s4_user_list_frame = ctk.CTkFrame(
                    self.goals_input_screen,
                    fg_color='gray20',
                    corner_radius=0
                )
                self.s4_create_selectable_users()

            _create_days()
            _create_hours()
            _create_user_selection()

            confirm_goals_btn = ctk.CTkButton(
                self.goals_input_screen,
                text="Confirm",
                command=lambda: self.s4_update_hourly_goal(show_summary=True),
                font=("Arial", 40, "bold"),
                text_color="white",
                fg_color="transparent",
                hover_color="gray20",
                height=40,
                corner_radius=0
            )
            confirm_goals_btn.pack(side="right", anchor="se", padx=6, pady=6)

            self.s4_update_input_ui()

        def _s4_create_display_screen():
            self.goals_view_screen = ctk.CTkFrame(self.slide_frames[4])
            self.goals_view_screen.place(relx=0.5, rely=0.5, relwidth=0.8, relheight=0.7, anchor="center")

            # Create back label (acts as a button)
            back_label = ctk.CTkLabel(
                self.goals_view_screen,
                text="◀ Back",
                font=("Arial", 40, "bold"),
                text_color="white",
                height=38,
                fg_color="transparent"
            )
            back_label.pack(ipadx=6, anchor="w", padx=6, pady=(6, 0))
            back_label.bind("<Button-1>", lambda e: self.s4_show_input())

            def on_back_hover_enter(_):
                back_label.configure(fg_color="gray20")
                back_label.configure(cursor="hand2")

            def on_back_hover_leave(_):
                back_label.configure(fg_color="transparent")
                back_label.configure(cursor="")

            back_label.bind("<Enter>", on_back_hover_enter)
            back_label.bind("<Leave>", on_back_hover_leave)

            # Create goals display frame (like logs_frame in slide 2)
            self.goals_frame = ctk.CTkFrame(self.goals_view_screen)
            self.goals_frame.pack(fill="both", expand=True, padx=10, pady=(1, 10))

            # Create header row (before scrollable frame)
            self._goals_display_header_frame = ctk.CTkFrame(self.goals_frame, fg_color="#23272B", corner_radius=8,
                                                            bg_color="#333333")
            static_header_row = ctk.CTkFrame(self._goals_display_header_frame, fg_color="transparent")
            static_header_row.pack(fill="x", padx=10, pady=5)
            for i in range(7):
                if i in (1, 3, 5):
                    static_header_row.grid_columnconfigure(i, weight=0, uniform="content_col", minsize=150)
                else:
                    static_header_row.grid_columnconfigure(i, weight=1, uniform="pad_colum")
            ctk.CTkLabel(
                static_header_row,
                text="user",
                font=("Arial", 20, "bold"),
                text_color="white",
                anchor="center"
            ).grid(row=0, column=1, sticky="nsew", padx=(0, 10))
            ctk.CTkLabel(
                static_header_row,
                text="Days Active",
                font=("Arial", 20, "bold"),
                text_color="white",
                anchor="center"
            ).grid(row=0, column=3, sticky="nsew", padx=(0, 10))
            ctk.CTkLabel(
                static_header_row,
                text="Hours",
                font=("Arial", 20, "bold"),
                text_color="white",
                anchor="center"
            ).grid(row=0, column=5, sticky="nsew")
            self._goals_display_header_frame.pack(fill="x", pady=(10, 0), padx=10)

            self.goals_display_frame = CtkSmartScrollableFrame(
                self.goals_frame,
                fg_color="transparent",
                corner_radius=0
            )
            self.goals_display_frame.pack(fill="both", expand=True, padx=5, pady=(2, 8))

            def on_goals_scrollbar_state_change(_):
                is_visible = self.goals_display_frame._scrollbar.winfo_viewable()
                if self._goals_display_header_frame is not None:
                    if is_visible:
                        self._goals_display_header_frame.pack_configure(padx=(10, 26))
                    else:
                        self._goals_display_header_frame.pack_configure(padx=10)

            self.goals_display_frame._scrollbar.bind('<Map>', on_goals_scrollbar_state_change)
            self.goals_display_frame._scrollbar.bind('<Unmap>', on_goals_scrollbar_state_change)

            self._goals_display_widgets = {}

        _s4_create_display_screen()
        _s4_create_input_screen()

    def s4_create_selectable_users(self):
        self.s4_selectable_users = sorted(
            self.online_users | self._current_week_goals.keys() | self._next_week_goals.keys() | {self.user_name})
        # Update selected if invalid
        if self.s4_selected_user_var.get() not in self.s4_selectable_users: # type: ignore[attr-defined]
            self.s4_selected_user_var.set(self.s4_selectable_users[0]) # type: ignore[attr-defined]
        # Create UI
        for user in self.s4_selectable_users:
            user_frame = ctk.CTkFrame(
                self.s4_user_list_frame, # type: ignore[attr-defined]
                fg_color="transparent",
                height=30
            )
            user_frame.pack(fill="x", padx=5)
            user_label = ctk.CTkLabel(
                user_frame,
                text=user,
                font=("Arial", 16),
                text_color="white",
                anchor="w",
                fg_color="transparent"
            )
            user_label._label.configure(cursor="hand2")
            user_label._canvas.configure(cursor="hand2")
            user_label.pack(fill="both", expand=True)
            for widget in (user_frame, user_label):
                widget.bind("<Enter>", lambda e: self.s4_dropdown_enter(e))
                widget.bind("<Leave>", self.s4_dropdown_leave)
                widget.bind("<Button-1>", lambda e, u=user: self.s4_dropdown_select(e, u))

    def s4_destroy_selectable_users(self):
        for item in self.s4_user_list_frame.winfo_children(): # type: ignore[attr-defined]
            item.destroy()

    def s4_update_selectable_users(self):
        self.s4_destroy_selectable_users()
        self.s4_create_selectable_users()

    def s4_selected_user_enter(self, _event):
        """Handle hover enter on user label"""
        self.s4_selected_user_label.configure(fg_color="gray20") # type: ignore[attr-defined]

    def s4_selected_user_leave(self, _event):
        """Handle hover leave on user label"""
        if not self.s4_user_list_frame.winfo_ismapped(): # type: ignore[attr-defined]
            self.s4_selected_user_label.configure(fg_color="transparent") # type: ignore[attr-defined]

    def s4_show_dropdown(self, _event):
        """Show dropdown"""
        self.s4_user_list_frame.place(x=6, y=52, anchor="nw") # type: ignore[attr-defined] 52=6+38+8
        self.bind("<Button-1>", self.s4_hide_dropdown)

    def s4_hide_dropdown(self, event=None):
        """Hide dropdown"""
        self.s4_user_list_frame.place_forget() # type: ignore[attr-defined]
        self.unbind("<Button-1>")
        if event and self.winfo_containing(event.x_root, event.y_root).master != self.s4_selected_user_label: # type: ignore[attr-defined]
            self.s4_selected_user_label.configure(fg_color="transparent") # type: ignore[attr-defined]

    @staticmethod
    def s4_dropdown_enter(event):
        """Handle hover on dropdown item"""
        event.widget.master.configure(fg_color="#2C2C2C")

    @staticmethod
    def s4_dropdown_leave(event):
        """Handle stop hover on dropdown item"""
        event.widget.master.configure(fg_color="transparent")

    def s4_dropdown_select(self, _event, user):
        """Handle selection of a user from dropdown"""
        self.s4_selected_user_var.set(user) # type: ignore[attr-defined]
        self.s4_hide_dropdown()
        self.s4_update_input_ui()

    @staticmethod
    def s4_fetch_goals(year, week):
        return status_meeting_collection.find_one({"_id": "Goals"},
            projection={f"{year}.{week}": 1}
        ).get(year, {}).get(week, {})

    def s4_update_input_ui(self, reset_focus=True):
        """Updates the inputs goals ui for selected user"""
        user = self.s4_selected_user_var.get() # type: ignore[attr-defined]
        current_goals = self._current_week_goals.get(user, {})
        next_goals = self._next_week_goals.get(user, {})
        # Update days buttons
        if next_goals.get("days"):
            self.selected_days = next_goals["days"]
            self.s4_update_days_ui(self.selected_days)
        elif current_goals.get("days"):
            self.selected_days = 0
            self.s4_update_days_ui(current_goals["days"], placeholder=True)
        else:
            self.selected_days = 0
            self.s4_update_days_ui(0)
        # Update hours entry
        self.hours_entry.delete(0, "end") # type: ignore[attr-defined]
        if next_goals.get("hours"):
            self.hours_entry.insert(0, str(next_goals["hours"])) # type: ignore[attr-defined]
        if current_goals.get("hours"):
            self.hours_entry.configure(placeholder_text=str(current_goals["hours"])) # type: ignore[attr-defined]
        else:
            self.hours_entry.configure(placeholder_text="int.MaxValue") # type: ignore[attr-defined]
        if reset_focus:
            self.focus_set()  # Remove focus from hours entry
        self._presence_update_event.set()  # Update presence in DB immediately

    def s4_update_days_ui(self, days, placeholder=False):
        """Update the days buttons UI based on selected days"""
        active_color = "#2ecc71" if not placeholder else "#1A733F"
        inactive_color = "#e74c3c"
        for i, btn in enumerate(self.days_buttons): # type: ignore[attr-defined]
            if i < days:
                btn.configure(fg_color=active_color)
            else:
                btn.configure(fg_color=inactive_color)

    def s4_show_input(self):
        """Show the goals input frame"""
        self.s4_in_input = True
        self._next_week_goals = self.s4_fetch_goals(self.next_year, self.next_week)
        self.s4_update_input_ui()
        self.goals_input_screen.lift() # type: ignore[attr-defined]

    def s4_show_display(self):
        """Show the goals view frame"""
        self.s4_in_input = False
        self.s4_update_display_ui()
        self.goals_view_screen.lift() # type: ignore[attr-defined]
        self._presence_update_event.set()

    def s4_update_display_ui(self):
        """Update the goals display with all user goals (only if changed)"""
        # 1. Build all users (online & users with goals)
        all_users = sorted(self.online_users | self._next_week_goals.keys())

        # 2. Build Data List
        display_state = []
        for user in all_users:
            if user in self.input_mode_users:
                display_state.append((user, 'pending', 'pending'))
            else:
                if user in self._next_week_goals:
                    user_goals = self._next_week_goals[user]
                    days_val = user_goals.get("days", 0)
                    hours_val = user_goals.get("hours", 0)
                    display_state.append((user, days_val, hours_val))
                else:
                    display_state.append((user, 0, 0))
        # 3. Return if no change
        if hasattr(self, '_last_goals_display_state') and self._last_goals_display_state == display_state: # type: ignore[attr-defined]
            return
        self._last_goals_display_state = display_state

        # 4. Remove users no longer in data (Widgets & Dict)
        current_users_in_display = {item[0] for item in display_state}
        users_to_remove = [
            user for user in self._goals_display_widgets.keys() # type: ignore[attr-defined]
            if user not in current_users_in_display
        ]
        for user in users_to_remove:
            self._goals_display_widgets[user]['frame'].destroy()  # type: ignore[attr-defined]
            del self._goals_display_widgets[user]  # type: ignore[attr-defined]

        # 5. Update or create widgets
        for user, days, hours in display_state:
            if user not in self._goals_display_widgets:  # type: ignore[attr-defined] Create new widgets
                frame_1 = ctk.CTkFrame(
                    self.goals_display_frame,  # type: ignore[attr-defined]
                    fg_color="#23272B",
                    corner_radius=8,
                    bg_color="#333333"
                )
                frame_1.pack(fill="x", pady=2, padx=5)
                frame_2 = ctk.CTkFrame(frame_1, fg_color="transparent")
                frame_2.pack(fill="x", padx=10, pady=5)
                for i in range(7):
                    if i in (1, 3, 5):
                        frame_2.grid_columnconfigure(i, weight=0, uniform="content_col", minsize=150)
                    else:
                        frame_2.grid_columnconfigure(i, weight=1, uniform="pad_colum")
                user_label = ctk.CTkLabel(frame_2, text=user, font=("Arial", 20), text_color="white",
                                            anchor="center")
                user_label.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
                days_label = ctk.CTkLabel(frame_2, text="", font=("Arial", 18), text_color="white", anchor="center")
                days_label.grid(row=0, column=3, sticky="nsew", padx=(0, 10))
                hours_label = ctk.CTkLabel(frame_2, text="", font=("Arial", 18), text_color="white", anchor="center")
                hours_label.grid(row=0, column=5, sticky="nsew")
                self._goals_display_widgets[user] = {  # type: ignore[attr-defined]
                    'frame': frame_1,
                    'user_label': user_label,
                    'days_label': days_label,
                    'hours_label': hours_label
                }
            widgets = self._goals_display_widgets[user] # type: ignore[attr-defined] Update the text
            if days == 'pending':
                widgets['days_label'].configure(text="⏳")
            else:
                widgets['days_label'].configure(text=f"{days or '-'}")

            if hours == 'pending':
                widgets['hours_label'].configure(text="⏳")
            else:
                widgets['hours_label'].configure(text=f"{hours or '-'}")

        # 6. Re-pack frames
        for user_widgets in self._goals_display_widgets.values(): # type: ignore[attr-defined]
            user_widgets['frame'].pack_forget()
        for user, _, _ in display_state:
            if user in self._goals_display_widgets: # type: ignore[attr-defined]
                self._goals_display_widgets[user]['frame'].pack(fill="x", pady=2, padx=5) # type: ignore[attr-defined]

        # 7. Team row
        if hasattr(self, '_team_row_widget') and self._team_row_widget is not None: # type: ignore[attr-defined]
            self._team_row_widget.destroy()
        team_hours = sum(int(hours) for _, _, hours in display_state if hours != 'pending')
        team_hours = str(team_hours) if team_hours else "-"
        frame_1 = ctk.CTkFrame(
            self.goals_display_frame, # type: ignore[attr-defined]
            fg_color="#23272B",
            corner_radius=8,
            bg_color="#333333"
        )
        frame_1.pack(fill="x", pady=2, padx=5)
        frame_2 = ctk.CTkFrame(frame_1, fg_color="transparent")
        frame_2.pack(fill="x", padx=10, pady=5)
        for i in range(7):
            if i in (1, 3, 5):
                frame_2.grid_columnconfigure(i, weight=0, uniform="content_col", minsize=150)
            else:
                frame_2.grid_columnconfigure(i, weight=1, uniform="pad_colum")
        user_label = ctk.CTkLabel(frame_2, text="Team", font=("Arial", 20, "bold"), text_color="white",
                                    anchor="center")
        user_label.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        days_label = ctk.CTkLabel(frame_2, text="", font=("Arial", 18), text_color="white", anchor="center")
        days_label.grid(row=0, column=3, sticky="nsew", padx=(0, 10))
        hours_label = ctk.CTkLabel(frame_2, text=team_hours, font=("Arial", 18, "bold"), text_color="white",
                                   anchor="center")
        hours_label.grid(row=0, column=5, sticky="nsew")
        self._team_row_widget = frame_1

    def s4_update_hourly_goal(self, show_summary=False):
        """Update only the hours goal for the current user. Optionally move to summary view if valid."""
        hours = self.hours_entry.get().strip() # type: ignore[attr-defined]
        # Convert to int & validate
        try:
            hours = int(hours) if hours else 0
            if not (0 <= hours <= 168):  # 0 to 24/7 is valid
                raise ValueError
        except ValueError:
            if show_summary:
                flash_error(self.hours_entry) # type: ignore[attr-defined]
            return

        self._s4_update_goal_data("hours", hours)

        # UI
        self.s4_update_display_ui()
        if show_summary:
            self.focus_set()
            self.s4_show_display()

    def s4_update_days_goal(self, day_input):
        """Update only the days goal for the current user."""
        self.selected_days = 0 if day_input == self.selected_days else day_input

        self._s4_update_goal_data("days", self.selected_days)

        # UI
        self.s4_update_days_ui(self.selected_days)
        self.s4_update_display_ui()

    def _s4_update_goal_data(self, field, value):
        user = self.s4_selected_user_var.get() # type: ignore[attr-defined]
        goals = self._next_week_goals.setdefault(user, {})
        if value > 0:
            goals[field] = value
        else:
            goals.pop(field, None)
            if not goals:
                del self._next_week_goals[user]

        with self._lock:
            if not goals:
                self.update_buffer["goals"][user] = {}
            else:
                self.update_buffer["goals"][user][field] = value

        self._update_event.set()

    ### SLIDE 5 ###
    def slide_5(self):
        """Bye"""
        self._s5_get_data_event = threading.Event()
        threading.Thread(target=self._s5_generate_with_lock, daemon=True, name="slide_5_generator").start()

    def _s5_generate_with_lock(self):
        string, style = self._s5_get_data()
        self.after(0, self._s5_create_end_label, string, style)

    # noinspection PyUnboundLocalVariable
    def _s5_get_data(self):
        data_path = f"data.{self.current_year}.{self.current_week}"
        # (Already have data) or (no valid lock)   [(no lock) or (stale lock)]
        filter_query = {
            "_id": "End Strings",
            "$or": [
                {data_path: {"$exists": True}},
                {"lock_timestamp": None},
                {"$expr": {"$lt": ["$lock_timestamp", {"$subtract": ["$$NOW", 20000]}]}}]}

        # If no data: lock, else pass
        update_pipeline = [{
            "$set": {
                "lock_timestamp": {
                    "$cond": {
                        "if": {"$eq":[{"$type": f"${data_path}"}, "missing"]},
                        "then": "$$NOW",
                        "else": "$lock_timestamp"}}}}]

        projection = {data_path: 1, "_id": 0}

        pymongo_network_errors = (ConnectionFailure, ServerSelectionTimeoutError, NetworkTimeout, AutoReconnect)
        openai_errors = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
        retry_errors = pymongo_network_errors + openai_errors # type: ignore

        while True:
            try:
                doc = status_meeting_collection.find_one_and_update(
                    filter_query, update_pipeline, projection=projection, return_document=ReturnDocument.AFTER
                )
                # Locked document
                if doc is None:
                    self._s5_get_data_event.clear()
                    self._s5_get_data_event.wait(1.0)
                    continue

                # Data already exists
                week_data = doc.get("data", {}).get(self.current_year, {}).get(self.current_week, {})
                if week_data:
                    return week_data["string"], week_data["style"]

                # We got the lock
                try:
                    string, style = self._s5_generate_phrase()

                    pipeline = [{
                        "$set": {
                            data_path: {
                                "$cond": {
                                    "if": {"$eq": [{"$type": f"${data_path}"}, "missing"]},
                                    "then": {"string": string, "style": style},
                                    "else": f"${data_path}"}},
                            "lock_timestamp": None}}]

                    updated_doc = status_meeting_collection.find_one_and_update({"_id": "End Strings"},
                        pipeline, projection=projection, return_document=ReturnDocument.AFTER
                    )
                    week_data = updated_doc["data"][self.current_year][self.current_week]
                    return week_data["string"], week_data["style"]
                except Exception as e:
                    if not isinstance(e, pymongo_network_errors):
                        try:
                            status_meeting_collection.update_one({"_id": "End Strings"},{"$set": {"lock_timestamp": None}})
                        except pymongo_network_errors:
                            pass
                    raise
            except retry_errors:
                self._s5_get_data_event.clear()
                self._s5_get_data_event.wait(1.0)
                continue

    @staticmethod
    def _s5_generate_phrase():
        """
        Generate phrase using OpenAI API.
        Returns generated phrase string and style.
        """
        style_list = [
            "Excessively corporate formal.",
            "Anime-girl cutesy chaos.",
            "Iconic Overwatch voice lines or common callouts.",
            "Kids' playground slang.",
            "Pirate talk.",
            "Film noir detective voice.",
            "Shakespearean English.",
            "Post-apocalyptic survivor tone.",
            "Yoda-speak.",
            "2000s internet forum mod.",
            "Valley girl texting her BFF.",
            "Medieval herald.",
            "Haiku format.",
            "Breaking news anchor.",
        ]
        style = random.choice(style_list)
        prompt = f"""You are generating the final slide message for a weekly status meeting at a small indie game studio.

Write a **very short and funny phrase**. It should either:
- Be exclamatory about the past meeting,
- Be an imperative telling what to do next,
- Or be both.

Tone/style: {style}

Strict rules:
- Max 10 words.
- Do NOT mention coffee or caffeine.
- Be punchy. No filler. No greetings. No sign-offs.
- Return ONLY the phrase. No explanations, extra text or having quotation marks around the phrase."""

        ai_client = OpenAI(
            base_url="https://models.github.ai/inference",
            api_key="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",
        )
        response = ai_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],  # type: ignore[arg-type]
            model="openai/gpt-4o", temperature=0.9, max_tokens=60, top_p=1, timeout=15.0
        )
        phrase = response.choices[0].message.content
        return phrase, style

    def _s5_create_end_label(self, text, style):
        """Create or update the end slide label"""
        self.end_slide_label = ctk.CTkLabel(
            self.slide_frames[5],
            text=text,
            font=("Arial", 80, "bold"),
            text_color="white",
            wraplength=1600
        )
        self.end_slide_label.place(relx=0.5, rely=0.5, anchor="center")
        CTkFlexToolTip(self.end_slide_label, message=style, delay=0.2, bg_color="#696969", corner_radius=5,
                       static_anchor="s", padding=(5, 4), alpha=1, x_offset=0, y_offset=5, border_width=1,
                       border_color="black", text_color="black")

if __name__ == "__main__":
    app = MeetingApp()
    app.mainloop()
