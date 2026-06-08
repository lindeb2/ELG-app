import customtkinter as ctk
import time
from datetime import datetime
from log_commit import commit_log
from timetable_db import aggregations, client, collection, user
from utils import flash_error
import requests

# Google App Engine URL for notifications
GAE_URL = "https://your-app-engine-url.appspot.com/notify"  # Replace with your actual GAE URL

class TimetableApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        self.title("Timetable")
        self.geometry("200x170")
        self.local_start = None
        self.elapsed_time = self._monotonic_anchor = 0.0
        self.running = False

        self.name_var = ctk.StringVar()
        self.desc_var = ctk.StringVar()

        self.name_var.trace_add("write", self.validate_inputs)
        self.desc_var.trace_add("write", self.validate_inputs)

        self.grid_rowconfigure([0, 1], weight=1, uniform='a')
        self.grid_columnconfigure([0, 1], weight=1, uniform='a')

        self.time_label = ctk.CTkLabel(self, text="00:00", font=("Arial", 32), text_color="white")
        self.time_label.grid(row=1, column=0, sticky='nsew', columnspan=2)

        self.toggle_run_button = ctk.CTkButton(self, text="Start", fg_color="#000000", hover_color="#121212", text_color="white", font=("Arial", 24), command=self.toggle_button)
        self.toggle_run_button.grid(row=0, column=0, sticky='nsew', padx=4, pady=4, columnspan=2)

        self.done_button = ctk.CTkButton(self, text="Done", fg_color="#000000", hover_color="#121212", text_color="white", font=("Arial", 14), command=self.show_entry_overlay)

        self.overlay_canvas = ctk.CTkFrame(self, corner_radius=0, fg_color="#2C2C2C")
        self.overlay_canvas.grid_rowconfigure([0, 1, 2], weight=1, uniform='a')
        self.overlay_canvas.grid_rowconfigure(2, weight=2)
        self.overlay_canvas.grid_columnconfigure([0, 1], weight=1, uniform='a')

        ctk.CTkEntry(self.overlay_canvas, placeholder_text="Name", textvariable=self.name_var # Name entry
        ).grid(row=0, column=0, padx=4, pady=4, sticky='nsew', columnspan=2)

        ctk.CTkEntry(self.overlay_canvas, placeholder_text="Description", textvariable=self.desc_var # Description entry
        ).grid(row=1, column=0, padx=4, pady=4, sticky='nsew', columnspan=2)

        ctk.CTkButton(self.overlay_canvas, text="Continue", fg_color="#000000", hover_color="#121212",
                      command=self.continue_timer, text_color="white", font=("Arial", 14)
        ).grid(row=2, column=0, padx=4, pady=4, sticky='nsew')

        self.log_button = ctk.CTkButton(self.overlay_canvas, text="Log Entry", fg_color="#000000", hover_color="#121212",
                                   command=self.submit_entry, text_color="white", font=("Arial", 14), state="disabled")
        self.log_button.grid(row=2, column=1, padx=4, pady=4, sticky='nsew')

    def format_time(self, seconds):
        """Format time from seconds to a readable format."""
        total = int(float(seconds or 0))
        if total <= 0:
            return "00:00"
        hours = total // 3600
        minutes = (total % 3600) // 60
        seconds = total % 60
        if hours >= 24:
            return f"{hours} hours"
        elif hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"

    def send_notification(self, message):
        """
        Send a notification to the Google App Engine server.

        Args:
            message (str): The message to send
        """
        # Print the message to console for testing
        print("\n=== Notification Preview ===")
        print(message)
        print("===========================\n")

        try:
            response = requests.post(GAE_URL, json={"message": message})
            response.raise_for_status()  # Raise an exception for bad status codes
        except requests.exceptions.RequestException as e:
            print(f"Failed to send notification: {e}")

    def toggle_button(self):
        if not self.running:
            if self.local_start is None:
                self.local_start = time.perf_counter()
            self._monotonic_anchor = time.perf_counter()
            self.running = True
            self.update_timer()
            self.hide_done_button("Pause")
        else:
            self.elapsed_time += time.perf_counter() - self._monotonic_anchor
            self.running = False
            self.toggle_run_button.configure(text="Continue", font=("Arial", 14))
            self.time_label.configure(text=self.format_time(self.elapsed_time))
            self.done_button.grid(row=0, column=1, sticky='nsew', padx=4, pady=4)
            self.toggle_run_button.grid_configure(columnspan=1)

    def hide_done_button(self, text):
        self.toggle_run_button.configure(text=text, font=("Arial", 24))
        self.toggle_run_button.grid_configure(columnspan=2)
        self.done_button.grid_forget()

    def update_timer(self):
        if self.running:
            now = time.perf_counter()
            self.elapsed_time += now - self._monotonic_anchor
            self._monotonic_anchor = now
            self.time_label.configure(text=self.format_time(self.elapsed_time))
            time_until_next_second = 1.0 - (self.elapsed_time % 1.0)
            delay = int(time_until_next_second * 1000)
            self.after(delay, self.update_timer)

    def submit_entry(self):
        ms_since_local_start = int((time.perf_counter() - self.local_start) * 1000)
        try:
            timestamp, broken_records = commit_log(
                collection,
                aggregations,
                client,
                name=self.name_var.get().strip(),
                user=user,
                description=self.desc_var.get().strip(),
                elapsed_time=int(self.elapsed_time),
                ms_since_local_start=ms_since_local_start,
            )

            if broken_records:
                global_records = [r for r in broken_records if r["old_record"]["scope"] == "global"]
                personal_records = [r for r in broken_records if r["old_record"]["scope"] == "personal"]
                combined_records = [r for r in broken_records if r["old_record"]["scope"] == "combined"]
                message = self.create_broken_records_notification(
                    user, global_records, personal_records, combined_records
                )
                self.send_notification(message)

            self.local_start = None
            self.elapsed_time = 0.0
            self.time_label.configure(text="00:00")
            self.hide_done_button("Start")
            self.name_var.set("")
            self.desc_var.set("")

            self.overlay_canvas.grid_forget()

        except Exception as e:
            flash_error(self.log_button)

    def continue_timer(self):
        self.toggle_button()
        self.overlay_canvas.grid_forget()

    def validate_inputs(self, *args):
        if self.name_var.get().strip() and self.desc_var.get().strip():
            self.log_button.configure(state="normal")
        else:
            self.log_button.configure(state="disabled")

    def show_entry_overlay(self):
        self.overlay_canvas.grid(row=0, column=0, sticky="nsew", columnspan=2, rowspan=2)

    def days_since_record(self, old_date_str):
        """
        Calculate the number of days between the old record date and now.

        Args:
            old_date_str (str): Date string in format "YYYY-MM-DD HH:MM:SS" or None

        Returns:
            int: Number of days since the old record, or 0 if no previous record
        """
        if old_date_str is None:
            return 0

        old_date = datetime.strptime(old_date_str, "%Y-%m-%d %H:%M:%S")
        current_date = datetime.now()
        delta = current_date - old_date
        return delta.days

    def format_record_message(self, record_pair, days):
        """
        Format a message for a broken record.

        Args:
            record_pair (dict): Dictionary containing old_record and new_record
            days (int): Number of days since the old record

        Returns:
            str: Formatted message string
        """
        old_record = record_pair["old_record"]
        new_record = record_pair["new_record"]

        # Format the time period
        time_period = old_record['time_type'].lower()

        # Format the scope and record holder
        if old_record["scope"] == "global":
            record_holder = f"{old_record.get('user', 'The')}'s" if old_record.get('user') else "The"
            record_type = "world record"
        elif old_record["scope"] == "personal":
            record_holder = "The"
            record_type = "PB"
        else:  # combined
            record_holder = "The"
            record_type = "team record"

        if old_record["metric"] == "total_time":
            old_time = self.format_time(old_record['value']['total_time'])
            new_time = self.format_time(new_record['value']['total_time'])
            return f"{record_holder} {days} days old {time_period} time {record_type}: {old_time} → {new_time}\n"
        if old_record["metric"] in ("consecutive_days", "consecutive_weeks"):
            unit = "days" if old_record["metric"] == "consecutive_days" else "weeks"
            old_streak = old_record["value"]["streak"]
            new_streak = new_record["value"]["streak"]
            return (
                f"{record_holder} {days} days old lifetime consecutive {unit} {record_type}: "
                f"{old_streak} → {new_streak}\n"
            )

        old_ratio = f"{old_record['value']['active_days']}/{old_record['value']['total_days']} ({old_record['value']['percentage']:.1%})"
        new_ratio = f"{new_record['value']['active_days']}/{new_record['value']['total_days']} ({new_record['value']['percentage']:.1%})"
        return f"{record_holder} {days} days old {time_period} activity {record_type}: {old_ratio} → {new_ratio}\n"

    def create_broken_records_notification(self, user, global_records, personal_records, combined_records):
        """
        Creates a formatted message string for broken records notification.

        Args:
            user (str): Name of the user who broke the records
            global_records (list): List of global records broken
            personal_records (list): List of PBs broken
            combined_records (list): List of combined records broken

        Returns:
            str: Formatted message string
        """
        # Filter out PBs that are duplicates of world records
        filtered_personal_records = []
        for personal_record in personal_records:
            is_duplicate = False
            for global_record in global_records:
                # Check if it's the same type of record (time period, metric)
                if (personal_record["old_record"]["time_type"] == global_record["old_record"]["time_type"] and
                        personal_record["old_record"]["metric"] == global_record["old_record"]["metric"]):
                    is_duplicate = True
                    break
            if not is_duplicate:
                filtered_personal_records.append(personal_record)

        # Create list of record counts with their types
        record_counts = []
        if len(global_records) > 0:
            record_counts.append(f"{len(global_records)} world {'record' if len(global_records) == 1 else 'records'}")
        if len(filtered_personal_records) > 0:
            record_counts.append(f"{len(filtered_personal_records)} PB{'s' if len(filtered_personal_records) > 1 else ''}")
        if len(combined_records) > 0:
            record_counts.append(f"{len(combined_records)} team {'record' if len(combined_records) == 1 else 'records'}")

        # Create the header message based on the number of record types
        if len(record_counts) == 1:
            message = f"{user} just broke {record_counts[0]}!\n\n"
        elif len(record_counts) == 2:
            message = f"{user} just broke {record_counts[0]} and {record_counts[1]}!\n\n"
        else:
            message = f"{user} just broke {record_counts[0]}, {record_counts[1]} and {record_counts[2]}!\n\n"

        # Add details for all records
        all_records = global_records + filtered_personal_records + combined_records
        for record_pair in all_records:
            days = self.days_since_record(record_pair["old_record"]["date"])
            message += self.format_record_message(record_pair, days)

        return message


if __name__ == "__main__":
    app = TimetableApp()
    app.mainloop()