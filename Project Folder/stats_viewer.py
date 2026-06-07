import customtkinter as ctk
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import os
import json
from CtkSmartScrollableFrame import CtkSmartScrollableFrame

# MongoDB connection
client = MongoClient(
    "mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/?retryWrites=true&w=majority&appName=ELG-timetable",
    server_api=ServerApi('1')
)
db = client['ELG-Database']
collection = db['Timetable New']
aggregations = db['Timetable Aggregations']

# Initialize the customtkinter theme with dark mode
ctk.set_appearance_mode("Dark")

class StatsViewer(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Configure window
        self.title("Timetable Stats Viewer")
        self.geometry("800x600")
        
        # Configure grid
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        # Create main container
        self.main_container = ctk.CTkFrame(self)
        self.main_container.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        
        # Configure main container grid
        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)
        
        # Show main menu
        self.show_main_menu()
    
    def show_main_menu(self):
        # Clear main container
        for widget in self.main_container.winfo_children():
            widget.destroy()
        
        # Configure grid for main menu
        self.main_container.grid_rowconfigure((0, 1, 2), weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)
        
        # Create title
        title = ctk.CTkLabel(
            self.main_container,
            text="Timetable Stats Viewer",
            font=("Arial", 32, "bold"),
            text_color="white"
        )
        title.grid(row=0, column=0, pady=20)
        
        # Create buttons container
        buttons_frame = ctk.CTkFrame(self.main_container)
        buttons_frame.grid(row=1, column=0, pady=20)
        buttons_frame.grid_rowconfigure((0, 1, 2), weight=1)
        buttons_frame.grid_columnconfigure(0, weight=1)
        
        # Create navigation buttons
        world_records_btn = ctk.CTkButton(
            buttons_frame,
            text="World Records",
            font=("Arial", 24),
            command=self.show_world_records,
            fg_color="#000000",
            hover_color="#121212",
            text_color="white"
        )
        world_records_btn.grid(row=0, column=0, pady=10, padx=20)
        
        team_records_btn = ctk.CTkButton(
            buttons_frame,
            text="Team Records",
            font=("Arial", 24),
            command=self.show_team_records,
            fg_color="#000000",
            hover_color="#121212",
            text_color="white"
        )
        team_records_btn.grid(row=1, column=0, pady=10, padx=20)
        
        personal_bests_btn = ctk.CTkButton(
            buttons_frame,
            text="Personal Bests",
            font=("Arial", 24),
            command=self.show_personal_bests,
            fg_color="#000000",
            hover_color="#121212",
            text_color="white"
        )
        personal_bests_btn.grid(row=2, column=0, pady=10, padx=20)
    
    def show_world_records(self):
        # Clear main container
        for widget in self.main_container.winfo_children():
            widget.destroy()
        
        # Configure grid
        self.main_container.grid_rowconfigure(0, weight=0)  # Header
        self.main_container.grid_rowconfigure(1, weight=1)  # Content
        self.main_container.grid_columnconfigure(0, weight=1)
        
        # Create header with back button
        header = ctk.CTkFrame(self.main_container)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        header.grid_columnconfigure(1, weight=1)
        
        back_btn = ctk.CTkButton(
            header,
            text="← Back",
            font=("Arial", 14),
            command=self.show_main_menu,
            fg_color="#000000",
            hover_color="#121212",
            text_color="white"
        )
        back_btn.grid(row=0, column=0, padx=5)
        
        title = ctk.CTkLabel(
            header,
            text="World Records",
            font=("Arial", 24, "bold"),
            text_color="white"
        )
        title.grid(row=0, column=1, padx=10)
        
        # Create content area
        content = CtkSmartScrollableFrame(self.main_container)
        content.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=10, pady=10)
        
        # Get world records from database
        highscores = aggregations.find_one({"_id": "Highscores"})
        if highscores and "Global" in highscores:
            self.display_records(content, highscores["Global"], is_global=True)
    
    def show_team_records(self):
        # Clear main container
        for widget in self.main_container.winfo_children():
            widget.destroy()
        
        # Configure grid
        self.main_container.grid_rowconfigure(0, weight=0)  # Header
        self.main_container.grid_rowconfigure(1, weight=1)  # Content
        self.main_container.grid_columnconfigure(0, weight=1)
        
        # Create header with back button
        header = ctk.CTkFrame(self.main_container)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        header.grid_columnconfigure(1, weight=1)
        
        back_btn = ctk.CTkButton(
            header,
            text="← Back",
            font=("Arial", 14),
            command=self.show_main_menu,
            fg_color="#000000",
            hover_color="#121212",
            text_color="white"
        )
        back_btn.grid(row=0, column=0, padx=5)
        
        title = ctk.CTkLabel(
            header,
            text="Team Records",
            font=("Arial", 24, "bold"),
            text_color="white"
        )
        title.grid(row=0, column=1, padx=10)
        
        # Create content area
        content = CtkSmartScrollableFrame(self.main_container)
        content.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=10, pady=10)
        
        # Get team records from database
        highscores = aggregations.find_one({"_id": "Highscores"})
        if highscores and "Combined" in highscores:
            self.display_records(content, highscores["Combined"], is_global=False)
    
    def show_personal_bests(self):
        # Clear main container
        for widget in self.main_container.winfo_children():
            widget.destroy()
        
        # Configure grid
        self.main_container.grid_rowconfigure(0, weight=0)  # Header
        self.main_container.grid_rowconfigure(1, weight=0)  # Author selection
        self.main_container.grid_rowconfigure(2, weight=1)  # Content
        self.main_container.grid_columnconfigure(0, weight=1)
        
        # Create header with back button
        header = ctk.CTkFrame(self.main_container)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        header.grid_columnconfigure(1, weight=1)
        
        back_btn = ctk.CTkButton(
            header,
            text="← Back",
            font=("Arial", 14),
            command=self.show_main_menu,
            fg_color="#000000",
            hover_color="#121212",
            text_color="white"
        )
        back_btn.grid(row=0, column=0, padx=5)
        
        title = ctk.CTkLabel(
            header,
            text="Personal Bests",
            font=("Arial", 24, "bold"),
            text_color="white"
        )
        title.grid(row=0, column=1, padx=10)
        
        # Create author selection area
        author_frame = ctk.CTkFrame(self.main_container)
        author_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=10)
        author_frame.grid_columnconfigure(1, weight=1)
        
        author_label = ctk.CTkLabel(
            author_frame,
            text="Select Author:",
            font=("Arial", 16),
            text_color="white"
        )
        author_label.grid(row=0, column=0, padx=10)
        
        # Get list of authors
        authors = collection.distinct("author")
        author_var = ctk.StringVar(value=authors[0] if authors else "")
        
        author_dropdown = ctk.CTkOptionMenu(
            author_frame,
            values=authors,
            variable=author_var,
            command=lambda x: self.update_personal_bests(x),
            fg_color="#000000",
            button_color="#121212",
            button_hover_color="#2C2C2C",
            text_color="white"
        )
        author_dropdown.grid(row=0, column=1, padx=10, sticky="ew")
        
        # Create content area
        self.content = CtkSmartScrollableFrame(self.main_container)
        self.content.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        
        # Show initial author's records
        if authors:
            self.update_personal_bests(authors[0])
    
    def update_personal_bests(self, author):
        # Clear content area
        for widget in self.content.winfo_children():
            widget.destroy()
        
        # Get author's records from database
        highscores = aggregations.find_one({"_id": "Highscores"})
        if highscores and author in highscores:
            self.display_records(self.content, highscores[author], is_global=False)
    
    def display_records(self, parent, records, is_global=False):
        # Configure grid
        parent.grid_columnconfigure(0, weight=1)
        
        # Display records for each time period
        for time_type in ["Year", "Month", "Week", "Day"]:
            if time_type in records:
                # Create time period frame
                period_frame = ctk.CTkFrame(parent)
                period_frame.grid(row=len(parent.winfo_children()), column=0, sticky="ew", padx=10, pady=5)
                period_frame.grid_columnconfigure(0, weight=1)
                
                # Time period title
                title = ctk.CTkLabel(
                    period_frame,
                    text=time_type,
                    font=("Arial", 20, "bold"),
                    text_color="white"
                )
                title.grid(row=0, column=0, sticky="w", padx=10, pady=5)
                
                # Time record
                if "time" in records[time_type]:
                    time_frame = ctk.CTkFrame(period_frame)
                    time_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
                    time_frame.grid_columnconfigure(1, weight=1)
                    
                    time_label = ctk.CTkLabel(
                        time_frame,
                        text="Total Time:",
                        font=("Arial", 16),
                        text_color="white"
                    )
                    time_label.grid(row=0, column=0, padx=10)
                    
                    time_value = ctk.CTkLabel(
                        time_frame,
                        text=self.format_time(records[time_type]["time"]["value"]),
                        font=("Arial", 16),
                        text_color="white"
                    )
                    time_value.grid(row=0, column=1, sticky="w", padx=10)
                    
                    if records[time_type]["time"]["date"]:
                        date_text = f"Set on: {records[time_type]['time']['date']}"
                        author = records[time_type]["time"].get("author")
                        if is_global and author:
                            date_text += f" by {author}"
                        date_label = ctk.CTkLabel(
                            time_frame,
                            text=date_text,
                            font=("Arial", 12),
                            text_color="white"
                        )
                        date_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=10)
                
                # Activity record (if applicable)
                if time_type != "Day" and "activity" in records[time_type]:
                    activity_frame = ctk.CTkFrame(period_frame)
                    activity_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
                    activity_frame.grid_columnconfigure(1, weight=1)
                    
                    activity_label = ctk.CTkLabel(
                        activity_frame,
                        text="Activity:",
                        font=("Arial", 16),
                        text_color="white"
                    )
                    activity_label.grid(row=0, column=0, padx=10)
                    
                    activity_value = ctk.CTkLabel(
                        activity_frame,
                        text=f"{records[time_type]['activity']['active_days']}/{records[time_type]['activity']['total_days']} days ({records[time_type]['activity']['value']:.1%})",
                        font=("Arial", 16),
                        text_color="white"
                    )
                    activity_value.grid(row=0, column=1, sticky="w", padx=10)
                    
                    if records[time_type]["activity"]["date"]:
                        date_text = f"Set on: {records[time_type]['activity']['date']}"
                        author = records[time_type]["activity"].get("author")
                        if is_global and author:
                            date_text += f" by {author}"
                        date_label = ctk.CTkLabel(
                            activity_frame,
                            text=date_text,
                            font=("Arial", 12),
                            text_color="white"
                        )
                        date_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=10)

        if "consecutive" in records:
            period_frame = ctk.CTkFrame(parent)
            period_frame.grid(row=len(parent.winfo_children()), column=0, sticky="ew", padx=10, pady=5)
            period_frame.grid_columnconfigure(0, weight=1)

            title = ctk.CTkLabel(
                period_frame,
                text="Lifetime Consecutive",
                font=("Arial", 20, "bold"),
                text_color="white",
            )
            title.grid(row=0, column=0, sticky="w", padx=10, pady=5)

            for row_idx, (streak_kind, label) in enumerate((("days", "Days"), ("weeks", "Weeks")), start=1):
                streak = records["consecutive"].get(streak_kind)
                if not streak:
                    continue
                streak_frame = ctk.CTkFrame(period_frame)
                streak_frame.grid(row=row_idx, column=0, sticky="ew", padx=10, pady=5)
                streak_frame.grid_columnconfigure(1, weight=1)

                ctk.CTkLabel(
                    streak_frame,
                    text=f"Consecutive {label}:",
                    font=("Arial", 16),
                    text_color="white",
                ).grid(row=0, column=0, padx=10)

                ctk.CTkLabel(
                    streak_frame,
                    text=str(streak.get("value", 0)),
                    font=("Arial", 16),
                    text_color="white",
                ).grid(row=0, column=1, sticky="w", padx=10)

                if streak.get("date"):
                    date_text = f"Set on: {streak['date']}"
                    author = streak.get("user") or streak.get("author")
                    if is_global and author:
                        date_text += f" by {author}"
                    ctk.CTkLabel(
                        streak_frame,
                        text=date_text,
                        font=("Arial", 12),
                        text_color="white",
                    ).grid(row=1, column=0, columnspan=2, sticky="w", padx=10)
    
    def format_time(self, seconds):
        if seconds is None:
            return "00:00"
        
        # Convert to integers to avoid float formatting issues
        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        
        if hours >= 24:
            return f"{hours} hours"
        elif hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"

if __name__ == "__main__":
    app = StatsViewer()
    app.mainloop() 