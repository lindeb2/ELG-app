"""Settings view embedded in the app shell."""
from __future__ import annotations

import customtkinter as ctk

from CtkSmartScrollableFrame import CtkSmartScrollableFrame
from app_config import (
    apply_startup_registration,
    app_preferences_from_config,
    merge_app_preferences,
    read_config,
    write_config,
)
from app_update import format_last_checked, load_pending_update
from app_version import current_version
from notification_preferences import (
    DEFAULT_NOTIFICATION_PREFS,
    fetch_notification_prefs,
    save_notification_prefs,
)
from platform_keys import primary_modifier_label
from session_guard import confirm_discard_session, has_unlogged_time
from settings_ui import (
    ACCENT,
    ROW_PADX,
    SettingsAccountFieldRow,
    SettingsCard,
    SettingsDropdownRow,
    SettingsExpandableRow,
    SettingsSectionHeader,
    SettingsSwitchRow,
    TEXT_MUTED,
    FONT_MUTED,
    FONT_ROW,
    _apply_nested_checkbox_style,
    _make_switch,
)
from update_dialog import show_update_dialog

_STARTUP_VIEW_LABELS = {"timetable": "Timetable", "statistics": "Statistics"}
_STARTUP_VIEW_VALUES = {label: key for key, label in _STARTUP_VIEW_LABELS.items()}


class SettingsFrame(ctk.CTkFrame):
    def __init__(self, parent, shell=None):
        super().__init__(parent, fg_color="transparent")
        self._shell = shell
        self._save_after_id: str | None = None
        self._loading = False
        self._version_title_label: ctk.CTkLabel | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text="Settings", font=("Segoe UI", 28, "bold")).grid(
            row=0, column=0, padx=24, pady=(24, 16), sticky="w"
        )

        scroll = CtkSmartScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 12))
        scroll.grid_columnconfigure(0, weight=1)

        row = 0

        SettingsSectionHeader(scroll, text="Account").grid(
            row=row, column=0, padx=4, pady=(0, 6), sticky="w"
        )
        row += 1

        account_card = SettingsCard(scroll)
        account_card.grid(row=row, column=0, sticky="ew", pady=(0, 20))
        row += 1

        self._username_row = SettingsAccountFieldRow(account_card, "Username", editable=False)
        account_card.add_row(self._username_row)
        account_card.add_separator()

        self._discord_row = SettingsAccountFieldRow(
            account_card,
            "Discord",
            editable=True,
            placeholder="Linked via /link in Discord",
        )
        account_card.add_row(self._discord_row)
        account_card.add_separator()

        self._notifications_enabled_var = ctk.BooleanVar(value=True)
        self._notifications_expand = SettingsExpandableRow(
            account_card,
            "Notifications",
            toggle_var=self._notifications_enabled_var,
            on_toggle=self._sync_notification_children,
        )
        account_card.add_row(self._notifications_expand)

        self._notify_vars = {
            "notify_others_start": ctk.BooleanVar(value=True),
            "notify_others_end": ctk.BooleanVar(value=True),
            "notify_own_start": ctk.BooleanVar(value=False),
            "notify_own_end": ctk.BooleanVar(value=False),
        }
        self._notify_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self._notifications_expand.build_body(self._build_notification_body)

        SettingsSectionHeader(scroll, text="App").grid(
            row=row, column=0, padx=4, pady=(0, 6), sticky="w"
        )
        row += 1

        app_card = SettingsCard(scroll)
        app_card.grid(row=row, column=0, sticky="ew", pady=(0, 20))
        row += 1

        self._startup_enabled_var = ctk.BooleanVar(value=False)
        self._startup_expand = SettingsExpandableRow(
            app_card,
            "Startup",
            toggle_var=self._startup_enabled_var,
            on_toggle=self._sync_startup_children,
        )
        app_card.add_row(self._startup_expand)
        app_card.add_separator()

        self._launch_minimized_var = ctk.BooleanVar(value=False)
        self._launch_minimized_cb: ctk.CTkCheckBox | None = None
        self._startup_expand.build_body(self._build_startup_body)

        self._startup_view_var = ctk.StringVar(value="Timetable")
        self._startup_view_row = SettingsDropdownRow(
            app_card,
            "Start view",
            self._startup_view_var,
            list(_STARTUP_VIEW_LABELS.values()),
        )
        app_card.add_row(self._startup_view_row)
        app_card.add_separator()

        self._ctrl_r_reload_var = ctk.BooleanVar(value=False)
        self._ctrl_r_row = SettingsSwitchRow(
            app_card,
            f"Reload with {primary_modifier_label()} + R",
            self._ctrl_r_reload_var,
            command=self._schedule_save,
        )
        app_card.add_row(self._ctrl_r_row)

        SettingsSectionHeader(scroll, text="Updates").grid(
            row=row, column=0, padx=4, pady=(0, 6), sticky="w"
        )
        row += 1

        self._pending_card = SettingsCard(scroll)
        self._pending_card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        row += 1

        pending_row = ctk.CTkFrame(self._pending_card, fg_color="transparent")
        pending_row.grid_columnconfigure(0, weight=1)
        self._pending_label = ctk.CTkLabel(
            pending_row,
            text="",
            font=FONT_ROW,
            anchor="w",
        )
        self._pending_label.grid(row=0, column=0, sticky="w", padx=ROW_PADX, pady=10)
        self._install_update_btn = ctk.CTkButton(
            pending_row,
            text="Install update",
            width=120,
            command=self._install_pending_update,
        )
        self._install_update_btn.grid(row=0, column=1, padx=(0, ROW_PADX), pady=10)
        self._pending_card.add_row(pending_row)
        self._pending_card.grid_remove()

        updates_card = SettingsCard(scroll)
        updates_card.grid(row=row, column=0, sticky="ew", pady=(0, 20))
        row += 1

        self._version_expand = SettingsExpandableRow(
            updates_card,
            current_version(),
            start_expanded=False,
        )
        updates_card.add_row(self._version_expand)
        self._version_title_label = self._version_expand.title_label

        self._include_prereleases_var = ctk.BooleanVar(value=False)
        self._last_checked_label: ctk.CTkLabel | None = None
        self._check_updates_btn: ctk.CTkButton | None = None
        self._version_expand.build_body(self._build_updates_body)

        self._discard_btn = ctk.CTkButton(
            scroll,
            text="Discard current session",
            width=170,
            fg_color="#5A1A1A",
            hover_color="#7A2020",
            command=self._discard_session,
        )
        self._discard_btn.grid(row=row, column=0, padx=4, pady=(0, 8))
        row += 1

        self._status_label = ctk.CTkLabel(scroll, text="", font=FONT_MUTED, text_color=TEXT_MUTED)
        self._status_label.grid(row=row, column=0, padx=4, pady=(0, 12), sticky="w")

        self._load_config()
        self._bind_auto_save()
        self._bind_click_away_focus(self)

    def _build_notification_body(self, parent: ctk.CTkFrame) -> list[ctk.CTkBaseClass]:
        labels = {
            "notify_others_start": "Team member clocking in",
            "notify_others_end": "Team member clocking out",
            "notify_own_start": "You clocking in",
            "notify_own_end": "You clocking out",
        }
        widgets: list[ctk.CTkBaseClass] = []
        for key, label in labels.items():
            cb = ctk.CTkCheckBox(
                parent,
                text=label,
                variable=self._notify_vars[key],
                font=FONT_ROW,
                fg_color=ACCENT,
                hover_color=ACCENT,
                border_color=ACCENT,
                checkmark_color="#1E1E1E",
                command=self._schedule_save,
            )
            cb.pack(anchor="w", padx=(ROW_PADX + 12, ROW_PADX), pady=4)
            self._notify_checkboxes[key] = cb
            widgets.append(cb)
        return widgets

    def _build_startup_body(self, parent: ctk.CTkFrame) -> list[ctk.CTkBaseClass]:
        self._launch_minimized_cb = ctk.CTkCheckBox(
            parent,
            text="Launch app minimized to system tray",
            variable=self._launch_minimized_var,
            font=FONT_ROW,
            fg_color=ACCENT,
            hover_color=ACCENT,
            border_color=ACCENT,
            checkmark_color="#1E1E1E",
            command=self._schedule_save,
        )
        self._launch_minimized_cb.pack(anchor="w", padx=(ROW_PADX + 12, ROW_PADX), pady=4)
        return [self._launch_minimized_cb]

    def _build_updates_body(self, parent: ctk.CTkFrame) -> list[ctk.CTkBaseClass]:
        widgets: list[ctk.CTkBaseClass] = []

        prereq_row = ctk.CTkFrame(parent, fg_color="transparent")
        prereq_row.pack(fill="x", padx=ROW_PADX, pady=(4, 0))
        ctk.CTkLabel(prereq_row, text="Include pre-releases", font=FONT_ROW, anchor="w").pack(
            side="left", anchor="w"
        )
        prereq_switch = _make_switch(
            prereq_row,
            self._include_prereleases_var,
            command=self._schedule_save,
        )
        prereq_switch.pack(side="right")
        widgets.extend((prereq_row, prereq_switch))

        last_row = ctk.CTkFrame(parent, fg_color="transparent")
        last_row.pack(fill="x", padx=ROW_PADX, pady=(8, 8))
        self._last_checked_label = ctk.CTkLabel(
            last_row,
            text="Last checked: Never",
            font=FONT_MUTED,
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self._last_checked_label.pack(side="left", anchor="w")

        self._check_updates_btn = ctk.CTkButton(
            last_row,
            text="Check for updates",
            width=140,
            height=28,
            fg_color="transparent",
            hover_color="#3A3A3A",
            border_width=1,
            border_color="#555555",
            text_color="#FFFFFF",
            command=self._check_for_updates,
        )
        self._check_updates_btn.pack(side="right", anchor="e")
        widgets.extend((last_row, self._check_updates_btn))
        return widgets

    def _bind_auto_save(self) -> None:
        for var in (
            self._notifications_enabled_var,
            self._startup_enabled_var,
            self._launch_minimized_var,
            self._ctrl_r_reload_var,
            self._include_prereleases_var,
            *self._notify_vars.values(),
        ):
            var.trace_add("write", lambda *_args: self._schedule_save())

        self._startup_view_var.trace_add("write", lambda *_args: self._schedule_save())
        self._discord_row.entry.bind("<FocusOut>", lambda _event: self._schedule_save())
        self._discord_row.entry.bind("<Return>", self._on_discord_return)

    def _on_discord_return(self, _event=None) -> str:
        self.focus_set()
        self._schedule_save()
        return "break"

    def _bind_click_away_focus(self, widget: ctk.CTkBaseClass) -> None:
        if widget is self._discord_row.entry:
            return
        if isinstance(widget, ctk.CTkEntry):
            return

        widget.bind("<Button-1>", lambda _event: self.focus_set(), add="+")
        for child in widget.winfo_children():
            self._bind_click_away_focus(child)

    def _schedule_save(self) -> None:
        if self._loading:
            return
        if self._save_after_id is not None:
            self.after_cancel(self._save_after_id)
        self._save_after_id = self.after(350, self._save)

    def _sync_notification_children(self) -> None:
        enabled = bool(self._notifications_enabled_var.get())
        for cb in self._notify_checkboxes.values():
            _apply_nested_checkbox_style(cb, enabled=enabled)
        if not self._loading:
            self._schedule_save()

    def _sync_startup_children(self) -> None:
        enabled = bool(self._startup_enabled_var.get())
        if self._launch_minimized_cb is not None:
            _apply_nested_checkbox_style(self._launch_minimized_cb, enabled=enabled)
        if not self._loading:
            self._schedule_save()

    def refresh_session_controls(self) -> None:
        timetable = self._shell.get_timetable() if self._shell is not None else None
        has_session = has_unlogged_time(timetable)
        self._discard_btn.configure(state="normal" if has_session else "disabled")

    def _discard_session(self) -> None:
        if self._shell is None:
            return
        timetable = self._shell.get_timetable()
        if not has_unlogged_time(timetable):
            return
        if not confirm_discard_session(self.winfo_toplevel(), timetable):
            return
        self._shell.discard_timetable_session()
        self.refresh_session_controls()
        self._show_status("Session discarded.")

    def _load_config(self) -> None:
        self._loading = True
        try:
            config = read_config()
            app_prefs = app_preferences_from_config(config)
            username = config.get("user") or ""

            self._username_row.set_value(username or "—")

            prefs = fetch_notification_prefs(username)
            self._notifications_enabled_var.set(
                bool(prefs.get("notifications_enabled", DEFAULT_NOTIFICATION_PREFS["notifications_enabled"]))
            )
            for key, var in self._notify_vars.items():
                var.set(bool(prefs.get(key, DEFAULT_NOTIFICATION_PREFS[key])))

            discord_value = str(prefs["discord_user_id"]) if prefs.get("discord_user_id") else ""
            self._discord_row.set_value(discord_value)

            self._startup_enabled_var.set(bool(app_prefs.get("launch_at_startup", False)))
            self._launch_minimized_var.set(bool(app_prefs.get("launch_minimized_to_tray", False)))
            view_key = app_prefs.get("startup_view", "timetable")
            self._startup_view_var.set(_STARTUP_VIEW_LABELS.get(view_key, "Timetable"))
            self._ctrl_r_reload_var.set(bool(app_prefs.get("enable_ctrl_r_reload", False)))
            self._include_prereleases_var.set(bool(app_prefs.get("include_prereleases", False)))

            if self._last_checked_label is not None:
                self._last_checked_label.configure(
                    text=f"Last checked: {format_last_checked(app_prefs.get('last_update_check_at'))}"
                )

            if self._version_title_label is not None:
                self._version_title_label.configure(text=current_version())

            self._sync_notification_children()
            self._sync_startup_children()
            self.refresh_update_controls()
            self.refresh_session_controls()
        finally:
            self._loading = False

    def refresh_update_controls(self) -> None:
        release = load_pending_update()
        if release is None:
            self._pending_card.grid_remove()
            return
        self._pending_label.configure(text=f"Update {release.version} is ready to install.")
        self._pending_card.grid()

    def _install_pending_update(self) -> None:
        release = load_pending_update()
        if release is None:
            self.refresh_update_controls()
            self._show_status("No pending update.", error=True)
            return
        if self._shell is None:
            return
        show_update_dialog(
            self.winfo_toplevel(),
            release,
            shell=self._shell,
            instance_guard=getattr(self._shell, "_instance_guard", None),
        )

    def _check_for_updates(self) -> None:
        if self._shell is None or self._shell._manual_update_check is None:
            self._show_status("Update checks are unavailable.", error=True)
            return

        self._show_status("Checking for updates…")

        def on_status(message: str, color: str) -> None:
            if color == "#FF4444":
                self._show_status(message, error=True)
            else:
                self._show_status(message)
            config = read_config()
            app_prefs = app_preferences_from_config(config)
            if self._last_checked_label is not None:
                self._last_checked_label.configure(
                    text=f"Last checked: {format_last_checked(app_prefs.get('last_update_check_at'))}"
                )
            self.refresh_update_controls()

        self._shell._manual_update_check(on_status)

    def _save(self) -> None:
        self._save_after_id = None
        config = read_config()
        username = (config.get("user") or "").strip()
        if not username:
            return

        existing_prefs = app_preferences_from_config(config)
        startup_view = _STARTUP_VIEW_VALUES.get(self._startup_view_var.get(), "timetable")
        launch_at_startup = bool(self._startup_enabled_var.get())
        launch_minimized = bool(self._launch_minimized_var.get()) if launch_at_startup else False

        app_prefs = {
            "close_action": existing_prefs["close_action"],
            "launch_at_startup": launch_at_startup,
            "launch_minimized_to_tray": launch_minimized,
            "startup_view": startup_view,
            "enable_ctrl_r_reload": bool(self._ctrl_r_reload_var.get()),
            "include_prereleases": bool(self._include_prereleases_var.get()),
            "pending_update": existing_prefs.get("pending_update"),
            "last_update_check_at": existing_prefs.get("last_update_check_at"),
        }

        config = merge_app_preferences(config, app_prefs)
        write_config(config)
        apply_startup_registration(enabled=launch_at_startup, minimized=launch_minimized)

        if self._shell is not None:
            self._shell.set_app_preferences(app_prefs)

        try:
            save_notification_prefs(
                username,
                notifications_enabled=bool(self._notifications_enabled_var.get()),
                notify_others_start=bool(self._notify_vars["notify_others_start"].get()),
                notify_others_end=bool(self._notify_vars["notify_others_end"].get()),
                notify_own_start=bool(self._notify_vars["notify_own_start"].get()),
                notify_own_end=bool(self._notify_vars["notify_own_end"].get()),
                discord_user_id=self._discord_row.entry.get().strip() or None,
            )
        except Exception as exc:
            self._show_status(f"Could not save notification settings: {exc}", error=True)
            return

        self._status_label.configure(text="")

    def _show_status(self, message: str, *, error: bool = False) -> None:
        color = "#FF6B6B" if error else TEXT_MUTED
        self._status_label.configure(text=message, text_color=color)
        if not error:
            self.after(2500, lambda: self._status_label.configure(text=""))
