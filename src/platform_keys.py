"""Platform detection and keyboard shortcut conventions."""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def primary_modifier() -> str:
    """Tk event modifier: Control on Windows/Linux, Command on macOS."""
    return "Command" if IS_MACOS else "Control"


def alt_modifier() -> str:
    """Tk event modifier for Alt/Option shortcuts."""
    return "Option" if IS_MACOS else "Alt"


def primary_modifier_label() -> str:
    return "Cmd" if IS_MACOS else "Ctrl"


def alt_modifier_label() -> str:
    return "Option" if IS_MACOS else "Alt"


def _mod_seq(modifier: str, keyspec: str) -> str:
    return f"<{modifier}-{keyspec}>"


def primary_sequences(keyspec: str) -> tuple[str, ...]:
    return (_mod_seq(primary_modifier(), keyspec),)


def primary_letter_sequences(letter: str) -> tuple[str, ...]:
    mod = primary_modifier()
    return (_mod_seq(mod, letter), _mod_seq(mod, letter.upper()))


def alt_arrow_sequences() -> tuple[str, ...]:
    mod = alt_modifier()
    sequences = (
        _mod_seq(mod, "Up"),
        _mod_seq(mod, "Down"),
        _mod_seq(mod, "Key-Down"),
    )
    if IS_MACOS:
        sequences += (
            _mod_seq("Alt", "Up"),
            _mod_seq("Alt", "Down"),
            _mod_seq("Alt", "Key-Down"),
        )
    return sequences


def bind_sequences(
    window,
    sequences: tuple[str, ...],
    callback,
    *,
    add: str = "+",
    bind_all: bool = False,
) -> None:
    binder = window.bind_all if bind_all else window.bind
    for sequence in sequences:
        binder(sequence, callback, add=add)


def unbind_sequences(window, sequences: tuple[str, ...], *, bind_all: bool = False) -> None:
    unbinder = window.unbind_all if bind_all else window.unbind
    for sequence in sequences:
        try:
            unbinder(sequence)
        except Exception:
            pass
