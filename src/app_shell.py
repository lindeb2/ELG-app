"""Main application shell with sidebar navigation."""
from __future__ import annotations

import os
import sys
import tkinter as tk
from collections.abc import Callable

import customtkinter as ctk

from app_config import app_preferences_from_config, read_config
from meeting_app import MeetingFrame
from meeting_point_manager import MeetingPointManagerFrame
from session_guard import has_unlogged_time, prompt_unlogged_action_blocked, prompt_unlogged_exit
from settings_frame import SettingsFrame
from shutdown_block import ShutdownBlocker
from stats_viewer import StatsFrame
from system_tray import SystemTray
from Timetable import TimetableFrame
from platform_keys import (
    IS_WINDOWS,
    alt_arrow_sequences,
    bind_sequences,
    primary_letter_sequences,
    primary_modifier,
    unbind_sequences,
)
from title_bar_pin import FLUENT_ICON_FONT, TitleBarButtonOverlay, WIDGET_ENTER_GLYPH
from window_chrome import (
    CAPTION_ICON_PATH,
    apply_app_title_bar_chrome,
    apply_widget_chrome,
    apply_widget_title_bar_chrome,
    hide_title_bar_icon,
    warm_widget_title_bar_assets,
)

_APP_BG = "#000000"
_SECTION_GAP = 1
_GAP_COLOR = "#303030"
_SIDEBAR_WIDTH = 100
_SIDEBAR_BTN_PADX = (0, 0)
_SIDEBAR_BTN_PADY = (0, 1)
_SIDEBAR_BTN_TEXT_INSET = 5
_SIDEBAR_BTN_WIDTH = _SIDEBAR_WIDTH
_WIDGET_WIDTH = 200
_WIDGET_HEIGHT = 170

_VIEW_SIZES: dict[str, tuple[int, int]] = {
    "timetable": (300, 177),
    "statistics": (1420, 800),
    "meeting": (1360, 820),
    "meeting_points": (300, 177),
    "settings": (462, 462),
}

_NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("timetable", "Timetable"),
    ("statistics", "Statistics"),
    ("meeting", "Meeting App"),
    ("meeting_points", "Point Manager"),
)

_ALT_ARROW_UP = tuple(s for s in alt_arrow_sequences() if s.endswith("Up>"))
_ALT_ARROW_DOWN = tuple(s for s in alt_arrow_sequences() if "Down" in s)

_SHORTCUT_VIEWS: dict[str, str] = {
    "1": "timetable",
    "2": "statistics",
    "3": "meeting",
    "4": "meeting_points",
}

_SIDEBAR_BG = _APP_BG
_BTN_INACTIVE = {"fg_color": _SIDEBAR_BG, "hover_color": "#252525", "text_color": "#888888"}
_BTN_ACTIVE = {"fg_color": "#333333", "hover_color": "#404040", "text_color": "white"}
_NAV_BTN = {
    **_BTN_INACTIVE,
    "width": _SIDEBAR_BTN_WIDTH,
    "anchor": "w",
    "corner_radius": 0,
}
_PIN_TRAILING = 1
_WIDGET_ICON_UPKEEP_MS = 250
_TK_FRAME = {"highlightthickness": 0, "bd": 0}


def _tk_frame(parent: tk.Misc, *, bg: str, width: int | None = None, height: int | None = None) -> tk.Frame:
    kwargs: dict = {"bg": bg, **_TK_FRAME}
    if width is not None:
        kwargs["width"] = width
    if height is not None:
        kwargs["height"] = height
    return tk.Frame(parent, **kwargs)


def _paint_ctk_button(btn: ctk.CTkButton, fg: str, text_color: str) -> None:
    fg_mode = btn._apply_appearance_mode(fg)
    text_mode = btn._apply_appearance_mode(text_color)
    btn._canvas.itemconfig("inner_parts", outline=fg_mode, fill=fg_mode)
    if btn._text_label is not None:
        btn._text_label.configure(bg=fg_mode, fg=text_mode)


def _apply_sidebar_nav_text_inset(btn: ctk.CTkButton) -> None:
    text_label = btn._text_label
    if text_label is None:
        return
    grid_info = text_label.grid_info()
    text_label.grid(
        row=int(grid_info["row"]),
        column=int(grid_info["column"]),
        sticky=grid_info.get("sticky", "w"),
        padx=(_SIDEBAR_BTN_TEXT_INSET, 0),
    )


class AppShell(tk.Frame):
    def __init__(
        self,
        window: ctk.CTk,
        master: tk.Misc,
        *,
        initial_view: str = "timetable",
        start_minimized_to_tray: bool = False,
    ):
        super().__init__(master, bg=_APP_BG, **_TK_FRAME)
        self._window = window
        self._initial_view = initial_view
        self._start_minimized_to_tray = start_minimized_to_tray
        self._app_prefs = app_preferences_from_config(read_config())
        self._tray = SystemTray(
            CAPTION_ICON_PATH,
            on_show=self._show_from_tray,
            on_exit=self._exit_from_tray,
        )
        self._shutdown_blocker = ShutdownBlocker(
            window,
            should_block=lambda: has_unlogged_time(self.get_timetable()),
        )
        self._ctrl_r_bound = False
        self._manual_update_check: Callable | None = None
        self._instance_guard = None
        self._sidebar_visible = True
        self._sidebar_before_fullscreen: bool | None = None
        self._widget_mode = False
        self._view_before_widget: str | None = None
        self._sidebar_before_widget = True
        self._title_bar_pin: TitleBarButtonOverlay | None = None
        self._widget_icon_upkeep_job: str | None = None
        self._widget_resync_jobs: list[str] = []
        self._widget_session = 0
        self._entering_widget_mode = False
        self._timetable_nav_hovered = False
        self._timetable_pin_hovered = False
        self._content_row = 0
        self._content_col = 0

        self._active_view: str | None = None
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._frames: dict[str, tk.Frame | ctk.CTkFrame | None] = {
            "timetable": None,
            "statistics": None,
            "meeting": None,
            "meeting_points": None,
            "settings": None,
        }

        self._sidebar = _tk_frame(self, bg=_SIDEBAR_BG, width=_SIDEBAR_WIDTH)
        self._sidebar.grid_propagate(False)
        self._sidebar.grid_columnconfigure(0, weight=1, minsize=0)
        self._sidebar.grid_rowconfigure(len(_NAV_ITEMS), weight=1)

        for row, (key, label) in enumerate(_NAV_ITEMS):
            if key == "timetable":
                self._build_timetable_nav(row)
                continue
            btn = ctk.CTkButton(
                self._sidebar,
                text=label,
                command=lambda k=key: self.switch_view(k),
                **_NAV_BTN,
            )
            btn.grid(row=row, column=0, sticky="ew", padx=_SIDEBAR_BTN_PADX, pady=_SIDEBAR_BTN_PADY)
            _apply_sidebar_nav_text_inset(btn)
            self._nav_buttons[key] = btn

        self._settings_btn = ctk.CTkButton(
            self._sidebar,
            text="Settings",
            command=lambda: self.switch_view("settings"),
            **_NAV_BTN,
        )
        self._settings_btn.grid(
            row=len(_NAV_ITEMS) + 1,
            column=0,
            sticky="sew",
            padx=_SIDEBAR_BTN_PADX,
            pady=(0, 0),
        )
        self._nav_buttons["settings"] = self._settings_btn
        _apply_sidebar_nav_text_inset(self._settings_btn)

        self._content = _tk_frame(self, bg=_APP_BG)

        self._apply_shell_layout()

        window.protocol("WM_DELETE_WINDOW", self._on_close)
        self._bind_shortcuts()
        self._sync_ctrl_r_binding()
        self._shutdown_blocker.install()
        if sys.platform.startswith("win"):
            self._window.bind("<FocusIn>", self._on_widget_window_focus, add="+")
            self._window.bind("<Activate>", self._on_widget_window_focus, add="+")
            self._window.after(300, self.warm_widget_title_bar_pin)

    def set_app_preferences(self, app_prefs: dict) -> None:
        self._app_prefs = dict(app_prefs)
        self._sync_ctrl_r_binding()

    def set_manual_update_check(self, callback: Callable | None) -> None:
        self._manual_update_check = callback

    def set_instance_guard(self, guard) -> None:
        self._instance_guard = guard

    def refresh_settings_update_controls(self) -> None:
        settings_host = self._frames.get("settings")
        settings = self._view_child(settings_host)
        if settings is not None and hasattr(settings, "refresh_update_controls"):
            settings.refresh_update_controls()

    def get_timetable(self) -> TimetableFrame | None:
        host = self._frames.get("timetable")
        child = self._view_child(host)
        return child if isinstance(child, TimetableFrame) else None

    def on_timetable_session_changed(self) -> None:
        self._shutdown_blocker.sync()
        settings_host = self._frames.get("settings")
        settings = self._view_child(settings_host)
        if settings is not None and hasattr(settings, "refresh_session_controls"):
            settings.refresh_session_controls()

    def discard_timetable_session(self) -> None:
        timetable = self.get_timetable()
        if timetable is not None:
            timetable.discard_session()

    def _sync_ctrl_r_binding(self) -> None:
        sequences = primary_letter_sequences("r")
        if self._app_prefs.get("enable_ctrl_r_reload"):
            if not self._ctrl_r_bound:
                bind_sequences(self._window, sequences, self._on_ctrl_r_reload)
                self._ctrl_r_bound = True
        elif self._ctrl_r_bound:
            unbind_sequences(self._window, sequences)
            self._ctrl_r_bound = False

    def _on_ctrl_r_reload(self, _event=None) -> str | None:
        if not self._app_prefs.get("enable_ctrl_r_reload"):
            return None
        if self._active_view in (None, "settings"):
            return "break"
        timetable = self.get_timetable()
        if has_unlogged_time(timetable):
            prompt_unlogged_action_blocked(self._window, timetable, action="reload the view")
            return "break"
        self.reload_current_view()
        return "break"

    def reload_view(self, name: str) -> None:
        """Destroy a view's frame. Rebuilds immediately if it's the active view;
        otherwise just clears it so it rebuilds lazily on next navigation there."""
        if name == "settings":
            return

        if name != self._active_view:
            host = self._frames.get(name)
            if host is not None:
                for child in list(host.winfo_children()):
                    child.destroy()
                self._frames[name] = None
            return

        if self._widget_mode:
            self._exit_widget_mode(restore_view=False)
        host = self._frames.get(name)
        if host is not None:
            for child in list(host.winfo_children()):
                child.destroy()
            self._frames[name] = None
        self._activate_view(name)

    def reload_current_view(self) -> None:
        if self._active_view:
            self.reload_view(self._active_view)

    def _minimize_to_tray(self) -> None:
        self._tray.ensure_started()
        self._window.withdraw()

    def _show_from_tray(self) -> None:
        self._window.after(0, self.restore_after_secondary_launch)

    def restore_after_secondary_launch(self) -> None:
        """Show, un-minimize, and focus the app (tray or second launch)."""
        if self._window.state() == "iconic":
            self._window.state("normal")
        self._window.deiconify()
        self._window.lift()
        self._window.attributes("-topmost", True)
        self._window.update_idletasks()
        self._window.attributes("-topmost", False)
        self._window.focus_force()
        self._sync_title_bar_chrome()

    def _deiconify_main_window(self) -> None:
        self.restore_after_secondary_launch()

    def _exit_from_tray(self) -> None:
        self._window.after(0, self._request_exit)

    def _request_exit(self) -> None:
        timetable = self.get_timetable()
        if has_unlogged_time(timetable):
            choice = prompt_unlogged_exit(self._window, timetable)
            if choice != "discard":
                return
            self.discard_timetable_session()
        self._quit_app()

    def _quit_app(self) -> None:
        self._shutdown_blocker.teardown()
        if self._title_bar_pin is not None and self._title_bar_pin.winfo_exists():
            self._title_bar_pin.destroy()
        self._title_bar_pin = None
        self._stop_widget_icon_upkeep()
        self._cancel_widget_resync()
        self._tray.stop()
        self._window.destroy()

    def mount_initial_view(self) -> None:
        if self._start_minimized_to_tray:
            self._tray.ensure_started()
            self.switch_view(self._initial_view, update_geometry=False)
            self._window.withdraw()
            return
        self.switch_view(self._initial_view)

    def _sync_title_bar_chrome(self) -> None:
        if self._widget_mode:
            apply_widget_title_bar_chrome(self._window)
        else:
            apply_app_title_bar_chrome(self._window)

    def _section_gaps_active(self) -> bool:
        return self._sidebar_visible and not self._widget_mode

    def content_top_pad_active(self) -> bool:
        return self._sidebar_visible and not self._widget_mode

    def _sync_view_padding(self) -> None:
        timetable = self.get_timetable()
        if timetable is not None and hasattr(timetable, "sync_top_padding"):
            timetable.sync_top_padding()

        meeting_points = self._view_child(self._frames.get("meeting_points"))
        if meeting_points is not None and hasattr(meeting_points, "sync_top_padding"):
            meeting_points.sync_top_padding()

    def _apply_shell_layout(self) -> None:
        """Strip-colored shell + grid gaps: empty cells show through as 1px dividers."""
        gaps = self._section_gaps_active()

        if gaps:
            self.configure(bg=_GAP_COLOR)
            self.grid_rowconfigure(0, weight=0, minsize=_SECTION_GAP)
            self.grid_rowconfigure(1, weight=1)
            self.grid_columnconfigure(0, weight=0, minsize=_SIDEBAR_WIDTH)
            self.grid_columnconfigure(1, weight=0, minsize=_SECTION_GAP)
            self.grid_columnconfigure(2, weight=1)

            self._content_row = 1
            self._content_col = 2
            self._sidebar.grid(row=1, column=0, sticky="nsew")
        else:
            self.configure(bg=_APP_BG)
            self._sidebar.grid_remove()

            self.grid_rowconfigure(0, weight=1)
            self.grid_rowconfigure(1, weight=0, minsize=0)
            self.grid_columnconfigure(0, weight=1)
            for col in (1, 2):
                self.grid_columnconfigure(col, weight=0, minsize=0)

            self._content_row = 0
            self._content_col = 0

        self._sync_view_padding()

    def warm_widget_title_bar_pin(self) -> None:
        if not sys.platform.startswith("win"):
            return
        warm_widget_title_bar_assets()
        try:
            self._ensure_title_bar_pin()
        except OSError:
            pass

    def _build_timetable_nav(self, row: int) -> None:
        row_frame = _tk_frame(self._sidebar, bg=_SIDEBAR_BG)
        row_frame.grid(row=row, column=0, sticky="ew", padx=_SIDEBAR_BTN_PADX, pady=_SIDEBAR_BTN_PADY)
        row_frame.grid_columnconfigure(0, weight=1)

        nav_btn = ctk.CTkButton(
            row_frame,
            text="Timetable",
            command=self._on_timetable_nav_clicked,
            hover=False,
            **_NAV_BTN,
        )
        nav_btn.grid(row=0, column=0, sticky="ew")
        _apply_sidebar_nav_text_inset(nav_btn)

        pin_btn = ctk.CTkButton(
            row_frame,
            text=WIDGET_ENTER_GLYPH,
            width=26,
            height=26,
            font=FLUENT_ICON_FONT,
            corner_radius=0,
            command=self._on_widget_pin_clicked,
            hover=False,
            border_width=0,
            fg_color=_BTN_INACTIVE["fg_color"],
            hover_color=_BTN_INACTIVE["hover_color"],
            text_color=_BTN_INACTIVE["text_color"],
        )
        pin_btn.place(relx=1.0, rely=0.5, anchor="e", x=-_PIN_TRAILING)
        pin_btn.lift()

        self._nav_buttons["timetable"] = nav_btn
        self._timetable_pin_btn = pin_btn
        self._wire_timetable_nav_hover(nav_btn, pin_btn)
        self._sync_timetable_nav_appearance()

    def _on_timetable_nav_clicked(self) -> None:
        self.switch_view("timetable")

    def _on_widget_pin_clicked(self) -> None:
        self._enter_widget_mode()

    def _timetable_nav_palette(self, *, hovered: bool) -> tuple[str, str]:
        palette = _BTN_ACTIVE if self._active_view == "timetable" else _BTN_INACTIVE
        fg = palette["hover_color"] if hovered else palette["fg_color"]
        return fg, palette["text_color"]

    def _wire_timetable_nav_hover(
        self,
        nav_btn: ctk.CTkButton,
        pin_btn: ctk.CTkButton,
    ) -> None:
        def bind_hover_targets(
            btn: ctk.CTkButton,
            on_enter: Callable[[object], None],
            on_leave: Callable[[object], None],
        ) -> None:
            for target in (btn, btn._canvas, btn._text_label):
                if target is not None:
                    target.bind("<Enter>", on_enter, add="+")
                    target.bind("<Leave>", on_leave, add="+")

        bind_hover_targets(
            nav_btn,
            lambda _e: self._set_timetable_nav_hovered(True),
            lambda _e: self._set_timetable_nav_hovered(False),
        )
        bind_hover_targets(
            pin_btn,
            lambda _e: self._set_timetable_pin_hovered(True),
            lambda _e: self._set_timetable_pin_hovered(False),
        )

    def _set_timetable_nav_hovered(self, hovered: bool) -> None:
        self._timetable_nav_hovered = hovered
        self._sync_timetable_nav_appearance()

    def _set_timetable_pin_hovered(self, hovered: bool) -> None:
        self._timetable_pin_hovered = hovered
        self._sync_timetable_nav_appearance()

    def _sync_timetable_nav_appearance(self) -> None:
        if "timetable" not in self._nav_buttons or not hasattr(self, "_timetable_pin_btn"):
            return

        nav_btn = self._nav_buttons["timetable"]
        pin_btn = self._timetable_pin_btn
        base_fg, base_text = self._timetable_nav_palette(hovered=False)
        hover_fg, hover_text = self._timetable_nav_palette(hovered=True)

        if self._timetable_pin_hovered and not self._timetable_nav_hovered:
            _paint_ctk_button(nav_btn, base_fg, base_text)
            _paint_ctk_button(pin_btn, hover_fg, hover_text)
        elif self._timetable_nav_hovered:
            _paint_ctk_button(nav_btn, hover_fg, hover_text)
            _paint_ctk_button(pin_btn, hover_fg, hover_text)
        else:
            _paint_ctk_button(nav_btn, base_fg, base_text)
            _paint_ctk_button(pin_btn, base_fg, base_text)

    def _bind_shortcuts(self) -> None:
        mod = primary_modifier()
        bind_sequences(
            self._window,
            primary_letter_sequences("b"),
            self._on_toggle_sidebar,
        )
        for key, view in _SHORTCUT_VIEWS.items():
            self._window.bind(
                f"<{mod}-Key-{key}>",
                lambda _e, v=view: self.switch_view(v),
                add="+",
            )
            self._window.bind(
                f"<{mod}-{key}>",
                lambda _e, v=view: self.switch_view(v),
                add="+",
            )
        self._window.bind(f"<{mod}-comma>", lambda _e: self.switch_view("settings"), add="+")
        bind_sequences(self._window, _ALT_ARROW_UP, self._on_alt_enter_widget, bind_all=True)
        bind_sequences(self._window, _ALT_ARROW_DOWN, self._on_alt_exit_widget, bind_all=True)

    def _on_toggle_sidebar(self, _event=None) -> None:
        self.toggle_sidebar()

    def toggle_sidebar(self) -> None:
        if self._widget_mode:
            return
        self.set_sidebar_visible(not self._sidebar_visible)

    def set_sidebar_visible(self, visible: bool) -> None:
        if self._widget_mode:
            return
        if visible == self._sidebar_visible:
            return
        self._sidebar_visible = visible
        self._apply_shell_layout()
        if self._active_view is not None:
            self._apply_layout(self._active_view)
        self._apply_window_geometry()
        self._sync_title_bar_chrome()

    def enter_meeting_fullscreen(self) -> None:
        if self._sidebar_before_fullscreen is None:
            self._sidebar_before_fullscreen = self._sidebar_visible
        if self._sidebar_visible:
            self.set_sidebar_visible(False)

    def exit_meeting_fullscreen(self) -> None:
        if self._sidebar_before_fullscreen is not None:
            self.set_sidebar_visible(self._sidebar_before_fullscreen)
            self._sidebar_before_fullscreen = None
        self._window.update_idletasks()
        self._sync_title_bar_chrome()
        self._window.after_idle(self._sync_title_bar_chrome)

    def _apply_window_geometry(self) -> None:
        if self._widget_mode:
            self._apply_widget_geometry()
            return
        if self._active_view is None:
            return
        width, height = _VIEW_SIZES[self._active_view]
        if not self._sidebar_visible:
            width -= _SIDEBAR_WIDTH
        elif self._section_gaps_active():
            width += _SECTION_GAP
            height += _SECTION_GAP
        self._window.geometry(f"{width}x{height}")

    def _apply_widget_geometry(self) -> None:
        self._window.geometry(f"{_WIDGET_WIDTH}x{_WIDGET_HEIGHT}")

    def _on_alt_enter_widget(self, _event=None) -> str | None:
        if self._active_view == "timetable" and not self._widget_mode:
            self._enter_widget_mode()
            return "break"
        return None

    def _on_alt_exit_widget(self, _event=None) -> str | None:
        if self._widget_mode:
            self._exit_widget_mode(restore_view=True)
            return "break"
        return None

    def _exit_widget_mode_from_pin(self) -> None:
        self._exit_widget_mode(restore_view=True)

    def _ensure_title_bar_pin(self) -> TitleBarButtonOverlay | None:
        if not IS_WINDOWS:
            return None
        if self._title_bar_pin is not None and not self._title_bar_pin.winfo_exists():
            self._title_bar_pin = None
        if self._title_bar_pin is None:
            self._title_bar_pin = TitleBarButtonOverlay(
                self._window,
                command=self._exit_widget_mode_from_pin,
            )
        return self._title_bar_pin

    def _hide_title_bar_pin(self) -> None:
        if self._title_bar_pin is not None and self._title_bar_pin.winfo_exists():
            self._title_bar_pin.hide()

    def _on_widget_window_focus(self, _event=None) -> None:
        if self._widget_mode:
            hide_title_bar_icon(self._window)

    def _start_widget_icon_upkeep(self) -> None:
        self._stop_widget_icon_upkeep()

        def tick() -> None:
            if not self._widget_mode:
                return
            hide_title_bar_icon(self._window)
            self._widget_icon_upkeep_job = self._window.after(
                _WIDGET_ICON_UPKEEP_MS, tick
            )

        tick()

    def _stop_widget_icon_upkeep(self) -> None:
        if self._widget_icon_upkeep_job is not None:
            self._window.after_cancel(self._widget_icon_upkeep_job)
            self._widget_icon_upkeep_job = None

    def _cancel_widget_resync(self) -> None:
        for job in self._widget_resync_jobs:
            self._window.after_cancel(job)
        self._widget_resync_jobs = []

    def _schedule_widget_pin_resync(self, widget_session: int) -> None:
        self._cancel_widget_resync()

        def tick() -> None:
            if not self._widget_mode or widget_session != self._widget_session:
                return
            self._apply_widget_geometry()
            pin = self._ensure_title_bar_pin()
            if pin is not None:
                pin.show()

        self._widget_resync_jobs.append(self._window.after(160, tick))

    def _forgot_inactive_view_hosts(self, active: str) -> None:
        for key, host in self._frames.items():
            if key != active and host is not None:
                host.pack_forget()
                host.lower()

    def _focus_view(self, name: str) -> None:
        child = self._view_child(self._frames.get(name))
        if child is not None:
            try:
                child.focus_set()
            except tk.TclError:
                pass

    def _set_stats_refresh_active(self, active: bool) -> None:
        stats = self._view_child(self._frames.get("statistics"))
        if stats is None:
            return
        if active:
            stats.resume_refresh()
        else:
            stats.pause_refresh()

    def _enter_widget_mode(self) -> None:
        if self._widget_mode or self._entering_widget_mode:
            return
        self._entering_widget_mode = True

        warm_widget_title_bar_assets()
        hide_title_bar_icon(self._window)
        self._leave_meeting_fullscreen()

        self._view_before_widget = self._active_view or "timetable"
        self._sidebar_before_widget = self._sidebar_visible
        self._set_stats_refresh_active(False)

        self._widget_mode = True
        self._widget_session += 1
        widget_session = self._widget_session
        self._cancel_widget_resync()
        self._start_widget_icon_upkeep()

        self._sidebar_visible = False
        self._apply_shell_layout()

        if self._active_view != "timetable":
            self._activate_view("timetable", update_geometry=False)
        else:
            self._apply_layout("timetable")
            self._forgot_inactive_view_hosts("timetable")

        apply_widget_chrome(self._window, enabled=True)
        self._update_nav_styles("timetable")
        self._apply_widget_geometry()

        self._window.after_idle(
            lambda s=widget_session: self._finish_enter_widget_mode(s),
        )

    def _finish_enter_widget_mode(self, widget_session: int) -> None:
        try:
            if not self._widget_mode or widget_session != self._widget_session:
                return
            self._focus_view("timetable")
            pin = self._ensure_title_bar_pin()
            if pin is not None:
                pin.show()
            self._schedule_widget_pin_resync(widget_session)
        finally:
            self._entering_widget_mode = False

    def _exit_widget_mode(self, *, restore_view: bool = True) -> None:
        if not self._widget_mode or self._entering_widget_mode:
            return
        self._widget_mode = False
        self._widget_session += 1
        self._cancel_widget_resync()
        self._stop_widget_icon_upkeep()

        self._hide_title_bar_pin()

        apply_widget_chrome(self._window, enabled=False)

        self._sidebar_visible = self._sidebar_before_widget
        self._apply_shell_layout()

        restore_name = self._view_before_widget or "timetable"
        if restore_view:
            self._activate_view(restore_name)
        else:
            if self._active_view is not None:
                self._apply_layout(self._active_view)
                if self._active_view == "statistics":
                    self._set_stats_refresh_active(True)
            self._apply_window_geometry()

        self._window.after_idle(self._sync_title_bar_chrome)

    def _apply_layout(self, name: str) -> None:
        row = self._content_row
        col = self._content_col
        self.grid_rowconfigure(row, weight=1)
        self.grid_columnconfigure(col, weight=1, minsize=0)
        self._content.grid(
            row=row,
            column=col,
            sticky="nsew",
        )
        self._content.grid_propagate(True)

    def _leave_meeting_fullscreen(self) -> None:
        meeting = self._view_child(self._frames.get("meeting"))
        if meeting is not None and hasattr(meeting, "leave_fullscreen_if_active"):
            meeting.leave_fullscreen_if_active()

    def _activate_view(self, name: str, *, update_geometry: bool = True) -> None:
        """Show a view without leaving widget mode."""
        if name != "meeting":
            self._leave_meeting_fullscreen()

        previous = self._active_view
        if previous == "settings" and name != "settings":
            settings = self._view_child(self._frames.get("settings"))
            if settings is not None and hasattr(settings, "on_leave_view"):
                settings.on_leave_view()
        if previous == "statistics" and name != "statistics":
            self._set_stats_refresh_active(False)

        self._apply_layout(name)
        self._forgot_inactive_view_hosts(name)

        if self._frames[name] is None:
            self._frames[name] = self._create_frame(name)

        frame = self._frames[name]
        assert frame is not None
        frame.pack(fill="both", expand=True)
        frame.lift()
        self._active_view = name

        if name == "statistics":
            self._set_stats_refresh_active(True)
        if name == "settings":
            settings = self._view_child(self._frames.get("settings"))
            if settings is not None:
                if hasattr(settings, "refresh_session_controls"):
                    settings.refresh_session_controls()
                if hasattr(settings, "refresh_update_controls"):
                    settings.refresh_update_controls()

        if update_geometry:
            self._apply_window_geometry()

        self._update_nav_styles(name)
        self._sync_view_padding()
        self._focus_view(name)

    def switch_view(self, name: str, *, update_geometry: bool = True) -> None:
        if self._entering_widget_mode:
            return
        if self._widget_mode:
            if name == self._active_view:
                return
            self._exit_widget_mode(restore_view=False)

        self._activate_view(name, update_geometry=update_geometry)

    def _update_nav_styles(self, active: str) -> None:
        for key, btn in self._nav_buttons.items():
            if key == "timetable":
                continue
            btn.configure(**(_BTN_ACTIVE if key == active else _BTN_INACTIVE))

        if "timetable" not in self._nav_buttons:
            return

        palette = _BTN_ACTIVE if active == "timetable" else _BTN_INACTIVE
        self._nav_buttons["timetable"].configure(**palette, hover=False)
        if hasattr(self, "_timetable_pin_btn"):
            self._timetable_pin_btn.configure(**palette, hover=False)
        self._timetable_nav_hovered = False
        self._timetable_pin_hovered = False
        self._sync_timetable_nav_appearance()

    @staticmethod
    def _view_child(host: tk.Frame | ctk.CTkFrame | None) -> tk.Misc | None:
        if host is None:
            return None
        children = host.winfo_children()
        return children[0] if children else host

    def _view_host(self, frame_cls: type, **kwargs) -> tk.Frame:
        host = _tk_frame(self._content, bg=_APP_BG)
        frame = frame_cls(host, **kwargs)
        frame.pack(fill="both", expand=True)
        return host

    def _create_frame(self, name: str) -> tk.Frame | ctk.CTkFrame:
        if name == "timetable":
            return self._view_host(TimetableFrame, shell=self)
        if name == "statistics":
            return self._view_host(StatsFrame)
        if name == "meeting":
            return self._view_host(MeetingFrame, shell=self)
        if name == "settings":
            return self._view_host(SettingsFrame, shell=self)
        if name == "meeting_points":
            return self._view_host(
                MeetingPointManagerFrame,
                navigate_back=lambda: self.switch_view("timetable"),
                shell=self,
            )
        raise ValueError(f"Unknown view: {name}")

    def _on_close(self) -> None:
        if self._app_prefs.get("close_action", "tray") == "tray":
            self._minimize_to_tray()
            return
        self._request_exit()
