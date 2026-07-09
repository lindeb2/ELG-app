"""Windows Settings–style building blocks for the settings view."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any

import customtkinter as ctk

from CTkStickyPlaceholderEntry import CTkStickyPlaceholderEntry
from settings_toggle_switch import SettingsToggleSwitch
from settings_ui_constants import (
    ACCENT,
    ACCENT_DIM,
    ACCOUNT_LABEL_WIDTH,
    ACCOUNT_FIELD_SHIFT,
    BOX_BORDER,
    DISCARD_BORDER,
    DISCARD_TEXT,
    BOX_CORNER,
    BOX_GAP,
    BOX_PADX,
    BOX_PADY,
    CARD_BG,
    CHEVRON_COLOR,
    CHEVRON_SIZE,
    FIELD_FG,
    FIELD_HEIGHT,
    FIELD_TEXT_PADX,
    FIELD_ENTRY_PADX,
    FONT_MUTED,
    FONT_ROW,
    OUTLINED_BTN_CHROME,
    SETTINGS_DROPDOWN_WIDTH,
    ROW_PADX,
    ROW_PADY,
    ACCOUNT_ROW_PADY,
    CHILD_ROW_PADY,
    SEPARATOR_COLOR,
    TEXT_MUTED,
)

from utils import bind_digits_only_entry, set_ctk_entry_value

__all__ = [
    "ACCENT",
    "ACCENT_DIM",
    "BOX_GAP",
    "CARD_BG",
    "DISCARD_BORDER",
    "DISCARD_TEXT",
    "ROW_PADX",
    "ROW_PADY",
    "ACCOUNT_ROW_PADY",
    "CHILD_ROW_PADY",
    "SettingsAccountFieldRow",
    "SettingsDropdownRow",
    "SettingsExpandableGroup",
    "SettingsGroup",
    "SettingsSwitchRow",
    "SettingsToggleSwitch",
    "TEXT_MUTED",
    "FONT_MUTED",
    "FONT_ROW",
    "_apply_nested_checkbox_style",
    "_make_switch",
    "_make_outlined_action_button",
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


def _apply_nested_checkbox_style(checkbox: ctk.CTkCheckBox, *, enabled: bool) -> None:
    accent = ACCENT if enabled else ACCENT_DIM
    checkbox.configure(
        state="normal" if enabled else "disabled",
        fg_color=accent,
        hover_color=accent,
        border_color=accent,
        text_color="#FFFFFF" if enabled else TEXT_MUTED,
    )


def _bordered_frame_kwargs() -> dict[str, Any]:
    return {
        "fg_color": CARD_BG,
        "corner_radius": BOX_CORNER,
        "border_width": 1,
        "border_color": BOX_BORDER,
    }


def _make_hseparator(parent: ctk.CTkBaseClass) -> tk.Frame:
    """1px divider — tk.Frame is reliable where CTkFrame height=1 often collapses."""
    sep = tk.Frame(parent, height=1, bg=SEPARATOR_COLOR, highlightthickness=0, bd=0)
    sep.pack(fill="x", side="top")
    sep.pack_propagate(False)
    return sep


def _make_outlined_action_button(
    parent: ctk.CTkBaseClass,
    text: str,
    *,
    command: Callable[[], None] | None = None,
    text_color: str = "#FFFFFF",
    text_color_disabled: str | None = None,
) -> ctk.CTkButton:
    kwargs: dict[str, Any] = {
        "master": parent,
        "text": text,
        "width": 1,
        "height": 28,
        "font": FONT_ROW,
        "fg_color": "transparent",
        "hover_color": "#3A3A3A",
        "border_width": 1,
        "border_color": "#555555",
        "border_spacing": 0,
        "corner_radius": 4,
        "text_color": text_color,
        "command": command,
    }
    if text_color_disabled is not None:
        kwargs["text_color_disabled"] = text_color_disabled
    button = ctk.CTkButton(**kwargs)
    button.update_idletasks()
    label = button._text_label
    if label is not None:
        button.configure(width=label.winfo_reqwidth() + OUTLINED_BTN_CHROME)
    return button


class SettingsGroup(ctk.CTkFrame):
    """Single bordered settings box. Rows are packed directly on this frame."""

    def __init__(self, parent, *, border_color: str | None = None, border_width: int | None = None, **kwargs):
        frame_kwargs = _bordered_frame_kwargs()
        if border_color is not None:
            frame_kwargs["border_color"] = border_color
        if border_width is not None:
            frame_kwargs["border_width"] = border_width
        super().__init__(parent, **frame_kwargs, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self._has_rows = False

    def add_row(self, widget: ctk.CTkBaseClass, *, separator: bool = False) -> None:
        if self._has_rows and separator:
            _make_hseparator(self)
        widget.pack(fill="x", padx=BOX_PADX, pady=(BOX_PADY, BOX_PADY), side="top")
        self._has_rows = True

    @property
    def surface(self) -> ctk.CTkFrame:
        return self


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


class SettingsAccountFieldRow(ctk.CTkFrame):
    """Account field row with fixed label width so Username/Discord line up across boxes."""

    def __init__(
        self,
        parent,
        label: str,
        *,
        editable: bool = True,
        placeholder: str = "",
        digits_only: bool = False,
    ):
        super().__init__(parent, fg_color="transparent")
        self._editable = editable
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=label,
            font=FONT_ROW,
            anchor="w",
            width=ACCOUNT_LABEL_WIDTH,
        ).grid(row=0, column=0, sticky="w", padx=(ROW_PADX, 8), pady=ACCOUNT_ROW_PADY)

        field_left = ROW_PADX + ACCOUNT_LABEL_WIDTH + 8 - ACCOUNT_FIELD_SHIFT
        self._field_box = ctk.CTkFrame(
            self,
            height=FIELD_HEIGHT,
            fg_color=FIELD_FG,
            border_width=1,
            border_color=BOX_BORDER,
            corner_radius=4,
        )
        self._field_box.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=(field_left, 0),
            pady=ACCOUNT_ROW_PADY,
        )
        self._field_box.grid_propagate(False)

        text_pad = (FIELD_TEXT_PADX, FIELD_TEXT_PADX)
        if editable:
            entry_kwargs = {
                "font": FONT_ROW,
                "placeholder_text": placeholder,
                "fg_color": FIELD_FG,
                "border_width": 0,
                "corner_radius": 0,
                "text_color": "#FFFFFF",
                "placeholder_text_color": TEXT_MUTED,
            }
            entry_cls = CTkStickyPlaceholderEntry if placeholder else ctk.CTkEntry
            self.entry = entry_cls(self._field_box, **entry_kwargs)
            self.entry.pack(
                fill="both",
                expand=True,
                padx=(FIELD_ENTRY_PADX, FIELD_TEXT_PADX),
                pady=1,
            )
            if digits_only:
                bind_digits_only_entry(self.entry)
            self._value_label = None
        else:
            self.entry = None
            self._value_label = ctk.CTkLabel(
                self._field_box,
                text="",
                font=FONT_ROW,
                text_color="#FFFFFF",
                anchor="w",
                justify="left",
            )
            self._value_label.pack(fill="both", expand=True, padx=text_pad, pady=1)

    def set_value(self, value: str) -> None:
        if self._editable and self.entry is not None:
            set_ctk_entry_value(self.entry, value)
        elif self._value_label is not None:
            self._value_label.configure(text=value)


class SettingsDropdownRow(SettingsRow):
    def __init__(
        self,
        parent,
        label: str,
        variable: ctk.StringVar,
        values: list[str],
        *,
        width: int = SETTINGS_DROPDOWN_WIDTH,
    ):
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


class SettingsExpandableGroup(ctk.CTkFrame):
    """Bordered group with a parent row and optional child rows (Windows settings style)."""

    def __init__(
        self,
        parent,
        label: str,
        *,
        toggle_var: ctk.BooleanVar | None = None,
        on_toggle: Callable[[], None] | None = None,
        on_expand: Callable[[], None] | None = None,
        on_collapse: Callable[[], None] | None = None,
        subtitle: str | None = None,
        start_expanded: bool = False,
    ):
        super().__init__(parent, **_bordered_frame_kwargs())
        self.grid_columnconfigure(0, weight=1)
        self._expanded = start_expanded
        self._on_toggle = on_toggle
        self._on_expand = on_expand
        self._on_collapse = on_collapse
        self._body_widgets: list[ctk.CTkBaseClass] = []
        self._separators: list[tk.Frame] = []
        self._body_builder: Callable[[SettingsExpandableGroup], list[Any]] | None = None

        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.pack(fill="x", padx=BOX_PADX, pady=(BOX_PADY, BOX_PADY), side="top")
        self.header.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(self.header, text=label, font=FONT_ROW, anchor="w")
        self.title_label.grid(row=0, column=0, sticky="w", padx=(ROW_PADX, 8), pady=ROW_PADY)
        row = 0
        if subtitle:
            row = 1
            ctk.CTkLabel(
                self.header,
                text=subtitle,
                font=FONT_MUTED,
                text_color=TEXT_MUTED,
                anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=(ROW_PADX, 8))

        controls = ctk.CTkFrame(self.header, fg_color="transparent")
        controls.grid(row=0, column=1, rowspan=row + 1, sticky="e", padx=(0, ROW_PADX), pady=ROW_PADY)

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
        self._chevron_btn.pack(side="left")

        if self._expanded:
            self._rebuild_body()

    @property
    def surface(self) -> ctk.CTkFrame:
        return self

    @property
    def body(self) -> ctk.CTkFrame:
        return self

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def _handle_toggle(self) -> None:
        if self._on_toggle is not None:
            self._on_toggle()

    def _set_header_padding(self, *, expanded: bool) -> None:
        if expanded:
            self.header.pack_configure(pady=(BOX_PADY, 0))
        else:
            self.header.pack_configure(pady=(BOX_PADY, BOX_PADY))

    def _clear_body(self) -> None:
        for widget in self._body_widgets:
            widget.pack_forget()
            widget.destroy()
        for sep in self._separators:
            sep.pack_forget()
            sep.destroy()
        self._body_widgets.clear()
        self._separators.clear()
        self._set_header_padding(expanded=False)

    def _toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self._chevron_btn.configure(text=_chevron_glyph(self._expanded))
        if self._expanded:
            self._rebuild_body()
        else:
            self._clear_body()
            if self._on_collapse is not None:
                self._on_collapse()

    def set_expanded(self, expanded: bool) -> None:
        if expanded != self._expanded:
            self._toggle_expanded()

    def _add_separator(self) -> None:
        self._separators.append(_make_hseparator(self))

    def add_child_row(self, widget: ctk.CTkBaseClass, *, separator: bool = True) -> None:
        if separator:
            self._add_separator()
        widget.pack(fill="x", padx=BOX_PADX, pady=0, side="top")
        self._body_widgets.append(widget)

    def append_child_row(self, widget: ctk.CTkBaseClass, *, separator: bool = True) -> None:
        """Add a row at the end of an already-expanded body."""
        if not self._expanded:
            return
        if self._body_widgets:
            self._body_widgets[-1].pack_configure(pady=0)
        if separator:
            self._add_separator()
        widget.pack(fill="x", padx=BOX_PADX, pady=0, side="top")
        widget.pack_configure(pady=(0, BOX_PADY))
        self._body_widgets.append(widget)

    def remove_child_row(self, widget: ctk.CTkBaseClass) -> None:
        if widget not in self._body_widgets:
            return
        idx = self._body_widgets.index(widget)
        widget.pack_forget()
        widget.destroy()
        self._body_widgets.pop(idx)
        if idx < len(self._separators):
            sep = self._separators.pop(idx)
            sep.pack_forget()
            sep.destroy()
        if self._body_widgets:
            self._body_widgets[-1].pack_configure(pady=(0, BOX_PADY))

    def build_body(self, builder: Callable[[SettingsExpandableGroup], list[Any]]) -> list[Any]:
        self._body_builder = builder
        return self._rebuild_body()

    def _rebuild_body(self) -> list[Any]:
        self._clear_body()
        if not self._expanded or self._body_builder is None:
            return []
        self._set_header_padding(expanded=True)
        created = self._body_builder(self)
        if self._body_widgets:
            self._body_widgets[-1].pack_configure(pady=(0, BOX_PADY))
        if self._on_expand is not None:
            self._on_expand()
        return created
