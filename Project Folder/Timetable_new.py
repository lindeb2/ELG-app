import customtkinter as ctk
import time
from datetime import datetime
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import os
import sys
import json
import requests

# MongoDB connection
client = MongoClient(
    "mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/?retryWrites=true&w=majority&appName=ELG-timetable",
    server_api=ServerApi('1')
)
db = client['ELG-Database']
collection = db['Timetable']
aggregations = db['Timetable Aggregations']

# Google App Engine URL for notifications
GAE_URL = "https://your-app-engine-url.appspot.com/notify"  # Replace with your actual GAE URL

def format_time(seconds):
    """Format time from seconds an exact readable format."""
    if seconds is None or seconds == 0:
        return "00:00"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    
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
name = description = start_time = end_time = None
elapsed_time = elapsed_seconds = elapsed_minutes = elapsed_hours = 0
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

def aggregate_day(year, month, day, user):
    pipeline = [
        {
            "$match": {
                "start_year": year,
                "start_month": month,
                "start_day": day
            }
        },
        {
            "$group": {
                "_id": "$user",
                "time": {"$sum": "$elapsed_time"}
            }
        }
    ]
    result = list(collection.aggregate(pipeline))
    
    # Calculate combined total
    combined_time = sum(entry["time"] for entry in result)
    
    # Update combined document
    aggregations.update_one(
        {
            "_id": "Combined"
        },
        {
            "$set": {
                f"years.{year}.months.{month}.days.{day}.time": combined_time
            }
        },
        upsert=True
    )
    
    # Update the specific user's document
    user_time = next((entry["time"] for entry in result if entry["_id"] == user), 0)
    aggregations.update_one(
        {
            "_id": user
        },
        {
            "$set": {
                f"years.{year}.months.{month}.days.{day}.time": user_time
            }
        },
        upsert=True
    )

def aggregate_weekday(year, month, day, weekday, user):
    pipeline = [
        {
            "$match": {
                "start_year": year,
                "start_month": month,
                "start_day": day,
                "start_weekday": weekday
            }
        },
        {
            "$group": {
                "_id": "$user",
                "time": {"$sum": "$elapsed_time"}
            }
        }
    ]
    result = list(collection.aggregate(pipeline))
    
    # Calculate combined total
    combined_time = sum(entry["time"] for entry in result)
    
    # Get the week number for this date
    dt = datetime(int(year), int(month), int(day))
    week = f"{int(dt.strftime('%W')) + 1:02d}"
    
    # Update combined document
    aggregations.update_one(
        {
            "_id": "Combined"
        },
        {
            "$set": {
                f"years.{year}.weeks.{week}.weekdays.{weekday}.time": combined_time
            }
        },
        upsert=True
    )
    
    # Update the specific user's document
    user_time = next((entry["time"] for entry in result if entry["_id"] == user), 0)
    aggregations.update_one(
        {
            "_id": user
        },
        {
            "$set": {
                f"years.{year}.weeks.{week}.weekdays.{weekday}.time": user_time
            }
        },
        upsert=True
    )

def aggregate_week(year, week, user):
    pipeline = [
        {
            "$match": {
                "start_year": year,
                "start_week": week
            }
        },
        {
            "$group": {
                "_id": "$user",
                "time": {"$sum": "$elapsed_time"},
                "active_days": {"$addToSet": {"$concat": ["$start_year", "-", "$start_month", "-", "$start_day"]}}
            }
        }
    ]
    result = list(collection.aggregate(pipeline))
    
    # Calculate combined total and active days
    combined_time = sum(entry["time"] for entry in result)
    combined_active_days = len(set(day for entry in result for day in entry["active_days"]))
    total_days = daysInWeek()
    
    # Calculate activity ratios
    combined_ratio = (combined_active_days / total_days)
    
    # Update combined document
    aggregations.update_one(
        {
            "_id": "Combined"
        },
        {
            "$set": {
                f"years.{year}.weeks.{week}.time": combined_time,
                f"years.{year}.weeks.{week}.active_days": combined_active_days,
                f"years.{year}.weeks.{week}.total_days": total_days,
                f"years.{year}.weeks.{week}.activity_ratio": combined_ratio
            }
        },
        upsert=True
    )
    
    # Update the specific user's document
    user_entry = next((entry for entry in result if entry["_id"] == user), None)
    if user_entry:
        user_active_days = len(user_entry["active_days"])
        user_ratio = (user_active_days / total_days)
        
        aggregations.update_one(
            {
                "_id": user
            },
            {
                "$set": {
                    f"years.{year}.weeks.{week}.time": user_entry["time"],
                    f"years.{year}.weeks.{week}.active_days": user_active_days,
                    f"years.{year}.weeks.{week}.total_days": total_days,
                    f"years.{year}.weeks.{week}.activity_ratio": user_ratio
                }
            },
            upsert=True
        )

    # Aggregate weekdays for this week
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for weekday in weekdays:
        weekday_pipeline = [
            {
                "$match": {
                    "start_year": year,
                    "start_week": week,
                    "start_weekday": weekday
                }
            },
            {
                "$group": {
                    "_id": "$user",
                    "time": {"$sum": "$elapsed_time"}
                }
            }
        ]
        weekday_result = list(collection.aggregate(weekday_pipeline))
        
        if weekday_result:
            # Calculate combined total for this weekday
            weekday_combined_time = sum(entry["time"] for entry in weekday_result)
            
            # Update combined document
            aggregations.update_one(
                {
                    "_id": "Combined"
                },
                {
                    "$set": {
                        f"years.{year}.weeks.{week}.weekdays.{weekday}.time": weekday_combined_time
                    }
                },
                upsert=True
            )
            
            # Update the specific user's document
            user_weekday_time = next((entry["time"] for entry in weekday_result if entry["_id"] == user), 0)
            aggregations.update_one(
                {
                    "_id": user
                },
                {
                    "$set": {
                        f"years.{year}.weeks.{week}.weekdays.{weekday}.time": user_weekday_time
                    }
                },
                upsert=True
            )

def aggregate_month(year, month, user):
    pipeline = [
        {
            "$match": {
                "start_year": year,
                "start_month": month
            }
        },
        {
            "$group": {
                "_id": "$user",
                "time": {"$sum": "$elapsed_time"},
                "active_days": {"$addToSet": {"$concat": ["$start_year", "-", "$start_month", "-", "$start_day"]}}
            }
        }
    ]
    result = list(collection.aggregate(pipeline))
    
    # Calculate combined total and active days
    combined_time = sum(entry["time"] for entry in result)
    combined_active_days = len(set(day for entry in result for day in entry["active_days"]))
    total_days = daysInMonth(year, month)
    
    # Calculate activity ratios
    combined_ratio = (combined_active_days / total_days)
    
    # Update combined document
    aggregations.update_one(
        {
            "_id": "Combined"
        },
        {
            "$set": {
                f"years.{year}.months.{month}.time": combined_time,
                f"years.{year}.months.{month}.active_days": combined_active_days,
                f"years.{year}.months.{month}.total_days": total_days,
                f"years.{year}.months.{month}.activity_ratio": combined_ratio
            }
        },
        upsert=True
    )
    
    # Update the specific user's document
    user_entry = next((entry for entry in result if entry["_id"] == user), None)
    if user_entry:
        user_active_days = len(user_entry["active_days"])
        user_ratio = (user_active_days / total_days)
        
        aggregations.update_one(
            {
                "_id": user
            },
            {
                "$set": {
                    f"years.{year}.months.{month}.time": user_entry["time"],
                    f"years.{year}.months.{month}.active_days": user_active_days,
                    f"years.{year}.months.{month}.total_days": total_days,
                    f"years.{year}.months.{month}.activity_ratio": user_ratio
                }
            },
            upsert=True
        )

def aggregate_year(year, user):
    pipeline = [
        {
            "$match": {
                "start_year": year
            }
        },
        {
            "$group": {
                "_id": "$user",
                "time": {"$sum": "$elapsed_time"},
                "active_days": {"$addToSet": {"$concat": ["$start_year", "-", "$start_month", "-", "$start_day"]}}
            }
        }
    ]
    result = list(collection.aggregate(pipeline))
    
    # Calculate combined total and active days
    combined_time = sum(entry["time"] for entry in result)
    combined_active_days = len(set(day for entry in result for day in entry["active_days"]))
    total_days = daysInYear(year)
    
    # Calculate activity ratios
    combined_ratio = (combined_active_days / total_days)
    
    # Update combined document
    aggregations.update_one(
        {
            "_id": "Combined"
        },
        {
            "$set": {
                f"years.{year}.time": combined_time,
                f"years.{year}.active_days": combined_active_days,
                f"years.{year}.total_days": total_days,
                f"years.{year}.activity_ratio": combined_ratio
            }
        },
        upsert=True
    )
    
    # Update the specific user's document
    user_entry = next((entry for entry in result if entry["_id"] == user), None)
    if user_entry:
        user_active_days = len(user_entry["active_days"])
        user_ratio = (user_active_days / total_days)
        
        aggregations.update_one(
            {
                "_id": user
            },
            {
                "$set": {
                    f"years.{year}.time": user_entry["time"],
                    f"years.{year}.active_days": user_active_days,
                    f"years.{year}.total_days": total_days,
                    f"years.{year}.activity_ratio": user_ratio
                }
            },
            upsert=True
        )

def recalculate_all_aggregations():
    """
    Recalculate all aggregations from scratch.
    This is useful if you need to rebuild all summaries.
    """
    # Get all unique years from the collection
    years = collection.distinct("start_year")
    
    for year in years:
        # Get all months for this year
        months = collection.distinct("start_month", {"start_year": year})
        for month in months:
            # Get all days for this month
            days = collection.distinct("start_day", {
                "start_year": year,
                "start_month": month
            })
            for day in days:
                aggregate_day(year, month, day, user)
            
            # Aggregate monthly data
            aggregate_month(year, month, user)
        
        # Get all weeks for this year
        weeks = collection.distinct("start_week", {"start_year": year})
        for week in weeks:
            aggregate_week(year, week, user)
        
        # Aggregate yearly data
        aggregate_year(year, user)

def recalculate_all_highscores():
    """
    Recalculate all highscores from scratch using the raw data in the collection.
    This is useful when the highscores document gets corrupted or needs to be reset.
    """
    # Delete existing highscores
    aggregations.delete_one({"_id": "Highscores"})
    
    # Get all entries sorted by date
    entries = list(collection.find().sort("start_year", 1).sort("start_month", 1).sort("start_day", 1).sort("start_time", 1))
    
    # Track current periods for each user
    current_periods = {}
    
    for entry in entries:
        user = entry["user"]
        date_str = f"{entry['start_year']}-{entry['start_month']}-{entry['start_day']} {entry['start_time']}"
        
        # Initialize tracking for new users
        if user not in current_periods:
            current_periods[user] = {
                "year": None,
                "month": None,
                "week": None,
                "day": None,
                "year_total": 0,
                "month_total": 0,
                "week_total": 0,
                "day_total": 0,
                "year_active_days": set(),
                "month_active_days": set(),
                "week_active_days": set()
            }
        
        # Update year total
        if current_periods[user]["year"] != entry["start_year"]:
            if current_periods[user]["year"] is not None:
                year_total_days = daysInYear(current_periods[user]["year"])
                year_activity_data = {
                    "active_days": len(current_periods[user]["year_active_days"]),
                    "total_days": year_total_days,
                    "activity_ratio": len(current_periods[user]["year_active_days"]) / year_total_days
                }
                update_highscore(user, "Year", current_periods[user]["year_total"], date_str, True, year_activity_data)
            current_periods[user]["year"] = entry["start_year"]
            current_periods[user]["year_total"] = 0
            current_periods[user]["year_active_days"] = set()
        current_periods[user]["year_total"] += entry["elapsed_time"]
        current_periods[user]["year_active_days"].add(f"{entry['start_year']}-{entry['start_month']}-{entry['start_day']}")
        
        # Update month total
        if current_periods[user]["month"] != (entry["start_year"], entry["start_month"]):
            if current_periods[user]["month"] is not None:
                month_total_days = daysInMonth(current_periods[user]["month"][0], current_periods[user]["month"][1])
                month_activity_data = {
                    "active_days": len(current_periods[user]["month_active_days"]),
                    "total_days": month_total_days,
                    "activity_ratio": len(current_periods[user]["month_active_days"]) / month_total_days
                }
                update_highscore(user, "Month", current_periods[user]["month_total"], date_str, True, month_activity_data)
            current_periods[user]["month"] = (entry["start_year"], entry["start_month"])
            current_periods[user]["month_total"] = 0
            current_periods[user]["month_active_days"] = set()
        current_periods[user]["month_total"] += entry["elapsed_time"]
        current_periods[user]["month_active_days"].add(f"{entry['start_year']}-{entry['start_month']}-{entry['start_day']}")
        
        # Update week total
        if current_periods[user]["week"] != (entry["start_year"], entry["start_week"]):
            if current_periods[user]["week"] is not None:
                week_total_days = daysInWeek()
                week_activity_data = {
                    "active_days": len(current_periods[user]["week_active_days"]),
                    "total_days": week_total_days,
                    "activity_ratio": len(current_periods[user]["week_active_days"]) / week_total_days
                }
                update_highscore(user, "Week", current_periods[user]["week_total"], date_str, True, week_activity_data)
            current_periods[user]["week"] = (entry["start_year"], entry["start_week"])
            current_periods[user]["week_total"] = 0
            current_periods[user]["week_active_days"] = set()
        current_periods[user]["week_total"] += entry["elapsed_time"]
        current_periods[user]["week_active_days"].add(f"{entry['start_year']}-{entry['start_month']}-{entry['start_day']}")
        
        # Update day total
        if current_periods[user]["day"] != (entry["start_year"], entry["start_month"], entry["start_day"]):
            if current_periods[user]["day"] is not None:
                update_highscore(user, "Day", current_periods[user]["day_total"], date_str, True)
            current_periods[user]["day"] = (entry["start_year"], entry["start_month"], entry["start_day"])
            current_periods[user]["day_total"] = 0
        current_periods[user]["day_total"] += entry["elapsed_time"]
    
    # Update final totals for the last periods
    for user, periods in current_periods.items():
        date_str = f"{periods['year']}-{periods['month'][1]}-{entry['start_day']} {entry['start_time']}"
        
        if periods["year"] is not None:
            year_total_days = daysInYear(periods["year"])
            year_activity_data = {
                "active_days": len(periods["year_active_days"]),
                "total_days": year_total_days,
                "activity_ratio": len(periods["year_active_days"]) / year_total_days
            }
            update_highscore(user, "Year", periods["year_total"], date_str, True, year_activity_data)
        
        if periods["month"] is not None:
            month_total_days = daysInMonth(periods["month"][0], periods["month"][1])
            month_activity_data = {
                "active_days": len(periods["month_active_days"]),
                "total_days": month_total_days,
                "activity_ratio": len(periods["month_active_days"]) / month_total_days
            }
            update_highscore(user, "Month", periods["month_total"], date_str, True, month_activity_data)
        
        if periods["week"] is not None:
            week_total_days = daysInWeek()
            week_activity_data = {
                "active_days": len(periods["week_active_days"]),
                "total_days": week_total_days,
                "activity_ratio": len(periods["week_active_days"]) / week_total_days
            }
            update_highscore(user, "Week", periods["week_total"], date_str, True, week_activity_data)
        
        if periods["day"] is not None:
            update_highscore(user, "Day", periods["day_total"], date_str, True)

# Toggle button function
def toggle_button():
    global start_time, running, elapsed_time, elapsed_seconds, elapsed_minutes, elapsed_hours
    if not running:
        start_time = time.time() - elapsed_time
        running = True
        button.configure(text="Pause", font=("Arial", 24))
        done_button.grid_forget()
        update_timer()
        button.grid_configure(columnspan=2)
    else:
        running = False
        button.configure(text="Continue", font=("Arial", 14))
        label.configure(text=format_time(int(elapsed_time)))
        done_button.grid(row=0, column=1, sticky='nsew', padx=4, pady=4)
        button.grid_configure(columnspan=1)

# Update timer function
def update_timer():
    global elapsed_time, elapsed_seconds, elapsed_minutes, elapsed_hours
    if running:
        elapsed_time = time.time() - start_time
        elapsed_seconds = int(elapsed_time % 60)
        elapsed_minutes = int((elapsed_time // 60) % 60)
        elapsed_hours = int(elapsed_time // 3600)
        label.configure(text=format_time(int(elapsed_time)))
        root.after(1000, update_timer)

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
            {
                "$match": {
                    "start_year": date_str[:4],
                    "start_month": date_str[5:7] if time_type in ["Month", "Week", "Day"] else {"$exists": True},
                    "start_week": f"{int(datetime.strptime(date_str[:10], '%Y-%m-%d').strftime('%W')) + 1:02d}" if time_type == "Week" else {"$exists": True},
                    "start_day": date_str[8:10] if time_type == "Day" else {"$exists": True}
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_time": {"$sum": "$elapsed_time"},
                    "active_days": {"$addToSet": {"$concat": ["$start_year", "-", "$start_month", "-", "$start_day"]}}
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
                total_days = daysInYear(date_str[:4]) if time_type == "Year" else \
                           daysInMonth(date_str[:4], date_str[5:7]) if time_type == "Month" else \
                           daysInWeek()
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
    
    # Get current year, month, week, and day
    year = dt.strftime("%Y")
    month = dt.strftime("%m")
    day = dt.strftime("%d")
    week = f"{int(dt.strftime('%W')) + 1:02d}"
    
    # Get total time and activity data for current year
    year_pipeline = [
        {
            "$match": {
                "start_year": year,
                "user": user
            }
        },
        {
            "$group": {
                "_id": None,
                "total_time": {"$sum": "$elapsed_time"},
                "active_days": {"$addToSet": {"$concat": ["$start_year", "-", "$start_month", "-", "$start_day"]}}
            }
        }
    ]
    year_data = list(collection.aggregate(year_pipeline))
    if year_data:
        year_time = year_data[0]["total_time"]
        year_active_days = len(year_data[0]["active_days"])
        year_total_days = daysInYear(year)
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
                "start_year": year,
                "start_month": month,
                "user": user
            }
        },
        {
            "$group": {
                "_id": None,
                "total_time": {"$sum": "$elapsed_time"},
                "active_days": {"$addToSet": {"$concat": ["$start_year", "-", "$start_month", "-", "$start_day"]}}
            }
        }
    ]
    month_data = list(collection.aggregate(month_pipeline))
    if month_data:
        month_time = month_data[0]["total_time"]
        month_active_days = len(month_data[0]["active_days"])
        month_total_days = daysInMonth(year, month)
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
                "start_year": year,
                "start_week": week,
                "user": user
            }
        },
        {
            "$group": {
                "_id": None,
                "total_time": {"$sum": "$elapsed_time"},
                "active_days": {"$addToSet": {"$concat": ["$start_year", "-", "$start_month", "-", "$start_day"]}}
            }
        }
    ]
    week_data = list(collection.aggregate(week_pipeline))
    if week_data:
        week_time = week_data[0]["total_time"]
        week_active_days = len(week_data[0]["active_days"])
        week_total_days = daysInWeek()
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
                "start_year": year,
                "start_month": month,
                "start_day": day,
                "user": user
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

# Modify the log_entry function to include highscore updates
def log_entry():
    global name, description, start_time, elapsed_time, elapsed_seconds, elapsed_minutes, elapsed_hours, running
    dt = datetime.fromtimestamp(start_time)

    entry = {
        "name": name,
        "user": user,
        "description": description,
        "elapsed_time": elapsed_time,
        "start_year": dt.strftime("%Y"),
        "start_month": dt.strftime("%m"),
        "start_day": dt.strftime("%d"),
        "start_time": dt.strftime("%H:%M:%S"),
        "start_week": str(dt.isocalendar().week),
        "start_weekday": dt.strftime("%A"),
        "elapsed_hours": elapsed_hours,
        "elapsed_minutes": elapsed_minutes,
        "elapsed_seconds": elapsed_seconds
    }

    collection.insert_one(entry)
    
    # Calculate summaries for all time periods affected by this entry
    year = dt.strftime("%Y")
    month = dt.strftime("%m")
    day = dt.strftime("%d")
    week = f"{int(dt.strftime('%W')) + 1:02d}"
    weekday = dt.strftime("%A")
    
    # Aggregate all timeframes
    aggregate_year(year, user)
    aggregate_month(year, month, user)
    aggregate_day(year, month, day, user)
    aggregate_week(year, week, user)
    aggregate_weekday(year, month, day, weekday, user)
    
    # Update highscores
    check_and_update_highscores(user, elapsed_time, dt)
    
    reset_state()

# Reset state and UI
def reset_state():
    global name, description, start_time, elapsed_time, elapsed_seconds, elapsed_minutes, elapsed_hours, running

    name = description = start_time = None
    elapsed_time = elapsed_seconds = elapsed_minutes = elapsed_hours = 0
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

def daysInWeek():
    return 7

def daysInMonth(year, month):
    year = int(year)
    month = int(month)
    if month == 2:
        if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
            return 29
        return 28
    if month in [1, 3, 5, 7, 8, 10, 12]:
        return 31
    return 30

def daysInYear(year):
    year = int(year)
    if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
        return 366
    return 365

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

# Move all the main application setup and execution into the if block
if __name__ == "__main__":
    # Main application setup
    root = ctk.CTk()
    root.title("Timetable (New Version)")
    root.geometry("200x170")

    button = ctk.CTkButton(root, text="Start", fg_color="#000000", hover_color="#121212", 
                           text_color="white", font=("Arial", 24), command=toggle_button)
    button.grid(row=0, column=0, sticky='nsew', padx=4, pady=4, columnspan=2)

    label = ctk.CTkLabel(root, text="00:00", font=("Arial", 32), text_color="white")
    label.grid(row=1, column=0, sticky='nsew', columnspan=2)

    done_button = ctk.CTkButton(root, text="Done", fg_color="#000000", hover_color="#121212", 
                                text_color="white", font=("Arial", 14), command=show_entry_overlay)
    done_button.grid_forget()

    root.grid_rowconfigure([0, 1], weight=1, uniform='a')
    root.grid_columnconfigure([0, 1], weight=1, uniform='b')

    root.mainloop()