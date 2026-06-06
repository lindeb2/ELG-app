from selectors import SelectSelector

from bson import ObjectId
import customtkinter as ctk
import time
from datetime import datetime, timedelta
from pymongo import ReturnDocument
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import os
import json
import requests

# MongoDB connection
client = MongoClient(
    "mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/?retryWrites=true&w=majority&appName=ELG-timetable",
    server_api=ServerApi('1')
)
db = client['ELG-Dev']
collection = db['Timetable']
aggregations = db['Timetable Aggregations']

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

# Get the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))
# Go up one level to the project root
project_root = os.path.dirname(script_dir)
# Construct the path to config.json
config_path = os.path.join(project_root, "config.json")

with open(config_path) as file:
    config = json.load(file)
    user = config["user"]

_ACTIVE_DAY_EXPR = {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}

def calendar_week_key(dt: datetime) -> tuple[str, str]:
    year, week, _ = dt.isocalendar()
    return str(year), str(week)


def calendar_bounds(
    period: str,
    *,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
    week: int | None = None,
) -> tuple[datetime, datetime]:
    """Inclusive start, exclusive end for a calendar day/week/month/year."""
    if period == "day":
        start = datetime(year, month, day).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

    if period == "week":
        start = datetime.fromisocalendar(year, week, 1)
        return start, start + timedelta(weeks=1)

    if period == "month":
        start = datetime(year, month, 1)
        end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        return start, end

    if period == "year":
        return datetime(year, 1, 1), datetime(year + 1, 1, 1)


def timestamp_range_match(start: datetime, end: datetime) -> dict:
    return {"timestamp": {"$gte": start, "$lt": end}}


def format_date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


_TIME_TYPE_TO_PERIOD = {"Year": "year", "Month": "month", "Week": "week", "Day": "day"}


def _period_timestamp_match(date_str: str, time_type: str) -> dict:
    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    period = _TIME_TYPE_TO_PERIOD[time_type]
    if period == "year":
        return timestamp_range_match(*calendar_bounds("year", year=dt.year))
    if period == "month":
        return timestamp_range_match(*calendar_bounds("month", year=dt.year, month=dt.month))
    if period == "week":
        iso_year, iso_week, _ = dt.isocalendar()
        return timestamp_range_match(*calendar_bounds("week", year=iso_year, week=iso_week))
    return timestamp_range_match(*calendar_bounds("day", year=dt.year, month=dt.month, day=dt.day))


_TIME_BY_USER_GROUP = {"$group": {"_id": "$user", "time": {"$sum": "$elapsed_time"}}}
_TIME_AND_DAYS_BY_USER_GROUP = {
    "$group": {
        "_id": "$user",
        "time": {"$sum": "$elapsed_time"},
        "active_days": {"$addToSet": _ACTIVE_DAY_EXPR},
    }
}


def _sum_time_by_user(start: datetime, end: datetime) -> list:
    pipeline = [{"$match": timestamp_range_match(start, end)}, _TIME_BY_USER_GROUP]
    return list(collection.aggregate(pipeline))


def _sum_time_and_days_by_user(start: datetime, end: datetime) -> list:
    pipeline = [
        {"$match": timestamp_range_match(start, end)},
        _TIME_AND_DAYS_BY_USER_GROUP,
    ]
    return list(collection.aggregate(pipeline))


def _upsert_aggregation(doc_id: str, fields: dict) -> None:
    aggregations.update_one({"_id": doc_id}, {"$set": fields}, upsert=True)


def _write_time_field(result: list, user: str, base_path: str) -> None:
    combined_time = sum(entry["time"] for entry in result)
    user_time = next((entry["time"] for entry in result if entry["_id"] == user), 0)
    _upsert_aggregation("Combined", {f"{base_path}.time": combined_time,})
    _upsert_aggregation(user, {f"{base_path}.time": user_time})


def _write_activity_fields(result: list, user: str, base_path: str, total_days: int) -> None:
    combined_time = sum(entry["time"] for entry in result)
    combined_active_days = len({day for entry in result for day in entry["active_days"]})
    combined_ratio = combined_active_days / total_days

    _upsert_aggregation("Combined", {
        f"{base_path}.time": combined_time,
        f"{base_path}.active_days": combined_active_days,
        f"{base_path}.total_days": total_days,
        f"{base_path}.activity_ratio": combined_ratio,
    })

    user_entry = next((entry for entry in result if entry["_id"] == user), None)
    if user_entry:
        user_active_days = len(user_entry["active_days"])
        _upsert_aggregation(user, {
            f"{base_path}.time": user_entry["time"],
            f"{base_path}.active_days": user_active_days,
            f"{base_path}.total_days": total_days,
            f"{base_path}.activity_ratio": user_active_days / total_days,
        })

def aggregate(
    period: str,
    user: str,
    *,
    year: str,
    month: str | None = None,
    day: str | None = None,
    week_year: str | None = None,
    week: str | None = None,
    weekday: str | None = None
) -> None:

    y = int(year) if year is not None else None
    m = int(month) if month is not None else None
    d = int(day) if day is not None else None
    wy = int(week_year) if week_year is not None else None
    w = int(week) if week is not None else None
    wd = int(weekday) if weekday is not None else None

    if period == "year":
        start, end = calendar_bounds("year", year=y)
        total_days = total_days_in_period(y)
        path_base = f"years.{year}"
    elif period == "month":
        path_base = f"years.{year}.months.{month}"
        start, end = calendar_bounds("month", year=y, month=m)
        total_days = total_days_in_period(y, m)
    elif period == "week":
        path_base = f"years.{week_year}.weeks.{week}"
        start, end = calendar_bounds("week", year=wy, week=w)
        total_days = total_days_in_period()
    else:
        start, end = calendar_bounds("day", year=y, month=m, day=d)
        total_days = None
        if period == "day":
            path_base = f"years.{year}.months.{month}.days.{day}"
        elif period == "weekday":
            path_base = f"years.{week_year}.weeks.{week}.weekdays.{weekday}"


    if total_days is not None:
        result = _sum_time_and_days_by_user(start, end)
        _write_activity_fields(result, user, path_base, total_days)
        return
    result = _sum_time_by_user(start, end)
    _write_time_field(result, user, path_base)

# Toggle button function
def toggle_button():
    global local_start, running, elapsed_time, _monotonic_anchor
    if not running:
        if local_start is None:
            local_start = time.time()
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

def update_highscore(user, time_type, time_value, date_str, is_global=False, activity_data=None):
    """
    Update highscores for a given time type and user.
    
    Args:
        user (str): Name of the user
        time_type (str): Type of time record (Year, Month, Week, Day)
        time_value (int): Time value in seconds
        date_str (str): Date string in format "YYYY-MM-DD HH:MM:SS"
        is_global (bool): Whether this is a global record
        activity_data (dict): Dictionary containing activity metrics (active_days, total_days, activity_ratio)
        
    Returns:
        list: List of dictionaries containing information about broken records
    """
    broken_records = []
    
    # Get or create highscores document
    highscores = aggregations.find_one({"_id": "Highscores"})
    if not highscores:
        # Initialize highscores structure
        highscores = {
            "_id": "Highscores",
            user: {
                "Year": {
                    "time": {"value": 0, "date": None},
                    "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None}
                },
                "Month": {
                    "time": {"value": 0, "date": None},
                    "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None}
                },
                "Week": {
                    "time": {"value": 0, "date": None},
                    "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None}
                },
                "Day": {
                    "time": {"value": 0, "date": None}
                }
            },
            "Global": {
                "Year": {
                    "time": {"value": 0, "date": None, "user": None},
                    "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None, "user": None}
                },
                "Month": {
                    "time": {"value": 0, "date": None, "user": None},
                    "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None, "user": None}
                },
                "Week": {
                    "time": {"value": 0, "date": None, "user": None},
                    "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None, "user": None}
                },
                "Day": {
                    "time": {"value": 0, "date": None, "user": None}
                }
            },
            "Combined": {
                "Year": {
                    "time": {"value": 0, "date": None},
                    "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None}
                },
                "Month": {
                    "time": {"value": 0, "date": None},
                    "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None}
                },
                "Week": {
                    "time": {"value": 0, "date": None},
                    "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None}
                },
                "Day": {
                    "time": {"value": 0, "date": None}
                }
            }
        }
        aggregations.insert_one(highscores)
    # Ensure the user's structure exists
    if user not in highscores:
        highscores[user] = {
            "Year": {
                "time": {"value": 0, "date": None},
                "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None}
            },
            "Month": {
                "time": {"value": 0, "date": None},
                "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None}
            },
            "Week": {
                "time": {"value": 0, "date": None},
                "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None}
            },
            "Day": {
                "time": {"value": 0, "date": None}
            }
        }
    
    # Update time record
    if time_value > highscores[user][time_type]["time"]["value"]:
        old_record = {
            "scope": "personal",
            "time_type": time_type,
            "metric": "total_time",
            "value": {
                "total_time": highscores[user][time_type]["time"]["value"],
                "active_days": None,
                "total_days": None,
                "percentage": None
            },
            "date": highscores[user][time_type]["time"]["date"]
        }
        
        new_record = {
            "scope": "personal",
            "time_type": time_type,
            "metric": "total_time",
            "value": {
                "total_time": time_value,
                "active_days": None,
                "total_days": None,
                "percentage": None
            },
            "date": date_str
        }
        
        broken_records.append({
            "old_record": old_record,
            "new_record": new_record
        })
        
        highscores[user][time_type]["time"] = {
            "value": time_value,
            "date": date_str
        }
        
        if is_global and time_value > highscores["Global"][time_type]["time"]["value"]:
            old_record = {
                "scope": "global",
                "time_type": time_type,
                "metric": "total_time",
                "value": {
                    "total_time": highscores["Global"][time_type]["time"]["value"],
                    "active_days": None,
                    "total_days": None,
                    "percentage": None
                },
                "date": highscores["Global"][time_type]["time"]["date"],
                "user": highscores["Global"][time_type]["time"].get("user")
            }
            
            new_record = {
                "scope": "global",
                "time_type": time_type,
                "metric": "total_time",
                "value": {
                    "total_time": time_value,
                    "active_days": None,
                    "total_days": None,
                    "percentage": None
                },
                "date": date_str
            }
            
            broken_records.append({
                "old_record": old_record,
                "new_record": new_record
            })
            
            highscores["Global"][time_type]["time"] = {
                "value": time_value,
                "date": date_str,
                "user": user
            }
    
    # Update activity record if activity data is provided
    if activity_data and time_type != "Day" and activity_data["activity_ratio"] > highscores[user][time_type]["activity"]["value"]:
        old_record = {
            "scope": "personal",
            "time_type": time_type,
            "metric": "days_active",
            "value": {
                "total_time": None,
                "active_days": highscores[user][time_type]["activity"]["active_days"],
                "total_days": highscores[user][time_type]["activity"]["total_days"],
                "percentage": highscores[user][time_type]["activity"]["value"]
            },
            "date": highscores[user][time_type]["activity"]["date"]
        }
        
        new_record = {
            "scope": "personal",
            "time_type": time_type,
            "metric": "days_active",
            "value": {
                "total_time": None,
                "active_days": activity_data["active_days"],
                "total_days": activity_data["total_days"],
                "percentage": activity_data["activity_ratio"]
            },
            "date": date_str
        }
        
        broken_records.append({
            "old_record": old_record,
            "new_record": new_record
        })
        
        highscores[user][time_type]["activity"] = {
            "value": activity_data["activity_ratio"],
            "active_days": activity_data["active_days"],
            "total_days": activity_data["total_days"],
            "date": date_str
        }
        
        if is_global and activity_data["activity_ratio"] > highscores["Global"][time_type]["activity"]["value"]:
            old_record = {
                "scope": "global",
                "time_type": time_type,
                "metric": "days_active",
                "value": {
                    "total_time": None,
                    "active_days": highscores["Global"][time_type]["activity"]["active_days"],
                    "total_days": highscores["Global"][time_type]["activity"]["total_days"],
                    "percentage": highscores["Global"][time_type]["activity"]["value"]
                },
                "date": highscores["Global"][time_type]["activity"]["date"],
                "user": highscores["Global"][time_type]["activity"].get("user")
            }
            
            new_record = {
                "scope": "global",
                "time_type": time_type,
                "metric": "days_active",
                "value": {
                    "total_time": None,
                    "active_days": activity_data["active_days"],
                    "total_days": activity_data["total_days"],
                    "percentage": activity_data["activity_ratio"]
                },
                "date": date_str
            }
            
            broken_records.append({
                "old_record": old_record,
                "new_record": new_record
            })
            
            highscores["Global"][time_type]["activity"] = {
                "value": activity_data["activity_ratio"],
                "active_days": activity_data["active_days"],
                "total_days": activity_data["total_days"],
                "date": date_str,
                "user": user
            }
    
    # Update combined records
    if is_global:
        # Get all users' times and activity data for the current period
        pipeline = [
            {"$match": _period_timestamp_match(date_str, time_type)},
            {
                "$group": {
                    "_id": None,
                    "total_time": {"$sum": "$elapsed_time"},
                    "active_days": {"$addToSet": _ACTIVE_DAY_EXPR}
                }
            }
        ]
        combined_data = list(collection.aggregate(pipeline))
        
        if combined_data:
            # Update time record
            if combined_data[0]["total_time"] > highscores["Combined"][time_type]["time"]["value"]:
                old_record = {
                    "scope": "combined",
                    "time_type": time_type,
                    "metric": "total_time",
                    "value": {
                        "total_time": highscores["Combined"][time_type]["time"]["value"],
                        "active_days": None,
                        "total_days": None,
                        "percentage": None
                    },
                    "date": highscores["Combined"][time_type]["time"]["date"]
                }
                
                new_record = {
                    "scope": "combined",
                    "time_type": time_type,
                    "metric": "total_time",
                    "value": {
                        "total_time": combined_data[0]["total_time"],
                        "active_days": None,
                        "total_days": None,
                        "percentage": None
                    },
                    "date": date_str
                }
                
                broken_records.append({
                    "old_record": old_record,
                    "new_record": new_record
                })
                
                highscores["Combined"][time_type]["time"] = {
                    "value": combined_data[0]["total_time"],
                    "date": date_str
                }
            
            # Update activity record
            if time_type != "Day":  # Activity records don't apply to Day type
                combined_active_days = len(combined_data[0]["active_days"])
                total_days = total_days_in_period(date_str[:4]) if time_type == "Year" else \
                           total_days_in_period(date_str[:4], date_str[5:7]) if time_type == "Month" else \
                           total_days_in_period()
                combined_ratio = combined_active_days / total_days
                
                if combined_ratio > highscores["Combined"][time_type]["activity"]["value"]:
                    old_record = {
                        "scope": "combined",
                        "time_type": time_type,
                        "metric": "days_active",
                        "value": {
                            "total_time": None,
                            "active_days": highscores["Combined"][time_type]["activity"]["active_days"],
                            "total_days": highscores["Combined"][time_type]["activity"]["total_days"],
                            "percentage": highscores["Combined"][time_type]["activity"]["value"]
                        },
                        "date": highscores["Combined"][time_type]["activity"]["date"]
                    }
                    
                    new_record = {
                        "scope": "combined",
                        "time_type": time_type,
                        "metric": "days_active",
                        "value": {
                            "total_time": None,
                            "active_days": combined_active_days,
                            "total_days": total_days,
                            "percentage": combined_ratio
                        },
                        "date": date_str
                    }
                    
                    broken_records.append({
                        "old_record": old_record,
                        "new_record": new_record
                    })
                    
                    highscores["Combined"][time_type]["activity"] = {
                        "value": combined_ratio,
                        "active_days": combined_active_days,
                        "total_days": total_days,
                        "date": date_str
                    }
    
    # Save updated highscores
    aggregations.replace_one({"_id": "Highscores"}, highscores)
    
    return broken_records

def check_and_update_highscores(user, elapsed_time, dt):
    """
    Check and update all relevant highscores for a new entry
    """
    # Format date for storage
    date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    
    # Track all broken records
    all_broken_records = []
    
    year = dt.strftime("%Y")
    month = dt.strftime("%m")
    day = dt.strftime("%d")
    week_year, week = calendar_week_key(dt)

    year_pipeline = [
        {
            "$match": {
                **timestamp_range_match(*calendar_bounds("year", year=int(year))),
                "user": user,
            }
        },
        {
            "$group": {
                "_id": None,
                "total_time": {"$sum": "$elapsed_time"},
                "active_days": {"$addToSet": _ACTIVE_DAY_EXPR}
            }
        }
    ]
    year_data = list(collection.aggregate(year_pipeline))
    if year_data:
        year_time = year_data[0]["total_time"]
        year_active_days = len(year_data[0]["active_days"])
        year_total_days = total_days_in_period(year)
        year_activity_data = {
            "active_days": year_active_days,
            "total_days": year_total_days,
            "activity_ratio": year_active_days / year_total_days
        }
        broken_records = update_highscore(user, "Year", year_time, date_str, True, year_activity_data)
        all_broken_records.extend(broken_records)
    
    # Get total time and activity data for current month
    month_pipeline = [
        {
            "$match": {
                **timestamp_range_match(*calendar_bounds("month", year=int(year), month=int(month))),
                "user": user,
            }
        },
        {
            "$group": {
                "_id": None,
                "total_time": {"$sum": "$elapsed_time"},
                "active_days": {"$addToSet": _ACTIVE_DAY_EXPR}
            }
        }
    ]
    month_data = list(collection.aggregate(month_pipeline))
    if month_data:
        month_time = month_data[0]["total_time"]
        month_active_days = len(month_data[0]["active_days"])
        month_total_days = total_days_in_period(year, month)
        month_activity_data = {
            "active_days": month_active_days,
            "total_days": month_total_days,
            "activity_ratio": month_active_days / month_total_days
        }
        broken_records = update_highscore(user, "Month", month_time, date_str, True, month_activity_data)
        all_broken_records.extend(broken_records)
    
    # Get total time and activity data for current week
    week_pipeline = [
        {
            "$match": {
                **timestamp_range_match(*calendar_bounds("week", year=int(week_year), week=int(week))),
                "user": user,
            }
        },
        {
            "$group": {
                "_id": None,
                "total_time": {"$sum": "$elapsed_time"},
                "active_days": {"$addToSet": _ACTIVE_DAY_EXPR}
            }
        }
    ]
    week_data = list(collection.aggregate(week_pipeline))
    if week_data:
        week_time = week_data[0]["total_time"]
        week_active_days = len(week_data[0]["active_days"])
        week_total_days = total_days_in_period()
        week_activity_data = {
            "active_days": week_active_days,
            "total_days": week_total_days,
            "activity_ratio": week_active_days / week_total_days
        }
        broken_records = update_highscore(user, "Week", week_time, date_str, True, week_activity_data)
        all_broken_records.extend(broken_records)
    
    # Get total time for current day
    day_pipeline = [
        {
            "$match": {
                **timestamp_range_match(*calendar_bounds("day", year=dt.year, month=dt.month, day=dt.day)),
                "user": user,
            }
        },
        {
            "$group": {
                "_id": None,
                "total_time": {"$sum": "$elapsed_time"}
            }
        }
    ]
    day_time = list(collection.aggregate(day_pipeline))
    if day_time:
        broken_records = update_highscore(user, "Day", day_time[0]["total_time"], date_str, True)
        all_broken_records.extend(broken_records)
    
    # If any records were broken, send notification
    if all_broken_records:
        # Count different types of records
        global_records = [r for r in all_broken_records if r["old_record"]["scope"] == "global"]
        personal_records = [r for r in all_broken_records if r["old_record"]["scope"] == "personal"]
        combined_records = [r for r in all_broken_records if r["old_record"]["scope"] == "combined"]
        
        # Create and send notification message
        message = create_broken_records_notification(user, global_records, personal_records, combined_records)
        send_notification(message)

def log_entry():
    global name, description, local_start, elapsed_time, running

    ms_since_local_start = int((time.time() - local_start) * 1000)
    new_id = ObjectId()
    
    doc = collection.find_one_and_update(
        {"_id": new_id},
        [{
            "$set": {
                "name": name,
                "user": user,
                "description": description,
                "elapsed_time": int(elapsed_time),
                "timestamp": {"$subtract": ["$$NOW", ms_since_local_start]}
            }
        }],
        upsert=True,
        return_document=ReturnDocument.AFTER,
        projection={"timestamp": 1, "_id": 0}
    )
    
    timestamp = doc["timestamp"]

    year = timestamp.strftime("%Y")
    month = timestamp.strftime("%m")
    day = timestamp.strftime("%d")
    week_year, week = calendar_week_key(timestamp)
    weekday = timestamp.strftime("%u")

    aggregate("year", user, year=year)
    aggregate("month", user, year=year, month=month)
    aggregate("day", user, year=year, month=month, day=day)
    aggregate("week", user, week_year=week_year, week=week)
    aggregate("weekday", user, year=year, month=month, day=day, week_year=week_year, week=week, weekday=weekday)

    check_and_update_highscores(user, int(elapsed_time), timestamp)
    
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

def total_days_in_period(year: str | int | None = None, month: str | int | None = None, ) -> int:
    if year is not None and month is None:
        year = int(year)
        if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
            return 366
        return 365

    if year is not None and month is not None:
        year = int(year)
        month = int(month)
        if month == 2:
            if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
                return 29
            return 28
        if month in [1, 3, 5, 7, 8, 10, 12]:
            return 31
        return 30

    return 7

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
    else:  # days_active
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