"""First-launch setup UI."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from app_config import apply_startup_registration, app_preferences_from_config, read_config, write_config
from notification_preferences import save_notification_prefs
from settings_ui_constants import ACCENT
from utils import bind_digits_only_entry, flash_error

_PADX = 8
_FONT_TITLE = ("Arial", 19, "bold")
_FONT_DESC = ("Arial", 11)
_FONT_BODY = ("Arial", 17)
_FONT_SMALL = ("Arial", 13)
_DESC_COLOR = "#DDDDDD"
_ENTRY = {
    "height": 26,
    "font": _FONT_BODY,
    "fg_color": "#1A1A1A",
    "border_color": "#444444",
    "border_width": 1,
    "text_color": "#FFFFFF",
    "justify": "center",
}
_BTN = {"height": 24, "font": _FONT_BODY, "corner_radius": 6}
_BTN_NAV_WIDTH = 78
_BTN_NAV_GAP = 7
_BTN_WELCOME_WIDTH = 96
_BTN_GRAY = {"fg_color": "#2A2A2A", "hover_color": "#333333"}
_NOTIFY_KEYS = ("notify_others_start", "notify_others_end", "notify_own_start", "notify_own_end")


class SetupFrame(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkFrame, on_complete: Callable[[], None]):
        super().__init__(master, fg_color="#000000", corner_radius=0)
        self._on_complete = on_complete
        self._step = 0
        self._allow_continue = True
        self._username_value = (read_config().get("user") or "").strip()
        self._discord_value = ""
        self._notify_vars = {k: ctk.BooleanVar(value=False) for k in _NOTIFY_KEYS}

        self._welcome_screen = ctk.CTkFrame(self, fg_color="#000000", corner_radius=0)
        self._welcome_screen.grid(row=0, column=0, rowspan=2, sticky="nsew")

        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_columnconfigure(0, weight=1)

        self._content = ctk.CTkFrame(self, fg_color="#000000", corner_radius=0)
        self._content.grid(row=0, column=0, sticky="nsew", padx=_PADX)
        self._content.grid_rowconfigure(0, weight=1)
        self._content.grid_columnconfigure(0, weight=1)

        self._nav = ctk.CTkFrame(self, fg_color="#000000", corner_radius=0)
        self._nav.grid(row=1, column=0, sticky="ew", padx=_PADX, pady=(0, 8))
        self._nav.grid_columnconfigure(0, weight=1)
        self._nav.grid_columnconfigure(1, weight=0)
        self._nav.grid_columnconfigure(2, weight=1)

        self._actions = ctk.CTkFrame(self._nav, fg_color="transparent")
        self._actions.grid(row=0, column=1)
        self._actions.grid_columnconfigure(0, weight=0)
        self._actions.grid_columnconfigure(1, weight=0)

        self._step_username = ctk.CTkFrame(self._content, fg_color="#000000", corner_radius=0)
        self._step_discord = ctk.CTkFrame(self._content, fg_color="#000000", corner_radius=0)
        self._step_notifications = ctk.CTkFrame(self._content, fg_color="#000000", corner_radius=0)
        self._step_frames = (self._step_username, self._step_discord, self._step_notifications)
        for frame in self._step_frames:
            frame.grid(row=0, column=0, sticky="nsew")
            frame.grid_remove()

        self._build_welcome_step()
        self._build_username_step()
        self._build_discord_step()
        self._build_notifications_step()

        nav_btn = {**_BTN, "width": _BTN_NAV_WIDTH, **_BTN_GRAY}
        self._back = ctk.CTkButton(self._actions, text="Back", command=self._on_back, **nav_btn)
        self._back.grid(row=0, column=0, padx=(0, _BTN_NAV_GAP), sticky="e")

        self._next = ctk.CTkButton(self._actions, text="", command=self._on_next, **nav_btn)
        self._next.grid(row=0, column=1, padx=(_BTN_NAV_GAP, 0), sticky="w")

        self._show_step()

    @staticmethod
    def _bind_wrap_sync(header: ctk.CTkFrame, *labels: ctk.CTkLabel) -> None:
        def _sync_wrap(_event=None) -> None:
            wrap = max(header.winfo_width() - 8, 1)
            for label in labels:
                label.configure(wraplength=wrap)

        header.bind("<Configure>", _sync_wrap)
        header.after_idle(_sync_wrap)

    def _focus_later(self, widget: ctk.CTkBaseClass) -> None:
        self.after(50, widget.focus_set)

    def _on_return(self, _event=None) -> None:
        self._on_next()

    def _add_entry(
        self,
        parent: ctk.CTkFrame,
        *,
        on_change: Callable | None = None,
        digits_only: bool = False,
    ) -> ctk.CTkEntry:
        entry = ctk.CTkEntry(parent, **_ENTRY)
        entry.bind("<Return>", self._on_return)
        if on_change is not None:
            entry.bind("<KeyRelease>", on_change)
        if digits_only:
            bind_digits_only_entry(entry)
        entry.pack(fill="x", pady=(0, 2), padx=5)
        return entry

    def _build_welcome_step(self) -> None:
        self._welcome_screen.pack_propagate(False)

        welcome_top = ctk.CTkLabel(
            self._welcome_screen,
            text="Welcome!",
            font=("Arial", 18, "bold"),
            anchor="center",
            justify="center",
        )
        welcome_mid = ctk.CTkLabel(
            self._welcome_screen,
            text="to",
            font=("Arial", 13),
            text_color=_DESC_COLOR,
            anchor="center",
            justify="center",
        )
        welcome_bottom = ctk.CTkLabel(
            self._welcome_screen,
            text="Eder Lindeberg Games Studio",
            font=("Arial", 20, "bold"),
            anchor="center",
            justify="center",
        )
        welcome_top.pack(fill="x", pady=(12, 0))
        welcome_mid.pack(fill="x")
        welcome_bottom.pack(fill="x")

        self._welcome_next = ctk.CTkButton(
            self._welcome_screen,
            text="Let's go!",
            command=self._advance_from_welcome,
            **_BTN_GRAY,
            width=_BTN_WELCOME_WIDTH,
            height=30,
            font=("Arial", 18, "bold"),
            corner_radius=8,
        )
        self._welcome_next.pack(pady=(16, 0))

    def _build_username_step(self) -> None:
        self._step_username.pack_propagate(False)

        header = ctk.CTkFrame(self._step_username, fg_color="transparent")
        header.pack(fill="x", anchor="n")

        self._username_title = ctk.CTkLabel(
            header,
            text="Username",
            font=_FONT_TITLE,
            anchor="center",
            justify="center",
        )
        self._username_title.pack(pady=2)

        username_desc = ctk.CTkLabel(
            header,
            text="\nEnter your (unique) name.",
            font=_FONT_DESC,
            text_color=_DESC_COLOR,
            anchor="center",
            justify="center",
        )
        username_desc.pack(pady=(0, 6))
        self._bind_wrap_sync(header, self._username_title, username_desc)

        self._username_entry = self._add_entry(self._step_username)

    def _build_discord_step(self) -> None:
        self._step_discord.pack_propagate(False)

        header = ctk.CTkFrame(self._step_discord, fg_color="transparent")
        header.pack(fill="x", anchor="n")

        discord_title = ctk.CTkLabel(
            header,
            text="Discord",
            font=_FONT_TITLE,
            anchor="center",
            justify="center",
        )
        discord_title.pack(pady=2)

        discord_desc = ctk.CTkLabel(
            header,
            text="Enter your Discord user-ID to receive\nDM-notifications for events of your choosing.",
            font=_FONT_DESC,
            text_color=_DESC_COLOR,
            anchor="center",
            justify="center",
        )
        discord_desc.pack(pady=(0, 6))
        self._bind_wrap_sync(header, discord_title, discord_desc)

        self._discord_entry = self._add_entry(
            self._step_discord,
            on_change=self._on_discord_change,
            digits_only=True,
        )

    def _build_notifications_step(self) -> None:
        body = ctk.CTkFrame(self._step_notifications, fg_color="transparent")
        body.pack(anchor="n", fill="x")

        header = ctk.CTkFrame(body, fg_color="transparent")
        header.pack(fill="x", anchor="n")

        self._notifications_title = ctk.CTkLabel(
            header,
            text="Notifications",
            font=_FONT_TITLE,
            anchor="center",
            justify="center",
        )
        self._notifications_title.pack(pady=(2, 0))

        notifications_desc = ctk.CTkLabel(
            header,
            text="Select which events will notify you via Discord DM:s.",
            font=_FONT_DESC,
            text_color=_DESC_COLOR,
            anchor="center",
            justify="center",
        )
        notifications_desc.pack()
        self._bind_wrap_sync(header, self._notifications_title, notifications_desc)

        list_wrap = ctk.CTkFrame(body, fg_color="transparent")
        list_wrap.pack(anchor="n", pady=(4, 0))

        notify_labels = (
            ("notify_others_start", "Team member clocks in"),
            ("notify_others_end", "Team member clocks out"),
            ("notify_own_start", "You clock in"),
            ("notify_own_end", "You clock out"),
        )
        for key, label in notify_labels:
            ctk.CTkCheckBox(
                list_wrap,
                text=label,
                variable=self._notify_vars[key],
                font=_FONT_SMALL,
                height=18,
                checkbox_width=14,
                checkbox_height=14,
                fg_color=ACCENT,
                hover_color=ACCENT,
                border_color=ACCENT,
                checkmark_color="#1E1E1E",
            ).pack(anchor="w")

    def set_continue_enabled(self, enabled: bool) -> None:
        self._allow_continue = bool(enabled)
        state = "normal" if enabled else "disabled"
        self._back.configure(state=state)
        self._next.configure(state=state)
        self._welcome_next.configure(state=state)

    def _activate_step_frame(self, index: int) -> None:
        for i, frame in enumerate(self._step_frames):
            (frame.grid if i == index else frame.grid_remove)()

    def _show_form_shell(self) -> None:
        self._welcome_screen.grid_remove()
        self._content.grid()
        self._nav.grid()
        self._back.grid()

    def _restore_entry(self, entry: ctk.CTkEntry, value: str) -> None:
        entry.delete(0, "end")
        if value:
            entry.insert(0, value)

    def _show_step(self) -> None:
        if self._step == 0:
            self._welcome_screen.grid()
            self._content.grid_remove()
            self._nav.grid_remove()
            return

        self._show_form_shell()

        if self._step == 1:
            self._activate_step_frame(0)
            self._restore_entry(self._username_entry, self._username_value)
            self._next.configure(text="Continue")
            self._focus_later(self._username_entry)
        elif self._step == 2:
            self._activate_step_frame(1)
            self._restore_entry(self._discord_entry, self._discord_value)
            self._on_discord_change()
            self._focus_later(self._discord_entry)
        else:
            self._activate_step_frame(2)
            self._next.configure(text="Finish")

    def _advance_from_welcome(self) -> None:
        if not self._allow_continue:
            return
        self._step = 1
        self._show_step()

    def _on_discord_change(self, _event=None) -> None:
        if self._step != 2:
            return
        self._discord_value = self._discord_entry.get().strip()
        self._next.configure(text="Continue" if self._discord_value else "Skip")

    def _on_back(self) -> None:
        if self._step == 2:
            self._discord_value = self._discord_entry.get().strip()
        if self._step > 0:
            self._step -= 1
            self._show_step()

    def _on_next(self) -> None:
        if not self._allow_continue:
            return
        if self._step == 0:
            self._advance_from_welcome()
            return

        if self._step == 1:
            name = self._username_entry.get().strip()
            if not name:
                flash_error(self._username_title)
                return
            self._username_value = name
            self._step = 2
            self._show_step()
            return

        if self._step == 2:
            self._discord_value = self._discord_entry.get().strip()
            if not self._discord_value:
                self._submit()
                return
            self._step = 3
            self._show_step()
            return

        self._submit()

    def _flash_submit_error(self) -> None:
        if self._step == 3:
            flash_error(self._notifications_title)
        else:
            flash_error(self._discord_entry)

    def _submit(self) -> None:
        username = self._username_value
        discord = self._discord_value or None
        flags = {k: bool(v.get()) for k, v in self._notify_vars.items()}
        notify_on = bool(discord) and any(flags.values())
        notify_flags = {k: flags[k] if discord else False for k in _NOTIFY_KEYS}

        try:
            config = read_config()
        except OSError:
            self._flash_submit_error()
            return

        config["user"] = username
        try:
            write_config(config)
        except OSError:
            self._flash_submit_error()
            return

        try:
            save_notification_prefs(
                username,
                notifications_enabled=notify_on,
                discord_user_id=discord,
                **notify_flags,
            )
        except Exception:
            self._flash_submit_error()
            return

        prefs = app_preferences_from_config(config)
        apply_startup_registration(
            enabled=prefs["launch_at_startup"],
            minimized=prefs["launch_minimized_to_tray"],
        )
        self._on_complete()
