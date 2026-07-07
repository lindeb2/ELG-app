"""Custom encapsulated toggle switch for settings (canvas vector rendering)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import customtkinter as ctk
import tkinter as tk

from settings_ui_constants import (
    ACCENT,
    CARD_BG,
    SWITCH_BORDER,
    SWITCH_BORDER_WIDTH,
    SWITCH_GAP,
    SWITCH_OFF_FILL,
    SWITCH_THUMB_OFF,
    SWITCH_THUMB_ON,
    SWITCH_TRACK_HEIGHT,
    SWITCH_TRACK_WIDTH,
)


class SettingsToggleSwitch(ctk.CTkFrame):
    """Pill track with a fully inset circular thumb — no edge bleed."""

    def __init__(
        self,
        master: Any,
        *,
        text: str = "",
        command: Callable[[], None] | None = None,
        variable: tk.BooleanVar | None = None,
        width: int = SWITCH_TRACK_WIDTH,
        height: int = SWITCH_TRACK_HEIGHT,
        gap: int = SWITCH_GAP,
        on_color: str = ACCENT,
        off_fill_color: str = SWITCH_OFF_FILL,
        border_color: str = SWITCH_BORDER,
        border_width: float = SWITCH_BORDER_WIDTH,
        thumb_on_color: str = SWITCH_THUMB_ON,
        thumb_off_color: str = SWITCH_THUMB_OFF,
        text_color: str | None = None,
        font: tuple | ctk.CTkFont | None = None,
        state: str = "normal",
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)

        self.command = command
        self._variable = variable
        self._state = state
        self._syncing = False

        self.track_width = width
        self.track_height = height
        self.gap = gap
        self.circle_size = self.track_height - (self.gap * 2)

        self.on_color = on_color
        self.off_fill_color = off_fill_color
        self.border_color = border_color
        self.border_width = border_width
        self.thumb_on_color = thumb_on_color
        self.thumb_off_color = thumb_off_color

        self.is_on = False

        self.canvas = ctk.CTkCanvas(
            self,
            width=self.track_width,
            height=self.track_height,
            highlightthickness=0,
            bd=0,
            bg=CARD_BG,
        )
        self.canvas.pack(side="left")

        if text:
            self.label = ctk.CTkLabel(
                self,
                text=text,
                text_color=text_color,
                font=font if font else ctk.CTkFont(size=14),
            )
            self.label.pack(side="left", padx=(12, 0))
            self.label.bind("<Button-1>", self._on_click)

        self.canvas.bind("<Button-1>", self._on_click)

        if self._variable is not None:
            self._variable.trace_add("write", self._on_variable_changed)
            self.set(bool(self._variable.get()), notify=False)

        self._apply_state()
        self.render_switch()

    def _on_click(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self.toggle()

    def _on_variable_changed(self, *_args) -> None:
        if self._syncing or self._variable is None:
            return
        self.set(bool(self._variable.get()), notify=False)

    def _sync_variable(self) -> None:
        if self._variable is None:
            return
        self._syncing = True
        self._variable.set(self.is_on)
        self._syncing = False

    def _thumb_bounds(self) -> tuple[float, float, float, float]:
        if self.is_on:
            x0 = self.track_width - self.gap - self.circle_size
        else:
            x0 = self.gap
        y0 = self.gap
        return x0, y0, x0 + self.circle_size, y0 + self.circle_size

    def render_switch(self) -> None:
        self.canvas.delete("all")

        center_y = self.track_height / 2
        outer_radius = self.track_height / 2

        if self.is_on:
            self.canvas.create_line(
                outer_radius,
                center_y,
                self.track_width - outer_radius,
                center_y,
                width=self.track_height,
                capstyle="round",
                fill=self.on_color,
            )
            thumb_color = self.thumb_on_color
        else:
            self.canvas.create_line(
                outer_radius,
                center_y,
                self.track_width - outer_radius,
                center_y,
                width=self.track_height,
                capstyle="round",
                fill=self.border_color,
            )
            inner_height = self.track_height - (2 * self.border_width)
            inner_radius = inner_height / 2
            inset = self.border_width
            self.canvas.create_line(
                inner_radius + inset,
                center_y,
                self.track_width - inner_radius - inset,
                center_y,
                width=inner_height,
                capstyle="round",
                fill=self.off_fill_color,
            )
            thumb_color = self.thumb_off_color

        x0, y0, x1, y1 = self._thumb_bounds()
        self.canvas.create_oval(x0, y0, x1, y1, fill=thumb_color, outline="")

    def toggle(self) -> None:
        if self.is_on:
            self.deselect()
        else:
            self.select()

    def select(self) -> None:
        self.is_on = True
        self.render_switch()
        self._sync_variable()
        if self.command is not None:
            self.command()

    def deselect(self) -> None:
        self.is_on = False
        self.render_switch()
        self._sync_variable()
        if self.command is not None:
            self.command()

    def set(self, value: bool, *, notify: bool = True) -> None:
        value = bool(value)
        if value == self.is_on:
            return
        self.is_on = value
        self.render_switch()
        if notify:
            self._sync_variable()

    def get(self) -> bool:
        return self.is_on

    def configure(self, **kwargs) -> None:
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            self._apply_state()
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if kwargs:
            super().configure(**kwargs)

    def _apply_state(self) -> None:
        if self._state == "disabled":
            self.canvas.configure(cursor="arrow")
        else:
            self.canvas.configure(cursor="hand2")
