import customtkinter as ctk
import time
from datetime import datetime
from log_commit import commit_log
from timetable_db import aggregations, client, collection, user
import requests

# Google App Engine URL for notifications
GAE_URL = "https://your-app-engine-url.appspot.com/notify"  # Replace with your actual GAE URL

def format_time(seconds):
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

def send_notification(message):
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

# Initialize the customtkinter theme with dark mode
ctk.set_appearance_mode("Dark")

# Global variables
name = description = local_start = None
elapsed_time = _monotonic_anchor = 0.0
running = False

# Toggle button function
def toggle_button():
    global local_start, running, elapsed_time, _monotonic_anchor
    if not running:
        if local_start is None:
            local_start = time.perf_counter()
        _monotonic_anchor = time.perf_counter()
        running = True
        button.configure(text="Pause", font=("Arial", 24))
        done_button.grid_forget()
        update_timer()
        button.grid_configure(columnspan=2)
    else:
        elapsed_time += time.perf_counter() - _monotonic_anchor
        running = False
        button.configure(text="Continue", font=("Arial", 14))
        label.configure(text=format_time(elapsed_time))
        done_button.grid(row=0, column=1, sticky='nsew', padx=4, pady=4)
        button.grid_configure(columnspan=1)

# Update timer function
def update_timer():
    global elapsed_time, _monotonic_anchor
    if running:
        now = time.perf_counter()
        elapsed_time += now - _monotonic_anchor
        _monotonic_anchor = now
        label.configure(text=format_time(elapsed_time))
        time_until_next_second = 1.0 - (elapsed_time % 1.0)
        delay = int(time_until_next_second * 1000)
        root.after(delay, update_timer)

def log_entry():
    global name, description, local_start, elapsed_time, running

    ms_since_local_start = int((time.perf_counter()() - local_start) * 1000)
    timestamp, broken_records = commit_log(
        collection,
        aggregations,
        client,
        name=name,
        user=user,
        description=description,
        elapsed_time=int(elapsed_time),
        ms_since_local_start=ms_since_local_start,
    )

    if broken_records:
        global_records = [r for r in broken_records if r["old_record"]["scope"] == "global"]
        personal_records = [r for r in broken_records if r["old_record"]["scope"] == "personal"]
        combined_records = [r for r in broken_records if r["old_record"]["scope"] == "combined"]
        message = create_broken_records_notification(
            user, global_records, personal_records, combined_records
        )
        send_notification(message)

    reset_state()

# Reset state and UI
def reset_state():
    global name, description, local_start, elapsed_time, running

    name = description = local_start = None
    elapsed_time = 0.0
    running = False

    label.configure(text="00:00")
    button.configure(text="Start", font=("Arial", 24))
    done_button.grid_forget()
    button.grid_configure(columnspan=2)

# Show entry overlay function
def show_entry_overlay():
    overlay_canvas = ctk.CTkFrame(root, corner_radius=0, fg_color="#2C2C2C")
    overlay_canvas.grid(row=0, column=0, sticky="nsew", columnspan=2, rowspan=2)

    overlay_canvas.grid_rowconfigure([0, 1], weight=1, uniform='c')
    overlay_canvas.grid_rowconfigure(2, weight=2, uniform='c')
    overlay_canvas.grid_columnconfigure([0, 1], weight=1, uniform='d')

    def continue_timer():
        overlay_canvas.grid_forget()
        toggle_button()

    def submit_entry():
        global name, description
        name = name_entry.get()
        description = description_entry.get()
        log_entry()
        overlay_canvas.grid_forget()

    name_entry = ctk.CTkEntry(overlay_canvas, placeholder_text="Name")
    name_entry.grid(row=0, column=0, padx=4, pady=4, sticky='nsew', columnspan=2)

    description_entry = ctk.CTkEntry(overlay_canvas, placeholder_text="Description")
    description_entry.grid(row=1, column=0, padx=4, pady=4, sticky='nsew', columnspan=2)

    ctk.CTkButton(overlay_canvas, text="Continue", fg_color="#000000", hover_color="#121212", 
                   command=continue_timer, text_color="white", font=("Arial", 14)).grid(row=2, column=0, padx=4, pady=4, sticky='nsew')

    ctk.CTkButton(overlay_canvas, text="Log Entry", fg_color="#000000", hover_color="#121212", 
                   command=submit_entry, text_color="white", font=("Arial", 14)).grid(row=2, column=1, padx=4, pady=4, sticky='nsew')

def days_since_record(old_date_str):
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

def format_record_message(record_pair, days):
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
        old_time = format_time(old_record['value']['total_time'])
        new_time = format_time(new_record['value']['total_time'])
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

def create_broken_records_notification(user, global_records, personal_records, combined_records):
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
        days = days_since_record(record_pair["old_record"]["date"])
        message += format_record_message(record_pair, days)
    
    return message

if __name__ == "__main__":
    root = ctk.CTk()
    root.title("Timetable")
    root.geometry("200x170")

    button = ctk.CTkButton(root, text="Start", fg_color="#000000", hover_color="#121212", 
                           text_color="white", font=("Arial", 24), command=toggle_button)
    button.grid(row=0, column=0, sticky='nsew', padx=4, pady=4, columnspan=2)

    label = ctk.CTkLabel(root, text="00:00", font=("Arial", 32), text_color="white")
    label.grid(row=1, column=0, sticky='nsew', columnspan=2)

    done_button = ctk.CTkButton(root, text="Done", fg_color="#000000", hover_color="#121212", 
                                text_color="white", font=("Arial", 14), command=show_entry_overlay)
    done_button.grid_forget() # remove?

    root.grid_rowconfigure([0, 1], weight=1, uniform='a')
    root.grid_columnconfigure([0, 1], weight=1, uniform='b')

    root.mainloop()