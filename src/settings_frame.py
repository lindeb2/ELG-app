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
    BOX_GAP,
    DISCARD_BORDER,
    DISCARD_TEXT,
    ROW_PADX,
    ROW_PADY,
    CHILD_ROW_PADY,
    SettingsAccountFieldRow,
    SettingsDropdownRow,
    SettingsExpandableGroup,
    SettingsGroup,
    SettingsSwitchRow,
    TEXT_MUTED,
    FONT_MUTED,
    FONT_ROW,
    _apply_nested_checkbox_style,
    _make_outlined_action_button,
    _make_switch,
)
from update_dialog import show_update_dialog
from utils import flash_error

_STARTUP_VIEW_LABELS = {"timetable": "Timetable", "statistics": "Statistics"}
_STARTUP_VIEW_VALUES = {label: key for key, label in _STARTUP_VIEW_LABELS.items()}


class SettingsFrame(ctk.CTkFrame):
    def __init__(self, parent, shell=None):
        super().__init__(parent, fg_color="transparent")
        self._shell = shell
        self._save_after_id: str | None = None
        self._loading = False
        self._updates_group: SettingsExpandableGroup | None = None
        self._current_version_label: ctk.CTkLabel | None = None
        self._update_status_row: ctk.CTkFrame | None = None
        self._update_status_persist = False
        self._update_status_message = ""
        self._update_status_color = TEXT_MUTED
        self._update_status_show_install = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = CtkSmartScrollableFrame(
            self,
            fg_color="transparent",
            bg_color="#000000",
            reserve_scrollbar_space=True,
        )
        scroll.grid(row=0, column=0, sticky="nsew", padx=(12, 1), pady=(12,6))
        scroll.grid_columnconfigure(0, weight=1)

        row = 0

        username_group = SettingsGroup(scroll)
        self._username_row = SettingsAccountFieldRow(username_group.surface, "Username", editable=False)
        username_group.add_row(self._username_row)
        row = self._place_box(scroll, row, username_group)

        discord_group = SettingsGroup(scroll)
        self._discord_row = SettingsAccountFieldRow(
            discord_group.surface,
            "Discord",
            editable=True,
            placeholder="Linked via /link in Discord",
        )
        discord_group.add_row(self._discord_row)
        row = self._place_box(scroll, row, discord_group)

        self._notifications_enabled_var = ctk.BooleanVar(value=True)
        self._notifications_group = SettingsExpandableGroup(
            scroll,
            "Notifications",
            toggle_var=self._notifications_enabled_var,
            on_toggle=self._sync_notification_children,
            on_expand=self._sync_notification_children,
        )
        row = self._place_box(scroll, row, self._notifications_group)

        self._notify_vars = {
            "notify_others_start": ctk.BooleanVar(value=True),
            "notify_others_end": ctk.BooleanVar(value=True),
            "notify_own_start": ctk.BooleanVar(value=False),
            "notify_own_end": ctk.BooleanVar(value=False),
        }
        self._notify_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self._notifications_group.build_body(self._build_notification_body)

        self._startup_enabled_var = ctk.BooleanVar(value=False)
        self._startup_group = SettingsExpandableGroup(
            scroll,
            "Startup",
            toggle_var=self._startup_enabled_var,
            on_toggle=self._sync_startup_children,
            on_expand=self._sync_startup_children,
        )
        row = self._place_box(scroll, row, self._startup_group)

        self._launch_minimized_var = ctk.BooleanVar(value=False)
        self._launch_minimized_cb: ctk.CTkCheckBox | None = None
        self._startup_group.build_body(self._build_startup_body)

        self._startup_view_var = ctk.StringVar(value="Timetable")
        startup_view_group = SettingsGroup(scroll)
        self._startup_view_row = SettingsDropdownRow(
            startup_view_group.surface,
            "Start view",
            self._startup_view_var,
            list(_STARTUP_VIEW_LABELS.values()),
        )
        startup_view_group.add_row(self._startup_view_row)
        row = self._place_box(scroll, row, startup_view_group)

        self._ctrl_r_reload_var = ctk.BooleanVar(value=False)
        reload_group = SettingsGroup(scroll)
        self._ctrl_r_row = SettingsSwitchRow(
            reload_group.surface,
            f"Reload with {primary_modifier_label()} + R",
            self._ctrl_r_reload_var,
            command=self._schedule_save,
        )
        reload_group.add_row(self._ctrl_r_row)
        row = self._place_box(scroll, row, reload_group)

        self._updates_group = SettingsExpandableGroup(
            scroll,
            "Updates",
            start_expanded=False,
            on_expand=self._on_updates_expand,
            on_collapse=self._on_updates_collapse,
        )
        row = self._place_box(scroll, row, self._updates_group)

        self._include_prereleases_var = ctk.BooleanVar(value=False)
        self._last_checked_label: ctk.CTkLabel | None = None
        self._check_updates_btn: ctk.CTkButton | None = None
        self._updates_group.build_body(self._build_updates_body)

        discard_group = SettingsGroup(scroll, border_color=DISCARD_BORDER, border_width=2)
        discard_row = ctk.CTkFrame(discard_group.surface, fg_color="transparent")
        ctk.CTkLabel(
            discard_row,
            text="Discard session",
            font=FONT_ROW,
            anchor="w",
        ).pack(side="left", anchor="w", padx=(ROW_PADX, 0), pady=ROW_PADY)
        self._discard_btn = _make_outlined_action_button(
            discard_row,
            "Discard",
            text_color=DISCARD_TEXT,
            text_color_disabled=TEXT_MUTED,
            command=self._discard_session,
        )
        self._discard_btn.pack(side="right", padx=(0, ROW_PADX), pady=ROW_PADY)
        discard_group.add_row(discard_row)
        row = self._place_box(scroll, row, discard_group)

        self._load_config()
        self._bind_auto_save()
        self._bind_click_away_focus(self)

    def _place_box(self, scroll: ctk.CTkBaseClass, row: int, widget: ctk.CTkBaseClass) -> int:
        widget.grid(row=row, column=0, sticky="ew", pady=(0, BOX_GAP))
        return row + 1

    def _build_notification_body(self, group: SettingsExpandableGroup) -> list[ctk.CTkBaseClass]:
        labels = {
            "notify_others_start": "Team member clocking in",
            "notify_others_end": "Team member clocking out",
            "notify_own_start": "You clocking in",
            "notify_own_end": "You clocking out",
        }
        widgets: list[ctk.CTkBaseClass] = []
        for key, label in labels.items():
            row = ctk.CTkFrame(group.surface, fg_color="transparent")
            cb = ctk.CTkCheckBox(
                row,
                text=label,
                variable=self._notify_vars[key],
                font=FONT_ROW,
                fg_color=ACCENT,
                hover_color=ACCENT,
                border_color=ACCENT,
                checkmark_color="#1E1E1E",
                command=self._schedule_save,
            )
            cb.pack(anchor="w", padx=(ROW_PADX + 12, ROW_PADX), pady=CHILD_ROW_PADY)
            group.add_child_row(row)
            self._notify_checkboxes[key] = cb
            widgets.append(row)
        return widgets

    def _build_startup_body(self, group: SettingsExpandableGroup) -> list[ctk.CTkBaseClass]:
        row = ctk.CTkFrame(group.surface, fg_color="transparent")
        self._launch_minimized_cb = ctk.CTkCheckBox(
            row,
            text="Launch app minimized to system tray",
            variable=self._launch_minimized_var,
            font=FONT_ROW,
            fg_color=ACCENT,
            hover_color=ACCENT,
            border_color=ACCENT,
            checkmark_color="#1E1E1E",
            command=self._schedule_save,
        )
        self._launch_minimized_cb.pack(anchor="w", padx=(ROW_PADX + 12, ROW_PADX), pady=CHILD_ROW_PADY)
        group.add_child_row(row)
        return [row]

    def _build_updates_body(self, group: SettingsExpandableGroup) -> list[ctk.CTkBaseClass]:
        widgets: list[ctk.CTkBaseClass] = []

        version_row = ctk.CTkFrame(group.surface, fg_color="transparent")
        ctk.CTkLabel(
            version_row,
            text="Current version",
            font=FONT_ROW,
            anchor="w",
        ).pack(side="left", anchor="w", padx=(ROW_PADX + 12, 0), pady=CHILD_ROW_PADY)
        self._current_version_label = ctk.CTkLabel(
            version_row,
            text=current_version(),
            font=FONT_ROW,
            anchor="e",
            text_color=TEXT_MUTED,
        )
        self._current_version_label.pack(side="right", padx=(0, ROW_PADX), pady=CHILD_ROW_PADY)
        group.add_child_row(version_row)
        widgets.append(version_row)

        prereq_row = ctk.CTkFrame(group.surface, fg_color="transparent")
        ctk.CTkLabel(prereq_row, text="Include pre-releases", font=FONT_ROW, anchor="w").pack(
            side="left", anchor="w", padx=(ROW_PADX + 12, 0), pady=CHILD_ROW_PADY
        )
        _make_switch(
            prereq_row,
            self._include_prereleases_var,
            command=self._schedule_save,
        ).pack(side="right", padx=(0, ROW_PADX), pady=CHILD_ROW_PADY)
        group.add_child_row(prereq_row)
        widgets.append(prereq_row)

        last_row = ctk.CTkFrame(group.surface, fg_color="transparent")
        self._last_checked_label = ctk.CTkLabel(
            last_row,
            text=self._last_checked_text(),
            font=FONT_MUTED,
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self._last_checked_label.pack(side="left", anchor="w", padx=(ROW_PADX + 12, 0), pady=CHILD_ROW_PADY)

        self._check_updates_btn = _make_outlined_action_button(
            last_row,
            "Check for updates",
            command=self._check_for_updates,
        )
        self._check_updates_btn.pack(side="right", padx=(0, ROW_PADX), pady=CHILD_ROW_PADY)
        group.add_child_row(last_row)
        widgets.append(last_row)
        return widgets

    def _last_checked_text(self) -> str:
        app_prefs = app_preferences_from_config(read_config())
        return f"Last checked: {format_last_checked(app_prefs.get('last_update_check_at'))}"

    def _refresh_last_checked_label(self) -> None:
        if self._last_checked_label is not None:
            self._last_checked_label.configure(text=self._last_checked_text())

    def _on_updates_expand(self) -> None:
        self._refresh_last_checked_label()
        if self._update_status_persist and self._update_status_message:
            self._render_update_status_row()

    def _on_updates_collapse(self) -> None:
        self._remove_update_status_row()
        if not self._update_status_persist:
            self._update_status_message = ""
            self._update_status_show_install = False

    def on_leave_view(self) -> None:
        if not self._update_status_persist:
            self._update_status_message = ""
            self._update_status_show_install = False
        self._remove_update_status_row()

    def _set_update_status(
        self,
        message: str,
        color: str,
        *,
        persist: bool = False,
        show_install: bool = False,
    ) -> None:
        self._update_status_message = message
        self._update_status_color = color
        self._update_status_persist = persist
        self._update_status_show_install = show_install
        if self._updates_group is not None and self._updates_group.is_expanded:
            self._render_update_status_row()

    def _render_update_status_row(self) -> None:
        if self._updates_group is None or not self._updates_group.is_expanded:
            return
        if self._update_status_row is not None:
            self._updates_group.remove_child_row(self._update_status_row)
            self._update_status_row = None
        if not self._update_status_message:
            return

        row = ctk.CTkFrame(self._updates_group.surface, fg_color="transparent")
        ctk.CTkLabel(
            row,
            text=self._update_status_message,
            font=FONT_ROW,
            text_color=self._update_status_color,
            anchor="w",
        ).pack(side="left", anchor="w", padx=(ROW_PADX + 12, 0), pady=CHILD_ROW_PADY)
        if self._update_status_show_install:
            _make_outlined_action_button(
                row,
                "Install update",
                command=self._install_pending_update,
            ).pack(side="right", padx=(0, ROW_PADX), pady=CHILD_ROW_PADY)
        self._updates_group.append_child_row(row)
        self._update_status_row = row

    def _remove_update_status_row(self) -> None:
        if self._update_status_row is None or self._updates_group is None:
            self._update_status_row = None
            return
        try:
            if self._update_status_row.winfo_exists():
                self._updates_group.remove_child_row(self._update_status_row)
        except Exception:
            pass
        self._update_status_row = None

    def _clear_transient_update_status(self) -> None:
        if self._update_status_persist:
            return
        self._update_status_message = ""
        self._update_status_show_install = False
        self._remove_update_status_row()

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
        if not confirm_discard_session(self, timetable):
            return
        self._shell.discard_timetable_session()
        self.refresh_session_controls()

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

            self._refresh_last_checked_label()

            if self._current_version_label is not None:
                self._current_version_label.configure(text=current_version())

            self._sync_notification_children()
            self._sync_startup_children()
            self.refresh_update_controls()
            self.refresh_session_controls()
        finally:
            self._loading = False

    def refresh_update_controls(self) -> None:
        release = load_pending_update()
        if release is not None:
            self._set_update_status(
                f"Update {release.version} is ready to install.",
                TEXT_MUTED,
                persist=True,
                show_install=True,
            )
            return
        if not self._update_status_show_install:
            self._restore_persistent_update_status()
            return
        self._update_status_show_install = False
        if "ready to install" not in self._update_status_message.lower():
            self._restore_persistent_update_status()
            return
        self._update_status_persist = False
        self._update_status_message = ""
        self._remove_update_status_row()
        self._restore_persistent_update_status()

    def _restore_persistent_update_status(self) -> None:
        if (
            self._update_status_persist
            and self._update_status_message
            and self._updates_group is not None
            and self._updates_group.is_expanded
        ):
            self._render_update_status_row()

    def _install_pending_update(self) -> None:
        release = load_pending_update()
        if release is None:
            self.refresh_update_controls()
            self._set_update_status("No pending update.", "#FF4444", persist=False)
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
            self._set_update_status("Update checks are unavailable.", "#FF4444", persist=False)
            return

        self._set_update_status("Checking for updates…", TEXT_MUTED, persist=False)

        def on_status(message: str, color: str) -> None:
            message_lower = message.lower()
            is_error = color == "#FF4444"
            is_up_to_date = "up to date" in message_lower
            persist = not is_error and not is_up_to_date
            self._set_update_status(message, color, persist=persist, show_install=False)
            self._refresh_last_checked_label()
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
            print(f"Could not save notification settings: {exc}")
            flash_error(self._discord_row.entry)
            return
