import customtkinter as ctk
import time
from datetime import datetime
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import os
import sys
import json

# MongoDB connection
client = MongoClient(
    "mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/?retryWrites=true&w=majority&appName=ELG-timetable",
    server_api=ServerApi('1')
)
db = client['ELG-Database']
collection = db['Timetable']
poolcollection = db['Test1']

# Initialize the customtkinter theme with dark mode
ctk.set_appearance_mode("Dark")

# Global variables
name = description = start_time = end_time = None
elapsed_time = elapsed_seconds = elapsed_minutes = elapsed_hours = 0
running = False

exe_path = sys.executable
root_path = os.path.dirname(os.path.dirname(os.path.dirname(exe_path)))
config_path = os.path.join(root_path, "config.json") # Goes up one level and find the config file
with open(config_path) as file:
    config = json.load(file)
    author = config["author"]

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
        label.configure(text=f"{int(elapsed_hours)}:{int(elapsed_minutes)}:{int(elapsed_seconds)}")
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
        label.configure(text=f"{elapsed_hours}:{elapsed_minutes}:{elapsed_seconds}")
        root.after(1000, update_timer)

# Log entry function
def log_entry():
    global name, description, start_time, elapsed_time, elapsed_seconds, elapsed_minutes, elapsed_hours, running
    dt = datetime.fromtimestamp(start_time)

    entry = {
        "name": name,
        "author": author,
        "description": description,
        "elapsed_time": elapsed_time,
        "start_year": dt.strftime("%Y"),
        "start_month": dt.strftime("%m"),
        "start_day": dt.strftime("%d"),
        "start_time": dt.strftime("%H:%M:%S"),
        "start_week": f"{int(dt.strftime('%W')) + 1:02d}",
        "start_weekday": dt.strftime("%A"),
        "elapsed_hours": elapsed_hours,
        "elapsed_minutes": elapsed_minutes,
        "elapsed_seconds": elapsed_seconds
    }

    collection.insert_one(entry)
    reset_state()

# Reset state and UI
def reset_state():
    global name, description, start_time, elapsed_time, elapsed_seconds, elapsed_minutes, elapsed_hours, running

    name = description = start_time = None
    elapsed_time = elapsed_seconds = elapsed_minutes = elapsed_hours = 0
    running = False

    label.configure(text="0:0:0")
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

# Main application setup
root = ctk.CTk()
root.title("")
root.geometry("200x170")

button = ctk.CTkButton(root, text="Start", fg_color="#000000", hover_color="#121212", 
                       text_color="white", font=("Arial", 24), command=toggle_button)
button.grid(row=0, column=0, sticky='nsew', padx=4, pady=4, columnspan=2)

label = ctk.CTkLabel(root, text="0:0:0", font=("Arial", 32), text_color="white")
label.grid(row=1, column=0, sticky='nsew', columnspan=2)

done_button = ctk.CTkButton(root, text="Done", fg_color="#000000", hover_color="#121212", 
                            text_color="white", font=("Arial", 14), command=show_entry_overlay)
done_button.grid_forget()

root.grid_rowconfigure([0, 1], weight=1, uniform='a')
root.grid_columnconfigure([0, 1], weight=1, uniform='b')

root.mainloop()