"""Small UI helpers shared across ELG views."""
from __future__ import annotations

import customtkinter as ctk

_DIGITS_NAV_KEYS = frozenset({
    "BackSpace",
    "Delete",
    "Left",
    "Right",
    "Up",
    "Down",
    "Home",
    "End",
    "Tab",
})


def flash_error(widget, times=3):
    """Flash a widget's text color red to indicate error and play a sound"""
    original_color = widget.cget("text_color")
    if original_color == "red":
        return

    widget.bell()

    def flash(count):
        if count > 0:
            widget.configure(text_color="red" if count % 2 == 1 else original_color)
            widget.after(100, lambda: flash(count - 1))
        else:
            widget.configure(text_color=original_color)

    flash(times * 2)


def _activate_ctk_entry_placeholder(entry: ctk.CTkEntry) -> None:
    activate = getattr(entry, "_activate_placeholder", None)
    if callable(activate):
        activate()
        entry.after_idle(activate)


def set_ctk_entry_value(entry: ctk.CTkEntry, value: str) -> None:
    """Set entry text and restore placeholder when cleared."""
    entry.delete(0, "end")
    if value:
        entry.insert(0, value)
    else:
        _activate_ctk_entry_placeholder(entry)


def _paste_digits_only(entry: ctk.CTkEntry, _event=None) -> str:
    tk_entry = entry._entry
    try:
        text = entry.clipboard_get()
    except Exception:
        return "break"

    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return "break"

    if getattr(entry, "_placeholder_text_active", False):
        deactivate = getattr(entry, "_deactivate_placeholder", None)
        if callable(deactivate):
            deactivate()

    if tk_entry.select_present():
        tk_entry.delete("sel.first", "sel.last")
    tk_entry.insert("insert", digits)
    return "break"


def _sanitize_digits_entry(entry: ctk.CTkEntry, _event=None) -> None:
    if getattr(entry, "_placeholder_text_active", False):
        return
    cleaned = "".join(ch for ch in entry.get() if ch.isdigit())
    if cleaned == entry.get():
        return
    set_ctk_entry_value(entry, cleaned)


def bind_digits_only_entry(entry: ctk.CTkEntry) -> None:
    """Restrict entry input to numeric characters (Discord snowflake IDs)."""
    tk_entry = entry._entry

    def on_key_press(event) -> str | None:
        if event.keysym in _DIGITS_NAV_KEYS:
            return None
        if event.state & 0x4 and event.keysym.lower() in ("a", "c", "x", "v"):
            return None
        if event.char and len(event.char) == 1 and event.char.isdigit():
            return None
        return "break"

    tk_entry.bind("<KeyPress>", on_key_press, add=True)
    tk_entry.bind("<<Paste>>", lambda event: _paste_digits_only(entry, event), add=True)
    tk_entry.bind("<Control-v>", lambda event: _paste_digits_only(entry, event), add=True)
    tk_entry.bind("<Control-V>", lambda event: _paste_digits_only(entry, event), add=True)
    entry.bind("<FocusOut>", lambda _event: _sanitize_digits_entry(entry), add="+")