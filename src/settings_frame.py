"""Settings view embedded in the app shell."""
from __future__ import annotations

import threading

import customtkinter as ctk

from CtkSmartScrollableFrame import CtkSmartScrollableFrame
from app_config import (
    apply_startup_registration,
    app_preferences_from_config,
    merge_app_preferences,
    read_config,
    write_config,
)
from app_secrets import has_discord_bot_token_configured
from app_update import format_last_checked, load_pending_update
from app_version import current_version
from meeting_recorder_setup import (
    HighQualitySetupJob,
    SetupError,
    SetupJob,
    best_quality_ready,
    high_quality_assets_ready,
    is_supported_os,
    models_ready,
    validate_and_store_token,
)
from meeting_recorder_token_dialog import prompt_discord_bot_token
from notification_preferences import (
    DEFAULT_NOTIFICATION_PREFS,
    fetch_notification_prefs,
    save_notification_prefs,
)
from platform_keys import alt_modifier_label, primary_modifier_label
from session_guard import confirm_discard_session, has_unlogged_time
from settings_ui_constants import DISCORD_USER_ID_PLACEHOLDER
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
_CLOSE_ACTION_LABELS = {"tray": "Minimize to system tray", "exit": "Exit app Completely"}
_CLOSE_ACTION_VALUES = {label: key for key, label in _CLOSE_ACTION_LABELS.items()}


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
        self._meeting_recorder_group: SettingsExpandableGroup | None = None
        self._meeting_recorder_status_label: ctk.CTkLabel | None = None
        self._meeting_recorder_token_btn: ctk.CTkButton | None = None
        self._meeting_recorder_status_message = ""
        self._meeting_recorder_setup_job = SetupJob()
        self._high_quality_cb: ctk.CTkCheckBox | None = None
        self._high_quality_status_label: ctk.CTkLabel | None = None
        self._high_quality_status_message = ""
        self._high_quality_setup_job = HighQualitySetupJob()
        self._best_quality_cb: ctk.CTkCheckBox | None = None
        self._best_quality_status_label: ctk.CTkLabel | None = None

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
            "Discord user-ID",
            editable=True,
            placeholder=DISCORD_USER_ID_PLACEHOLDER,
            digits_only=True,
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

        self._close_action_var = ctk.StringVar(value=_CLOSE_ACTION_LABELS["tray"])
        on_close_group = SettingsGroup(scroll)
        self._close_action_row = SettingsDropdownRow(
            on_close_group.surface,
            "On close",
            self._close_action_var,
            list(_CLOSE_ACTION_LABELS.values()),
        )
        on_close_group.add_row(self._close_action_row)
        row = self._place_box(scroll, row, on_close_group)

        self._shortcuts_group = SettingsExpandableGroup(
            scroll,
            "Keyboard shortcuts",
            start_expanded=False,
        )
        row = self._place_box(scroll, row, self._shortcuts_group)
        self._shortcuts_group.build_body(self._build_shortcuts_body)

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

        self._meeting_recorder_enabled_var = ctk.BooleanVar(value=False)
        self._high_quality_enabled_var = ctk.BooleanVar(value=False)
        self._best_quality_enabled_var = ctk.BooleanVar(value=False)
        self._meeting_recorder_group = SettingsExpandableGroup(
            scroll,
            "Meeting Recorder",
            toggle_var=self._meeting_recorder_enabled_var,
            on_toggle=self._on_meeting_recorder_toggle,
            on_expand=self._on_meeting_recorder_expand,
        )
        row = self._place_box(scroll, row, self._meeting_recorder_group)
        self._meeting_recorder_group.build_body(self._build_meeting_recorder_body)

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

    def _build_meeting_recorder_body(self, group: SettingsExpandableGroup) -> list[ctk.CTkBaseClass]:
        widgets: list[ctk.CTkBaseClass] = []

        status_row = ctk.CTkFrame(group.surface, fg_color="transparent")
        self._meeting_recorder_status_label = ctk.CTkLabel(
            status_row,
            text=self._meeting_recorder_status_text(),
            font=FONT_ROW,
            text_color=TEXT_MUTED,
            anchor="w",
            wraplength=320,
            justify="left",
        )
        self._meeting_recorder_status_label.pack(
            side="left", anchor="w", padx=(ROW_PADX + 12, ROW_PADX), pady=CHILD_ROW_PADY
        )
        group.add_child_row(status_row)
        widgets.append(status_row)

        token_row = ctk.CTkFrame(group.surface, fg_color="transparent")
        ctk.CTkLabel(token_row, text="Discord bot token", font=FONT_ROW, anchor="w").pack(
            side="left", anchor="w", padx=(ROW_PADX + 12, 0), pady=CHILD_ROW_PADY
        )
        self._meeting_recorder_token_btn = _make_outlined_action_button(
            token_row,
            "Change token" if has_discord_bot_token_configured() else "Set token",
            command=self._change_meeting_recorder_token,
        )
        self._meeting_recorder_token_btn.pack(side="right", padx=(0, ROW_PADX), pady=CHILD_ROW_PADY)
        group.add_child_row(token_row)
        widgets.append(token_row)

        quality_row = ctk.CTkFrame(group.surface, fg_color="transparent")
        self._high_quality_cb = ctk.CTkCheckBox(
            quality_row,
            text="High-quality mode (adds a slower combined-audio pass)",
            variable=self._high_quality_enabled_var,
            font=FONT_ROW,
            fg_color=ACCENT,
            hover_color=ACCENT,
            border_color=ACCENT,
            checkmark_color="#1E1E1E",
            command=self._on_high_quality_toggle,
        )
        quality_row.grid_columnconfigure(0, weight=1)
        self._high_quality_cb.pack(anchor="w", padx=(ROW_PADX + 12, ROW_PADX), pady=CHILD_ROW_PADY)
        group.add_child_row(quality_row)
        widgets.append(quality_row)

        quality_status_row = ctk.CTkFrame(group.surface, fg_color="transparent")
        self._high_quality_status_label = ctk.CTkLabel(
            quality_status_row,
            text=self._high_quality_status_text(),
            font=FONT_MUTED,
            text_color=TEXT_MUTED,
            anchor="w",
            wraplength=320,
            justify="left",
        )
        self._high_quality_status_label.pack(
            side="left", anchor="w", padx=(ROW_PADX + 12, ROW_PADX), pady=CHILD_ROW_PADY
        )
        group.add_child_row(quality_status_row)
        widgets.append(quality_status_row)

        best_quality_row = ctk.CTkFrame(group.surface, fg_color="transparent")
        self._best_quality_cb = ctk.CTkCheckBox(
            best_quality_row,
            text="Best quality (adds an LLM reconciliation pass)",
            variable=self._best_quality_enabled_var,
            font=FONT_ROW,
            fg_color=ACCENT,
            hover_color=ACCENT,
            border_color=ACCENT,
            checkmark_color="#1E1E1E",
            command=self._on_best_quality_toggle,
        )
        best_quality_row.grid_columnconfigure(0, weight=1)
        self._best_quality_cb.pack(anchor="w", padx=(ROW_PADX + 24, ROW_PADX), pady=CHILD_ROW_PADY)
        group.add_child_row(best_quality_row)
        widgets.append(best_quality_row)

        best_quality_status_row = ctk.CTkFrame(group.surface, fg_color="transparent")
        self._best_quality_status_label = ctk.CTkLabel(
            best_quality_status_row,
            text=self._best_quality_status_text(),
            font=FONT_MUTED,
            text_color=TEXT_MUTED,
            anchor="w",
            wraplength=320,
            justify="left",
        )
        self._best_quality_status_label.pack(
            side="left", anchor="w", padx=(ROW_PADX + 24, ROW_PADX), pady=CHILD_ROW_PADY
        )
        group.add_child_row(best_quality_status_row)
        widgets.append(best_quality_status_row)

        self._sync_meeting_recorder_children()

        return widgets

    def _meeting_recorder_status_text(self) -> str:
        if self._meeting_recorder_setup_job.is_running:
            return self._meeting_recorder_status_message or "Setting up…"
        if not is_supported_os():
            return "Not supported on this OS yet."
        if not self._meeting_recorder_enabled_var.get():
            return "Off — no downloads or dependencies used until enabled."
        if self._meeting_recorder_status_message:
            return self._meeting_recorder_status_message
        if models_ready() and has_discord_bot_token_configured():
            return "Ready."
        return "Not fully configured — toggle off and on to retry setup."

    def _refresh_meeting_recorder_status_label(self) -> None:
        if self._meeting_recorder_status_label is not None:
            self._meeting_recorder_status_label.configure(text=self._meeting_recorder_status_text())

    def _on_meeting_recorder_toggle(self) -> None:
        if self._loading:
            return
        self._sync_meeting_recorder_children()
        if not self._meeting_recorder_enabled_var.get():
            self._meeting_recorder_status_message = ""
            self._refresh_meeting_recorder_status_label()
            self._schedule_save()
            return
        self._start_meeting_recorder_setup()

    def _start_meeting_recorder_setup(self) -> None:
        if not is_supported_os():
            self._meeting_recorder_status_message = "Meeting Recorder isn't supported on this OS yet."
            self._refresh_meeting_recorder_status_label()
            self._meeting_recorder_group.switch.set(False)
            return

        token: str | None = None
        if not has_discord_bot_token_configured():
            token = prompt_discord_bot_token(self)
            if not token:
                self._meeting_recorder_group.switch.set(False)
                return

        if self._meeting_recorder_group.switch is not None:
            self._meeting_recorder_group.switch.configure(state="disabled")
        self._meeting_recorder_status_message = "Setting up…"
        self._refresh_meeting_recorder_status_label()

        def on_status(message: str) -> None:
            self._meeting_recorder_status_message = message
            self._refresh_meeting_recorder_status_label()

        def on_error(exc: Exception) -> None:
            self._meeting_recorder_status_message = f"Setup failed: {exc}"
            self._refresh_meeting_recorder_status_label()
            if self._meeting_recorder_group.switch is not None:
                self._meeting_recorder_group.switch.configure(state="normal")
            self._meeting_recorder_group.switch.set(False)
            if self._meeting_recorder_token_btn is not None:
                self._meeting_recorder_token_btn.configure(
                    text="Change token" if has_discord_bot_token_configured() else "Set token"
                )

        def on_done(_result) -> None:
            self._meeting_recorder_status_message = "Ready."
            self._refresh_meeting_recorder_status_label()
            if self._meeting_recorder_group.switch is not None:
                self._meeting_recorder_group.switch.configure(state="normal")
            if self._meeting_recorder_token_btn is not None:
                self._meeting_recorder_token_btn.configure(text="Change token")
            self._schedule_save()

        self._meeting_recorder_setup_job.start(
            token,
            on_status=lambda msg: self.after(0, on_status, msg),
            on_error=lambda exc: self.after(0, on_error, exc),
            on_done=lambda result: self.after(0, on_done, result),
        )

    def _change_meeting_recorder_token(self) -> None:
        token = prompt_discord_bot_token(self)
        if not token:
            return
        self._meeting_recorder_status_message = "Validating Discord bot token…"
        self._refresh_meeting_recorder_status_label()

        def worker() -> None:
            try:
                validate_and_store_token(token)
            except SetupError as exc:
                self.after(0, self._on_meeting_recorder_token_error, exc)
            else:
                self.after(0, self._on_meeting_recorder_token_done)

        threading.Thread(target=worker, daemon=True, name="meeting_recorder_token_check").start()

    def _on_meeting_recorder_token_error(self, exc: SetupError) -> None:
        self._meeting_recorder_status_message = f"Token not saved: {exc}"
        self._refresh_meeting_recorder_status_label()

    def _on_meeting_recorder_token_done(self) -> None:
        self._meeting_recorder_status_message = "Discord bot token updated."
        self._refresh_meeting_recorder_status_label()
        if self._meeting_recorder_token_btn is not None:
            self._meeting_recorder_token_btn.configure(text="Change token")

    def _on_meeting_recorder_expand(self) -> None:
        self._refresh_meeting_recorder_status_label()
        self._refresh_high_quality_status_label()
        self._refresh_best_quality_status_label()

    def _high_quality_status_text(self) -> str:
        if self._high_quality_setup_job.is_running:
            return self._high_quality_status_message or "Downloading…"
        if not self._high_quality_enabled_var.get():
            return "Off — adds a second, slower transcription pass on a combined mixdown for comparison."
        if self._high_quality_status_message:
            return self._high_quality_status_message
        if high_quality_assets_ready():
            return "Ready."
        return "Not fully downloaded — toggle off and on to retry."

    def _refresh_high_quality_status_label(self) -> None:
        if self._high_quality_status_label is not None:
            self._high_quality_status_label.configure(text=self._high_quality_status_text())

    def _sync_meeting_recorder_children(self) -> None:
        enabled = bool(self._meeting_recorder_enabled_var.get())
        if self._high_quality_cb is not None:
            _apply_nested_checkbox_style(self._high_quality_cb, enabled=enabled)
        self._refresh_high_quality_status_label()
        self._sync_high_quality_children()

    def _best_quality_status_text(self) -> str:
        if not self._high_quality_enabled_var.get():
            return "Requires High-quality mode."
        if not self._best_quality_enabled_var.get():
            return "Off — adds a local-LLM pass that reconciles this transcript with the combined-audio one."
        if not best_quality_ready():
            return "Waiting on High-quality mode's assets to finish downloading."
        return "Ready."

    def _refresh_best_quality_status_label(self) -> None:
        if self._best_quality_status_label is not None:
            self._best_quality_status_label.configure(text=self._best_quality_status_text())

    def _sync_high_quality_children(self) -> None:
        """Best quality is nested one level deeper than High-quality mode
        (see _sync_meeting_recorder_children above for the outer nesting) -
        disabled/styled unless _high_quality_enabled_var is also on, not just
        _meeting_recorder_enabled_var."""
        enabled = bool(self._high_quality_enabled_var.get())
        if self._best_quality_cb is not None:
            _apply_nested_checkbox_style(self._best_quality_cb, enabled=enabled)
        self._refresh_best_quality_status_label()

    def _on_best_quality_toggle(self) -> None:
        """No SetupJob here - unlike the base feature/High-quality mode toggles,
        Step 6 downloads nothing new (see meeting_recorder_setup.best_quality_ready),
        so this just saves the preference directly."""
        if self._loading:
            return
        self._refresh_best_quality_status_label()
        self._schedule_save()

    def _on_high_quality_toggle(self) -> None:
        if self._loading:
            return
        self._sync_high_quality_children()
        if not self._high_quality_enabled_var.get():
            self._high_quality_status_message = ""
            self._refresh_high_quality_status_label()
            self._schedule_save()
            return
        self._start_high_quality_setup()

    def _start_high_quality_setup(self) -> None:
        if not is_supported_os():
            self._high_quality_status_message = "High-quality mode isn't supported on this OS yet."
            self._refresh_high_quality_status_label()
            self._high_quality_enabled_var.set(False)
            return

        if self._high_quality_cb is not None:
            self._high_quality_cb.configure(state="disabled")
        self._high_quality_status_message = "Downloading…"
        self._refresh_high_quality_status_label()

        def on_status(message: str) -> None:
            self._high_quality_status_message = message
            self._refresh_high_quality_status_label()

        def on_error(exc: Exception) -> None:
            self._high_quality_status_message = f"Setup failed: {exc}"
            self._refresh_high_quality_status_label()
            if self._high_quality_cb is not None:
                self._high_quality_cb.configure(state="normal")
            self._high_quality_enabled_var.set(False)

        def on_done(_result) -> None:
            self._high_quality_status_message = "Ready."
            self._refresh_high_quality_status_label()
            if self._high_quality_cb is not None:
                self._high_quality_cb.configure(state="normal")
            self._refresh_best_quality_status_label()
            self._schedule_save()

        self._high_quality_setup_job.start(
            on_status=lambda msg: self.after(0, on_status, msg),
            on_error=lambda exc: self.after(0, on_error, exc),
            on_done=lambda result: self.after(0, on_done, result),
        )

    def _build_shortcuts_body(self, group: SettingsExpandableGroup) -> list[ctk.CTkBaseClass]:
        mod = primary_modifier_label()
        alt = alt_modifier_label()
        shortcuts = [
            ("Toggle sidebar", f"{mod} + B"),
            ("Switch view", f"{mod} + 1-4"),
            ("Settings", f"{mod} + ,"),
            ("Reload active view", f"{mod} + R"),
            ("Enter timetable widget mode", f"{alt} + Up"),
            ("Exit timetable widget mode", f"{alt} + Down"),
        ]
        widgets: list[ctk.CTkBaseClass] = []
        for name, combo in shortcuts:
            shortcut_row = ctk.CTkFrame(group.surface, fg_color="transparent")
            shortcut_row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                shortcut_row,
                text=name,
                font=FONT_ROW,
                anchor="w",
            ).pack(side="left", anchor="w", padx=(ROW_PADX + 12, 0), pady=CHILD_ROW_PADY)
            ctk.CTkLabel(
                shortcut_row,
                text=combo,
                font=FONT_ROW,
                anchor="e",
                text_color=TEXT_MUTED,
            ).pack(side="right", padx=(0, ROW_PADX), pady=CHILD_ROW_PADY)
            group.add_child_row(shortcut_row)
            widgets.append(shortcut_row)
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
            self._include_prereleases_var,
            self._meeting_recorder_enabled_var,
            self._high_quality_enabled_var,
            self._best_quality_enabled_var,
            *self._notify_vars.values(),
        ):
            var.trace_add("write", lambda *_args: self._schedule_save())

        self._startup_view_var.trace_add("write", lambda *_args: self._schedule_save())
        self._close_action_var.trace_add("write", lambda *_args: self._schedule_save())
        self._discord_row.entry.bind("<FocusOut>", lambda _event: self._schedule_save(), add="+")
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
            close_key = app_prefs.get("close_action", "tray")
            self._close_action_var.set(_CLOSE_ACTION_LABELS.get(close_key, _CLOSE_ACTION_LABELS["tray"]))
            self._include_prereleases_var.set(bool(app_prefs.get("include_prereleases", False)))
            self._meeting_recorder_enabled_var.set(bool(app_prefs.get("meeting_recording_enabled", False)))
            self._high_quality_enabled_var.set(
                bool(app_prefs.get("meeting_recording_high_quality_enabled", False))
            )
            self._best_quality_enabled_var.set(
                bool(app_prefs.get("meeting_recording_best_quality_enabled", False))
            )
            self._refresh_meeting_recorder_status_label()
            self._sync_meeting_recorder_children()

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
        close_action = _CLOSE_ACTION_VALUES.get(
            self._close_action_var.get(),
            existing_prefs["close_action"],
        )
        launch_at_startup = bool(self._startup_enabled_var.get())
        launch_minimized = bool(self._launch_minimized_var.get()) if launch_at_startup else False

        app_prefs = {
            "close_action": close_action,
            "launch_at_startup": launch_at_startup,
            "launch_minimized_to_tray": launch_minimized,
            "startup_view": startup_view,
            "include_prereleases": bool(self._include_prereleases_var.get()),
            "meeting_recording_enabled": bool(self._meeting_recorder_enabled_var.get()),
            "meeting_recording_high_quality_enabled": bool(self._high_quality_enabled_var.get()),
            "meeting_recording_best_quality_enabled": bool(self._best_quality_enabled_var.get()),
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

