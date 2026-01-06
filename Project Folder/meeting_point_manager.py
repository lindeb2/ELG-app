import customtkinter as ctk
import datetime
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from CtkSmartScrollableFrame import CtkSmartScrollableFrame
from CTkFlexToolTip import CTkFlexToolTip

COLOR_BACKGROUND=   "#000000" # Background [Root, MenuBar, ScreenFrame, Add- Edit- & Editpoint-Screens, EditScreen.F, EditScreen.F.F] 0      0       0
COLOR_SELECTED=     "#191919" # Selected buttons                                                                                      25     9.8     10
COLOR_PRIMARY=      "#232323" # Widgets [Buttons, Entries, Labels, Scrollbars]                                                        35     13.7    14
COLOR_HOVER=        "#333333" # Hover [Buttons, Scrollbars] & EntryBorders & Tooltips                                                 51     20      20
COLOR_DISABLED_TEXT="#666666" # Disabled text                                                                                         102    40      40
COLOR_TEXT=         "#FFFFFF" # Text                                                                                                  255    100     10

class MeetingPointManagerApp:
    # Connect to MongoDB
    client = MongoClient("mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/?retryWrites=true&w=majority&appName=ELG-timetable", server_api=ServerApi('1'))
    db = client['ELG-Database']
    status_meeting_collection = db['Status Meeting']

    def __init__(self, root):
        self.root = root
        self.root.title("")
        self.root.geometry("200x170")
        self.root.iconbitmap("Project Folder\ELG Studio 0.1_16_clean_big.ico")

        # State variables
        self.in_edit_screen = False
        self.in_edit_point_screen = False
        self.active_overlay_point = None
        self.edit_point = None

        # Week/year options
        today = datetime.date.today()
        self.week_options = []
        for offset in [-1, 0, 1]:
            dt = today + datetime.timedelta(weeks=offset)
            year, week, _ = dt.isocalendar()
            self.week_options.append((str(year), str(week)))
        self.selected_week_index = 1
        self.selected_year_week = self.week_options[1]

        # --- Menu Bar ---
        self.menu_bar = ctk.CTkFrame(self.root, height=26, fg_color=COLOR_BACKGROUND, corner_radius=0)
        self.menu_bar.pack(fill="x")

        self.back_button = ctk.CTkButton(self.menu_bar, text="◀", width=28, fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER, text_color=COLOR_TEXT, font=("Arial", 19), corner_radius=0, command=self.back)
        self.back_button.pack(side="left", padx=(4,2))

        self.edit_button = ctk.CTkButton(self.menu_bar, text="⚙", width=28, fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER, text_color=COLOR_TEXT, font=("Arial", 16), corner_radius=0, command=self.toggle_edit_screen)
        self.edit_button.pack(side="left", padx=2)

        self.add_button = ctk.CTkButton(self.menu_bar, text="➕", width=28, fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER, text_color=COLOR_TEXT, text_color_disabled=COLOR_DISABLED_TEXT, font=("Arial", 17), corner_radius=0, command=self.add_button, takefocus=False, state="disabled")
        self.add_button.pack(side="left", padx=2)

        self.week_offset = ctk.CTkSegmentedButton(self.menu_bar, values=["-1", "0", "+1"], fg_color=COLOR_PRIMARY, font=("Arial", 14, "bold"), corner_radius=0, command=self.update_selected_week
        ,unselected_color=COLOR_PRIMARY, unselected_hover_color=COLOR_HOVER, selected_color=COLOR_SELECTED, selected_hover_color=COLOR_SELECTED)
        self.week_offset.set("0")
        self.week_offset.pack(side="right", padx=(2,4))

        # Bind global mouse click to reset focus if needed
        self.root.bind_all("<Button-1>", self._reset_focus_if_needed, add="+")

        # --- Screen Frame ---
        self.screen_frame = ctk.CTkFrame(self.root, fg_color=COLOR_BACKGROUND)
        self.screen_frame.pack(expand=True, fill="both")

        # --- EditScreen ---
        self.edit_screen = ctk.CTkFrame(self.screen_frame, fg_color=COLOR_BACKGROUND, corner_radius=0)
        self.edit_screen.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.edit_screen.rowconfigure(0, weight=1)
        self.edit_screen.columnconfigure(0, weight=1)

        # --- Weeks in Editscreen ---
        self.week_list_frames = []
        def create_week_list_frame(year, week):
            frame = CtkSmartScrollableFrame(self.edit_screen, corner_radius=0, fg_color=COLOR_BACKGROUND, scrollbar_button_color=COLOR_PRIMARY, scrollbar_button_hover_color=COLOR_HOVER)
            frame.grid(row=0, column=0, sticky="nsew", padx=3, pady=2)
            doc = self.status_meeting_collection.find_one({"_id": "Discussion Points"})
            points = doc.get(year, {}).get(week, []) if doc else []
            # Only using place in a smartframe does not seem to work. Why? Who knows??? Anyway, this is a workaround.
            frame.bug_fix_frame = ctk.CTkFrame(frame, height=len(points)*40, corner_radius=0, fg_color=COLOR_BACKGROUND, bg_color=COLOR_BACKGROUND)
            frame.bug_fix_frame.pack()
            frame.point_frames = []
            for point in points:
                self.create_point_label(frame, point) # type: ignore
            self.week_list_frames.append(frame)
            return frame

        for year, week in self.week_options:
            create_week_list_frame(year, week)
        self.week_list_frames[1].lift()

        # --- EditPointScreen ---
        self.edit_point_screen = ctk.CTkFrame(self.screen_frame, corner_radius=0, fg_color=COLOR_BACKGROUND)
        self.edit_point_screen.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.edit_point_screen.grid_rowconfigure([0, 1], weight=1, uniform='a')
        self.edit_point_screen.grid_columnconfigure([0, 1], weight=1, uniform='b')

        self.edit_point_entry = ctk.CTkEntry(self.edit_point_screen, placeholder_text="Point", font=("Arial", 18), state="disabled", fg_color=COLOR_PRIMARY, border_color=COLOR_HOVER, placeholder_text_color=COLOR_DISABLED_TEXT, text_color=COLOR_TEXT)
        self.edit_point_entry.grid(row=0, column=0, padx=6, pady=(6,3), sticky='nsew', columnspan=2)
        self.edit_point_entry.bind("<Return>", lambda event: self.update_point())
        self.edit_point_entry.bind("<KeyPress>", lambda event: self.root.after_idle(self.update_add_button_state))

        self.edit_description_entry = ctk.CTkEntry(self.edit_point_screen, placeholder_text="Description (optional)", font=("Arial", 18), state="disabled", fg_color=COLOR_PRIMARY, border_color=COLOR_HOVER, placeholder_text_color=COLOR_DISABLED_TEXT, text_color=COLOR_TEXT)
        self.edit_description_entry.grid(row=1, column=0, padx=6, pady=(3,6), sticky='nsew', columnspan=2)
        self.edit_description_entry.bind("<Return>", lambda event: self.update_point())


        # --- AddScreen ---
        self.add_screen = ctk.CTkFrame(self.screen_frame, corner_radius=0, fg_color=COLOR_BACKGROUND)
        self.add_screen.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.add_screen.grid_rowconfigure([0, 1], weight=1, uniform='a')
        self.add_screen.grid_columnconfigure([0, 1], weight=1, uniform='b')

        self.point_entry = ctk.CTkEntry(self.add_screen, placeholder_text="Point", font=("Arial", 18), fg_color=COLOR_PRIMARY, border_color=COLOR_HOVER, placeholder_text_color=COLOR_DISABLED_TEXT, text_color=COLOR_TEXT)
        self.point_entry.grid(row=0, column=0, padx=6, pady=(6,3), sticky='nsew', columnspan=2)
        self.point_entry.bind("<Return>", lambda event: self.add())
        self.point_entry.bind("<KeyPress>", lambda event: self.root.after_idle(self.update_add_button_state))

        self.description_entry = ctk.CTkEntry(self.add_screen, placeholder_text="Description (optional)", font=("Arial", 18), fg_color=COLOR_PRIMARY, border_color=COLOR_HOVER, placeholder_text_color=COLOR_DISABLED_TEXT, text_color=COLOR_TEXT)
        self.description_entry.grid(row=1, column=0, padx=6, pady=(3,6), sticky='nsew', columnspan=2)
        self.description_entry.bind("<Return>", lambda event: self.add())

    def show_edit_screen(self):
        self.edit_screen.lift()
        self.add_button.configure(state="normal", takefocus=True)
        self.edit_button.configure(fg_color=COLOR_SELECTED, hover_color=COLOR_SELECTED)
        self.point_entry.configure(state="disabled")
        self.description_entry.configure(state="disabled")
        self.in_edit_screen = True

    def show_add_screen(self):
        self.add_screen.lift()
        self.update_add_button_state()
        self.edit_button.configure(fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER)
        self.point_entry.configure(state="normal")
        self.description_entry.configure(state="normal")
        self.in_edit_screen = False

    def show_edit_point_screen(self, point_data):
        self.edit_point = point_data
        data = point_data["data"]
        self.edit_point_screen.lift()
        self.add_button.configure(text="⟳")
        self.edit_point_entry.configure(state="normal")
        self.edit_point_entry.delete(0, "end")
        self.edit_point_entry.insert(0, data.get("title"))
        self.edit_description_entry.configure(state="normal")
        self.edit_description_entry.delete(0, "end")
        self.edit_description_entry.insert(0, data.get("description", ""))
        self.in_edit_point_screen = True
        self.root.after_idle(self.edit_point_entry.focus_set)
        self.edit_point_entry.select_range(0, 'end')

    def hide_edit_point_screen(self):
        self.edit_point = None # not really needed
        self.edit_point_screen.lower()
        self.add_button.configure(text="➕", state="normal", takefocus=True)
        self.in_edit_point_screen = False
        self.edit_point_entry.configure(state="disabled")
        self.edit_description_entry.configure(state="disabled")

    def back(self):
        if not self.in_edit_screen:
            self.root.destroy()
        elif self.in_edit_point_screen:
            self.hide_edit_point_screen()
        else:
            self.show_add_screen()

    def toggle_edit_screen(self):
        if self.in_edit_screen:
            if self.in_edit_point_screen:
                self.hide_edit_point_screen()
            self.show_add_screen()
        else:
            self.show_edit_screen()

    def create_point_label(self, master, point):
        point_frame = ctk.CTkFrame(master, fg_color=COLOR_BACKGROUND, corner_radius=0, height=34)
        y = (len(master.point_frames) * 40) + 3 # [index] (34 [height] + 6 [margin]) + 3 [top margin = margin / 2] 
        point_frame.place(relx=0, y=y, relwidth=1, anchor="nw")
        point_pad_frame = ctk.CTkFrame(point_frame, fg_color=COLOR_PRIMARY, corner_radius=10, height=36)
        point_pad_frame.pack(fill="both", expand=True, padx=3)

        label_frame = ctk.CTkFrame(point_pad_frame, width=0, height=28, corner_radius=0, fg_color=COLOR_PRIMARY)
        label_frame.pack_propagate(False)
        label_frame.pack(side="left", expand="True", fill="x", anchor="w", padx=6, pady=3)
        label = ctk.CTkLabel(label_frame, text=point["title"], font=("Arial", 16), text_color=COLOR_TEXT, fg_color=COLOR_PRIMARY, height=28, anchor="w")
        label.pack(side="left", expand="True", fill="y", anchor="w")

        # Add a tooltip for description if present
        if "description" in point and point["description"]:
            label_frame_tooltip = CTkFlexToolTip(label_frame, message=point["description"], delay=0.2, bg_color=COLOR_HOVER, corner_radius=5, padding=(5, 4), alpha=1, border_width=1, border_color=COLOR_BACKGROUND, text_color=COLOR_TEXT)
            label_tooltip = CTkFlexToolTip(label, message=point["description"], delay=0.2, bg_color=COLOR_HOVER, corner_radius=5, padding=(5, 4), alpha=1, border_width=1, border_color=COLOR_BACKGROUND, text_color=COLOR_TEXT)
        else:
            label_frame_tooltip = None
            label_tooltip = None

        drag_label = ctk.CTkLabel(point_pad_frame, text="☰", font=("Arial", 25), text_color=COLOR_TEXT, fg_color=COLOR_PRIMARY, width=28, cursor="fleur")
        drag_label.bind("<Button-1>", lambda e: self._on_drag_start(e, point_data))
        drag_label.bind("<B1-Motion>", lambda e: self._on_drag_motion(e, point_data))
        drag_label.bind("<ButtonRelease-1>", lambda e: self._on_drag_release(e, point_data))
        edit_label = ctk.CTkLabel(point_pad_frame, text="✏️", font=("Arial", 16), text_color=COLOR_TEXT, fg_color=COLOR_PRIMARY, width=28, cursor="hand2")
        edit_label.bind("<Button-1>", lambda e: self.show_edit_point_screen(point_data))
        delete_label = ctk.CTkLabel(point_pad_frame, text="⌫", font=("Arial", 16), text_color=COLOR_TEXT, fg_color=COLOR_PRIMARY, width=28, cursor="hand2")
        delete_label.bind("<Button-1>", lambda e: self.delete_point(point_data))

        point_data = {
            'frame': point_frame,
            'pad_frame': point_pad_frame,
            'label_frame': label_frame,
            'label_frame_tooltip': label_frame_tooltip,
            'label': label,
            'label_tooltip': label_tooltip,
            'drag_label': drag_label,
            'edit_label': edit_label,
            'delete_label': delete_label,
            'data': point,
            'anim_after_id': None,
        }
        master.point_frames.append(point_data)

        # Bind <Enter> and <Leave> to all relevant widgets
        widgets_to_bind = [point_frame, point_pad_frame, label_frame, label, drag_label, edit_label, delete_label]
        for w in widgets_to_bind:
            # noinspection PyDefaultArgument
            w.bind("<Enter>", lambda e, pd=point_data: self._on_point_hover_enter(pd)) # noqa: B006
            w.bind("<Leave>", lambda e: self._on_point_hover_leave(e))

    def _on_point_hover_enter(self, point_data):
        if self.active_overlay_point is point_data:
            return
        self.hide_overlay()
        self.show_overlay(point_data)

    def _on_point_hover_leave(self, event):
        try:
            x, y = event.x_root, event.y_root
            entered_widget = event.widget.winfo_containing(x, y).master # ERROR RISK
            if entered_widget in self.active_overlay_point.values(): # ERROR RISK
                return
            self.hide_overlay()
        except AttributeError:
            pass

    def show_overlay(self, point_data):
        self.active_overlay_point = point_data
        point_data['label_frame'].pack_forget()
        point_data['drag_label'].pack(side="left", padx=(3,0))
        point_data['label_frame'].pack(side="left", expand="True", fill="x", anchor="w", padx=3, pady=3)
        point_data['edit_label'].pack(side="left")
        point_data['delete_label'].pack(side="left", padx=(0,3))

    def hide_overlay(self):
        if self.active_overlay_point is None:
            return
        pd = self.active_overlay_point
        pd['drag_label'].pack_forget()
        pd['edit_label'].pack_forget()
        pd['delete_label'].pack_forget()
        pd['label_frame'].pack_configure(padx=6)
        self.active_overlay_point = None

    def add(self):
        title = self.point_entry.get().strip()
        if title == "":
            return
        title = title[0].upper() + title[1:]
        description = self.description_entry.get().strip()
        doc = self.status_meeting_collection.find_one({"_id": "Discussion Points"})
        year, week = self.selected_year_week
        if year not in doc:
            doc[year] = {}
        if week not in doc[year]:
            doc[year][week] = []
        points = doc[year][week]
        for p in points:
            if p.get("title") == title:
                # TODO Error feedback
                return
        self.point_entry.delete(0, "end")
        self.description_entry.delete(0, "end")
        point = {"title": title}
        if description and description != "":
            point["description"] = description
        points.append(point)
        self.status_meeting_collection.update_one({"_id": "Discussion Points"}, {"$set": {f"{year}.{week}": points}}, upsert=True)
        frame = self.week_list_frames[self.selected_week_index]
        self.create_point_label(frame, point)
        frame.bug_fix_frame.configure(height=len(frame.point_frames)*40)
        self.point_entry.focus_set()
        # noinspection PyProtectedMember
        self.description_entry._activate_placeholder()
        self.add_button.configure(state="disabled", takefocus=False)

    def add_button(self):
        if self.in_edit_screen:
            if self.in_edit_point_screen:
                self.update_point()
                return
            self.show_add_screen()
            self.point_entry.delete(0, "end")
            self.description_entry.delete(0, "end")
            self.root.after_idle(self.point_entry.focus_set)
            return
        self.add()
        self.root.focus_set()

    def update_selected_week(self, value):
        self.selected_week_index = ["-1", "0", "+1"].index(value)
        self.selected_year_week = self.week_options[self.selected_week_index]
        self.week_list_frames[self.selected_week_index].lift()

    def update_add_button_state(self):
        title = self.edit_point_entry.get() if self.in_edit_point_screen else self.point_entry.get()
        if title.strip() == "":
            self.add_button.configure(state="disabled", takefocus=False)
        else:
            self.add_button.configure(state="normal", takefocus=True)

    def _reset_focus_if_needed(self, event):
        widget = event.widget
        if ".!ctkentry" in str(widget):
            return
        if ".!ctksegmentedbutton" in str(widget):
            return
        self.root.focus_set()
        for attr_name in dir(self):
            if attr_name.endswith('_entry'):
                entry_widget = getattr(self, attr_name, None)
                if entry_widget is not None and entry_widget.get() == "":
                    # noinspection PyProtectedMember
                    entry_widget._activate_placeholder()

    def update_point(self):
        new_title = self.edit_point_entry.get().strip()
        if new_title == "":
            return
        new_title = new_title[0].upper() + new_title[1:]
        new_description = self.edit_description_entry.get().strip()
        # DB
        year, week = self.selected_year_week
        doc = self.status_meeting_collection.find_one({"_id": "Discussion Points"})
        points = doc[year][week]
        old_title = self.edit_point["data"].get("title")
        for p in points:
            if p.get("title") == old_title:
                p["title"] = new_title
                if new_description:
                    p["description"] = new_description
                elif "description" in p:
                    del p["description"]
                break
        self.status_meeting_collection.update_one(
            {"_id": "Discussion Points"},
            {"$set": {f"{year}.{week}": points}},
            upsert=True
        )
        # Cache
        self.edit_point["data"]["title"] = new_title
        if new_description:
            self.edit_point["data"]["description"] = new_description
        elif "description" in self.edit_point["data"]:
            del self.edit_point["data"]["description"]
        # UI
        self.edit_point["label"].configure(text=new_title)

        # Update tooltip for description
        if self.edit_point['label_frame_tooltip'] is not None or self.edit_point['label_tooltip'] is not None:
            if new_description:
                self.edit_point['label_frame_tooltip'].configure(message=new_description)
                self.edit_point['label_tooltip'].configure(message=new_description)
            else:
                self.edit_point['label_frame_tooltip'].hide()
                self.edit_point['label_frame_tooltip'].destroy()
                self.edit_point['label_frame_tooltip'] = None
                self.edit_point['label_tooltip'].hide()
                self.edit_point['label_tooltip'].destroy()
                self.edit_point['label_tooltip'] = None
        else:
            if new_description:
                self.edit_point['label_frame_tooltip'] = CTkFlexToolTip(
                    self.edit_point['label_frame'],
                    message=new_description,
                    delay=0.2,
                    bg_color=COLOR_HOVER,
                    corner_radius=5,
                    padding=(5, 4),
                    alpha=1,
                    border_width=1,
                    border_color=COLOR_BACKGROUND,
                    text_color=COLOR_TEXT
                )
                self.edit_point['label_tooltip'] = CTkFlexToolTip(
                    self.edit_point['label'],
                    message=new_description,
                    delay=0.2,
                    bg_color=COLOR_HOVER,
                    corner_radius=5,
                    padding=(5, 4),
                    alpha=1,
                    border_width=1,
                    border_color=COLOR_BACKGROUND,
                    text_color=COLOR_TEXT
                )

        # Return to edit screen
        self.hide_edit_point_screen()

    def animate_to(self, point_data, target_y):
        # Cancel previous animation if any
        if point_data['anim_after_id']:
            self.root.after_cancel(point_data['anim_after_id'])
            point_data['anim_after_id'] = None
    
        def step():
            frame = point_data['frame']
            if not frame.winfo_exists():
                point_data['anim_after_id'] = None
                return
            current_y = frame.winfo_y()
            if current_y == target_y:
                point_data['anim_after_id'] = None
                return
            dy = target_y - current_y

            max_dy = 40
            dist = abs(dy)
            dist_capped = min(dist, max_dy)
            norm = dist_capped / max_dy  # 0..1

            move = 1 * (1 if dy > 0 else -1)
            frame.place_configure(y=current_y + move)


            k = 10
            if norm == 0:
                ease = 0
            else:
                ease = 1 - 2 ** (-k * norm)

            min_delay = 5    # fastest
            max_delay = 100  # slowest

            delay = int(min_delay + (max_delay - min_delay) * (1 - ease))
            point_data['anim_after_id'] = self.root.after(delay, step)
        step()

    def shrink_debug_frame(self, frame, height):
        self.root.after(750, lambda: frame.configure(height=height))

    def delete_point(self, point_data):
        # DB
        year, week = self.selected_year_week
        doc = self.status_meeting_collection.find_one({"_id": "Discussion Points"})
        points = doc[year][week]
        title = point_data["data"].get("title")
        new_points = [p for p in points if p.get("title") != title]
        self.status_meeting_collection.update_one({"_id": "Discussion Points"}, {"$set": {f"{year}.{week}": new_points}}, upsert=True)
        # Cache
        frame = self.week_list_frames[self.selected_week_index]
        frame.point_frames.remove(point_data)
        # UI
        point_data["frame"].destroy()
        self.active_overlay_point = None
        for i, pd in enumerate(frame.point_frames):
            target_y = i * 40 + 3
            self.animate_to(pd, target_y)
        self.shrink_debug_frame(frame.bug_fix_frame, len(frame.point_frames) * 40)
        
    def _on_drag_start(self, event, point_data):
        self.edit_point = point_data
        self.mouse_y = event.y_root
        self.point_y = point_data['frame'].winfo_y()
        self.scroll_offset = 0
        point_data['frame'].lift()
    
    def _on_drag_motion(self, event, point_data):
        dy = event.y_root - self.mouse_y
        new_y = self.point_y + dy + self.scroll_offset
        # Clamp new_y within min/max
        min_y = 3
        max_y = (len(self.week_list_frames[self.selected_week_index].point_frames) - 1) * 40 + 3
        new_y = max(min_y, min(new_y, max_y))
        point_data['frame'].place_configure(y=new_y)
        # Calculate new index, animate others
        self._reposition_points_during_drag(point_data, new_y)
        frame = self.week_list_frames[self.selected_week_index]
        frame.scroll_widget_into_view(point_data['frame'])
    
    def _on_drag_release(self, _event, point_data):
        # Snap to nearest slot
        new_y = point_data['frame'].winfo_y()
        new_index = round((new_y - 3) / 40)
        self._finalize_drag(point_data, new_index)
    
    def _reposition_points_during_drag(self, dragging_point, dragging_y):
        point_frames = self.week_list_frames[self.selected_week_index].point_frames
        slot = round((dragging_y - 3) / 40)
        slot = max(0, min(slot, len(point_frames) - 1))
    
        # Build the new order as it would be if dropped now
        current_index = point_frames.index(dragging_point)
        new_order = point_frames.copy()
        new_order.pop(current_index)
        new_order.insert(slot, dragging_point)
    
        # Animate all points to their would-be positions
        for i, pd in enumerate(new_order):
            if pd is dragging_point:
                continue
            target_y = i * 40 + 3
            self.animate_to(pd, target_y)
    
    def _finalize_drag(self, dragging_point, new_index):
        # UI & Cache
        point_frames = self.week_list_frames[self.selected_week_index].point_frames
        current_index = point_frames.index(dragging_point)

        if current_index == new_index:
            target_y = new_index * 40 + 3
            self.animate_to(dragging_point, target_y)
            return

        point_frames.pop(current_index)
        point_frames.insert(new_index, dragging_point)
        for i, pd in enumerate(point_frames):
            target_y = i * 40 + 3
            self.animate_to(pd, target_y)
        # DB
        year, week = self.selected_year_week
        doc = self.status_meeting_collection.find_one({"_id": "Discussion Points"})
        latest_points = doc[year][week]
        latest_points_by_title = {p["title"]: p for p in latest_points}
        new_points = []
        for pd in point_frames:
            title = pd["data"]["title"]
            new_points.append(latest_points_by_title.get(title, pd["data"]))
        self.status_meeting_collection.update_one(
            {"_id": "Discussion Points"},
            {"$set": {f"{year}.{week}": new_points}},
            upsert=True
        )

if __name__ == "__main__":
    root = ctk.CTk(COLOR_BACKGROUND)
    app = MeetingPointManagerApp(root)
    try:
        from ctypes import windll, byref, sizeof, c_int
        HWND = windll.user32.GetParent(root.winfo_id()) # type: ignore
        # 34 = Border, 35 = Titlebar, 36 = TEXT, color format: 0xAABBGGRR
        windll.dwmapi.DwmSetWindowAttribute(HWND, 34, byref(c_int(0x00000000)), sizeof(c_int)) # type: ignore
        windll.dwmapi.DwmSetWindowAttribute(HWND, 35, byref(c_int(0x00000000)), sizeof(c_int)) # type: ignore
    except (ImportError, AttributeError, OSError):
        print("DWM API not available, skipping window attribute settings.")
        pass
    root.mainloop()