"""Shared app behavior preferences UI for setup and settings."""
from __future__ import annotations

import customtkinter as ctk

from app_config import DEFAULT_APP_PREFERENCES, normalize_app_preferences


class AppBehaviorPreferencesPanel(ctk.CTkFrame):
    def __init__(self, parent, *, compact: bool = False):
        super().__init__(parent, fg_color="transparent")
        self._compact = compact
        self.grid_columnconfigure(0, weight=1)

        section_font = ("Arial", 16, "bold") if not compact else ("Arial", 14, "bold")
        label_font = ("Arial", 14) if not compact else ("Arial", 13)
        row = 0

        ctk.CTkLabel(
            self,
            text="When clicking X (close window)",
            font=section_font,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", pady=(0, 6))
        row += 1

        self._close_action = ctk.StringVar(value=DEFAULT_APP_PREFERENCES["close_action"])
        close_frame = ctk.CTkFrame(self, fg_color="transparent")
        close_frame.grid(row=row, column=0, sticky="w", pady=(0, 12))
        row += 1
        ctk.CTkRadioButton(
            close_frame,
            text="Minimize app to the system tray",
            variable=self._close_action,
            value="tray",
            font=label_font,
        ).pack(anchor="w", pady=2)
        ctk.CTkRadioButton(
            close_frame,
            text="Exit app completely",
            variable=self._close_action,
            value="exit",
            font=label_font,
        ).pack(anchor="w", pady=2)

        ctk.CTkLabel(self, text="Startup", font=section_font, anchor="w").grid(
            row=row, column=0, sticky="w", pady=(0, 6)
        )
        row += 1

        self._launch_at_startup_var = ctk.BooleanVar(
            value=DEFAULT_APP_PREFERENCES["launch_at_startup"]
        )
        self._launch_startup_cb = ctk.CTkCheckBox(
            self,
            text="Launch app when I start my computer",
            variable=self._launch_at_startup_var,
            font=label_font,
            command=self._sync_launch_minimized_state,
        )
        self._launch_startup_cb.grid(row=row, column=0, sticky="w", pady=(0, 4))
        row += 1

        self._launch_minimized_var = ctk.BooleanVar(
            value=DEFAULT_APP_PREFERENCES["launch_minimized_to_tray"]
        )
        self._launch_minimized_cb = ctk.CTkCheckBox(
            self,
            text="Launch app minimized to the system tray",
            variable=self._launch_minimized_var,
            font=label_font,
        )
        self._launch_minimized_cb.grid(row=row, column=0, sticky="w", padx=(24, 0), pady=(0, 8))
        row += 1

        ctk.CTkLabel(self, text="On startup, view", font=label_font, anchor="w").grid(
            row=row, column=0, sticky="w", pady=(0, 4)
        )
        row += 1

        self._startup_view = ctk.StringVar(value=DEFAULT_APP_PREFERENCES["startup_view"])
        startup_view_frame = ctk.CTkFrame(self, fg_color="transparent")
        startup_view_frame.grid(row=row, column=0, sticky="w", pady=(0, 4))
        row += 1
        ctk.CTkRadioButton(
            startup_view_frame,
            text="Timetable",
            variable=self._startup_view,
            value="timetable",
            font=label_font,
        ).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(
            startup_view_frame,
            text="Statistics",
            variable=self._startup_view,
            value="statistics",
            font=label_font,
        ).pack(side="left")

        self._sync_launch_minimized_state()

    def _sync_launch_minimized_state(self) -> None:
        enabled = bool(self._launch_at_startup_var.get())
        state = "normal" if enabled else "disabled"
        self._launch_minimized_cb.configure(state=state)
        if not enabled:
            self._launch_minimized_var.set(False)

    def load_from(self, app_prefs: dict | None) -> None:
        prefs = normalize_app_preferences(app_prefs)
        self._close_action.set(prefs["close_action"])
        self._launch_at_startup_var.set(prefs["launch_at_startup"])
        self._launch_minimized_var.set(prefs["launch_minimized_to_tray"])
        self._startup_view.set(prefs["startup_view"])
        self._sync_launch_minimized_state()

    def values(self) -> dict:
        prefs = normalize_app_preferences(
            {
                "close_action": self._close_action.get(),
                "launch_at_startup": self._launch_at_startup_var.get(),
                "launch_minimized_to_tray": self._launch_minimized_var.get(),
                "startup_view": self._startup_view.get(),
            }
        )
        return prefs
