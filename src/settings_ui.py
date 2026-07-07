"""Windows Settings–style building blocks for the settings view."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import customtkinter as ctk

from settings_toggle_switch import SettingsToggleSwitch
from settings_ui_constants import (
    ACCENT,
    ACCENT_DIM,
    CARD_BG,
    CARD_CORNER,
    CHEVRON_COLOR,
    CHEVRON_SIZE,
    FIELD_BORDER,
    FIELD_FG,
    FIELD_WIDTH,
    FONT_MUTED,
    FONT_ROW,
    FONT_SECTION,
    FONT_TITLE,
    ROW_PADX,
    ROW_PADY,
    SEPARATOR_COLOR,
    TEXT_MUTED,
)

__all__ = [
    "ACCENT",
    "ACCENT_DIM",
    "CARD_BG",
    "ROW_PADX",
    "SettingsAccountFieldRow",
    "SettingsCard",
    "SettingsDropdownRow",
    "SettingsExpandableRow",
    "SettingsSectionHeader",
    "SettingsSwitchRow",
    "SettingsToggleSwitch",
    "TEXT_MUTED",
    "FONT_MUTED",
    "FONT_ROW",
    "_apply_nested_checkbox_style",
    "_make_switch",
]


def _make_switch(parent: ctk.CTkBaseClass, variable: ctk.BooleanVar, command: Callable[[], None] | None = None):
    return SettingsToggleSwitch(parent, variable=variable, command=command)


def _chevron_glyph(expanded: bool) -> str:
    return "▲" if expanded else "▼"


def _make_chevron_button(parent: ctk.CTkBaseClass, *, expanded: bool, command: Callable[[], None]) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent,
        text=_chevron_glyph(expanded),
        width=CHEVRON_SIZE,
        height=CHEVRON_SIZE,
        font=("Segoe UI Symbol", 11),
        fg_color="transparent",
        hover_color="#3A3A3A",
        text_color=CHEVRON_COLOR,
        command=command,
    )


def _field_entry(parent: ctk.CTkBaseClass, *, placeholder: str = "") -> ctk.CTkEntry:
    return ctk.CTkEntry(
        parent,
        width=FIELD_WIDTH,
        height=28,
        font=FONT_ROW,
        placeholder_text=placeholder,
        fg_color=FIELD_FG,
        border_color=FIELD_BORDER,
        text_color="#FFFFFF",
    )


def _apply_nested_checkbox_style(checkbox: ctk.CTkCheckBox, *, enabled: bool) -> None:
    accent = ACCENT if enabled else ACCENT_DIM
    checkbox.configure(
        state="normal" if enabled else "disabled",
        fg_color=accent,
        hover_color=accent,
        border_color=accent,
        text_color="#FFFFFF" if enabled else TEXT_MUTED,
    )


class SettingsSectionHeader(ctk.CTkLabel):
    def __init__(self, parent, text: str, **kwargs):
        super().__init__(
            parent,
            text=text,
            font=FONT_SECTION,
            text_color=TEXT_MUTED,
            anchor="w",
            **kwargs,
        )


class SettingsCard(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color=CARD_BG, corner_radius=CARD_CORNER, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self._row = 0

    def add_separator(self) -> None:
        sep = ctk.CTkFrame(self, height=1, fg_color=SEPARATOR_COLOR)
        sep.grid(row=self._row, column=0, sticky="ew", padx=ROW_PADX)
        self._row += 1

    def add_widget(self, widget: ctk.CTkBaseClass, *, pady: tuple[int, int] = (0, 0)) -> None:
        widget.grid(row=self._row, column=0, sticky="ew", pady=pady)
        self._row += 1

    def add_row(self, row: ctk.CTkFrame) -> None:
        row.grid(row=self._row, column=0, sticky="ew")
        self._row += 1


class SettingsRow(ctk.CTkFrame):
    def __init__(self, parent, label: str, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)

        ctk.CTkLabel(self, text=label, font=FONT_ROW, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(ROW_PADX, 8), pady=ROW_PADY
        )
        self.trailing = ctk.CTkFrame(self, fg_color="transparent")
        self.trailing.grid(row=0, column=1, sticky="e", padx=(0, ROW_PADX), pady=ROW_PADY)


class SettingsAccountFieldRow(SettingsRow):
    """Uniform inline field — editable or read-only with identical styling."""

    def __init__(self, parent, label: str, *, editable: bool = True, placeholder: str = ""):
        super().__init__(parent, label)
        self._editable = editable
        self.grid_columnconfigure(0, weight=0, minsize=90)
        self.grid_columnconfigure(1, weight=1)
        self.trailing.grid_configure(sticky="w", padx=(0, ROW_PADX))

        self._field_box = ctk.CTkFrame(
            self.trailing,
            width=FIELD_WIDTH,
            height=28,
            fg_color=FIELD_FG,
            border_width=1,
            border_color=FIELD_BORDER,
            corner_radius=6,
        )
        self._field_box.pack(anchor="w")
        self._field_box.pack_propagate(False)

        if editable:
            self.entry = ctk.CTkEntry(
                self._field_box,
                font=FONT_ROW,
                placeholder_text=placeholder,
                fg_color=FIELD_FG,
                border_width=0,
                text_color="#FFFFFF",
            )
            self.entry.pack(fill="both", expand=True, padx=(8, 8), pady=2)
            self._value_label = None
        else:
            self.entry = None
            self._value_label = ctk.CTkLabel(
                self._field_box,
                text="",
                font=FONT_ROW,
                text_color="#FFFFFF",
                anchor="w",
            )
            self._value_label.pack(fill="both", expand=True, padx=(8, 8), pady=2)

    def set_value(self, value: str) -> None:
        if self._editable and self.entry is not None:
            self.entry.delete(0, "end")
            if value:
                self.entry.insert(0, value)
        elif self._value_label is not None:
            self._value_label.configure(text=value)


class SettingsDropdownRow(SettingsRow):
    def __init__(self, parent, label: str, variable: ctk.StringVar, values: list[str], *, width: int = 160):
        super().__init__(parent, label)
        self.menu = ctk.CTkOptionMenu(
            self.trailing,
            variable=variable,
            values=values,
            width=width,
            font=FONT_ROW,
            dropdown_font=FONT_ROW,
            fg_color="#3A3A3A",
            button_color="#4A4A4A",
            button_hover_color="#555555",
        )
        self.menu.pack(anchor="e")


class SettingsSwitchRow(SettingsRow):
    def __init__(
        self,
        parent,
        label: str,
        variable: ctk.BooleanVar,
        *,
        command: Callable[[], None] | None = None,
    ):
        super().__init__(parent, label)
        self.switch = _make_switch(self.trailing, variable, command=command)
        self.switch.pack(anchor="e")


class SettingsExpandableRow(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        label: str,
        *,
        toggle_var: ctk.BooleanVar | None = None,
        on_toggle: Callable[[], None] | None = None,
        subtitle: str | None = None,
        start_expanded: bool = False,
    ):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self._expanded = start_expanded
        self._on_toggle = on_toggle

        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.grid_columnconfigure(0, weight=1)

        title_frame = ctk.CTkFrame(self.header, fg_color="transparent")
        title_frame.grid(row=0, column=0, sticky="w", padx=(ROW_PADX, 8), pady=ROW_PADY)

        self.title_label = ctk.CTkLabel(title_frame, text=label, font=FONT_ROW, anchor="w")
        self.title_label.pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(
                title_frame,
                text=subtitle,
                font=FONT_MUTED,
                text_color=TEXT_MUTED,
                anchor="w",
            ).pack(anchor="w")

        controls = ctk.CTkFrame(self.header, fg_color="transparent")
        controls.grid(row=0, column=1, sticky="e", padx=(0, 8), pady=ROW_PADY)

        self.toggle_var = toggle_var
        self.switch: SettingsToggleSwitch | None = None
        if toggle_var is not None:
            self.switch = _make_switch(controls, toggle_var, command=self._handle_toggle)
            self.switch.pack(side="left", padx=(0, 4))

        self._chevron_btn = _make_chevron_button(
            controls,
            expanded=self._expanded,
            command=self._toggle_expanded,
        )
        self._chevron_btn.pack(side="left", padx=(0, ROW_PADX - 8))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        if self._expanded:
            self.body.grid(row=1, column=0, sticky="ew")
        self._body_widgets: list[ctk.CTkBaseClass] = []

    def _handle_toggle(self) -> None:
        if self._on_toggle is not None:
            self._on_toggle()

    def _toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self._chevron_btn.configure(text=_chevron_glyph(self._expanded))
        if self._expanded:
            self.body.grid(row=1, column=0, sticky="ew")
        else:
            self.body.grid_remove()

    def set_expanded(self, expanded: bool) -> None:
        if expanded != self._expanded:
            self._toggle_expanded()

    def build_body(self, builder: Callable[[ctk.CTkFrame], list[Any]]) -> list[Any]:
        for widget in self._body_widgets:
            widget.destroy()
        self._body_widgets.clear()
        created = builder(self.body)
        self._body_widgets.extend(created)
        return created
