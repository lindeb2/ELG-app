"""Title-bar button overlay (CTkTitleMenu-style, no CTkMenuBar dependency)."""
from __future__ import annotations

import os
import sys
import tkinter as tk
from typing import Callable

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageTk

from window_chrome import CAPTION_COLOR_BLACK_HEX, CAPTION_HOVER_HEX, _hwnd

_CAPTION_BG_HEX = CAPTION_COLOR_BLACK_HEX
_CAPTION_HOVER_HEX = CAPTION_HOVER_HEX
_EXIT_ICON_PX = 12
_CORNER_RADIUS = 6
_LEFT_NUDGE = 1
_TOP_NUDGE = 1
_BOTTOM_GAP = 1
_CAPTION_BUTTON_WIDTH = 31

# Windows Calculator always-on-top icons (Segoe Fluent Icons).
FLUENT_ICON_FONT = ("Segoe Fluent Icons", 14)
WIDGET_ENTER_GLYPH = "\uEE49"  # MiniContract2Mirrored — enter widget
WIDGET_EXIT_GLYPH = "\uEE47"  # MiniExpand2Mirrored — exit widget


def _fluent_icon_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for name in ("SegoeIcons.ttf", "Segoe Fluent Icons.ttf"):
        path = os.path.join(windir, "Fonts", name)
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _make_exit_icon_image(size: int = _EXIT_ICON_PX) -> ImageTk.PhotoImage:
    """Render a square, undistorted icon that fills a size x size pixel box."""
    font = _fluent_icon_font(16)
    scratch = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    draw.text((0, 0), WIDGET_EXIT_GLYPH, font=font, fill=(255, 255, 255, 255))
    ink_bbox = scratch.getbbox()
    if ink_bbox is None:
        return ImageTk.PhotoImage(Image.new("RGBA", (size, size), (0, 0, 0, 0)))

    glyph = scratch.crop(ink_bbox)
    if glyph.width != glyph.height:
        side = max(glyph.width, glyph.height)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(glyph, ((side - glyph.width) // 2, (side - glyph.height) // 2), glyph)
        glyph = square

    if glyph.size != (size, size):
        glyph = glyph.resize((size, size), Image.Resampling.LANCZOS)

    return ImageTk.PhotoImage(glyph)


def _caption_button_width() -> int:
    return _CAPTION_BUTTON_WIDTH


def _caption_layout(window: ctk.CTk, btn_width: int) -> tuple[int, int, int, int]:
    """Return button width, height, and screen x/y inside the native caption band."""
    from ctypes import byref, sizeof, windll, wintypes

    hwnd = _hwnd(window)
    outer = wintypes.RECT()
    windll.user32.GetWindowRect(hwnd, byref(outer))
    client_origin = wintypes.POINT(0, 0)
    windll.user32.ClientToScreen(hwnd, byref(client_origin))

    caption_height = max(client_origin.y - outer.top, 1)
    y = outer.top + _TOP_NUDGE
    btn_height = max(caption_height - _TOP_NUDGE - _BOTTOM_GAP, 1)

    x = outer.left
    try:
        extended = wintypes.RECT()
        if (
            windll.dwmapi.DwmGetWindowAttribute(  # type: ignore[attr-defined]
                hwnd,
                9,  # DWMWA_EXTENDED_FRAME_BOUNDS
                byref(extended),
                sizeof(extended),
            )
            == 0
        ):
            x = extended.left
    except (ImportError, AttributeError, OSError):
        inset = windll.user32.GetSystemMetrics(92)  # SM_CXPADDEDBORDER
        if inset < 1:
            inset = 7
        x = outer.left + inset

    x += _LEFT_NUDGE
    return btn_width, btn_height, x, y


def _draw_tl_rounded_rect(
    canvas: tk.Canvas, width: int, height: int, fill: str, radius: int
) -> None:
    canvas.delete("bg")
    r = min(radius, width, height)
    if r <= 0:
        canvas.create_rectangle(0, 0, width, height, fill=fill, outline=fill, tags="bg")
        return
    canvas.create_rectangle(r, 0, width, height, fill=fill, outline=fill, tags="bg")
    canvas.create_rectangle(0, r, r, height, fill=fill, outline=fill, tags="bg")
    canvas.create_arc(
        0,
        0,
        r * 2,
        r * 2,
        start=90,
        extent=90,
        style=tk.PIESLICE,
        fill=fill,
        outline=fill,
        tags="bg",
    )


class _CaptionPinButton(tk.Canvas):
    """Caption-sized hit target with only the top-left corner rounded."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        transparent_bg: str,
        command: Callable[[], None] | None,
    ) -> None:
        super().__init__(
            master,
            highlightthickness=0,
            bd=0,
            bg=transparent_bg,
            cursor="hand2",
        )
        self._command = command
        self._hover = False
        self._icon_image = _make_exit_icon_image()
        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _fill(self) -> str:
        return _CAPTION_HOVER_HEX if self._hover else _CAPTION_BG_HEX

    def _redraw(self, _event=None) -> None:
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 2 or height < 2:
            return
        _draw_tl_rounded_rect(self, width, height, self._fill(), _CORNER_RADIUS)
        self.delete("icon")
        self.create_image(
            width // 2,
            height // 2,
            image=self._icon_image,
            tags="icon",
        )

    def _on_click(self, _event=None) -> None:
        if self._command is not None:
            self._command()

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._redraw()


class TitleBarButtonOverlay(ctk.CTkToplevel):
    """Transparent overlay with one button aligned into the native caption area."""

    def __init__(
        self,
        master: ctk.CTk,
        *,
        command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        if not sys.platform.startswith("win"):
            raise OSError("TitleBarButtonOverlay is supported on Windows only.")

        self._master = master
        self._command = command
        self._visible = False
        self._width = _caption_button_width()

        self.after(100, lambda: self.overrideredirect(True))

        self._transparent = self._apply_appearance_mode(self._fg_color)
        self.attributes("-transparentcolor", self._transparent)
        self.configure(background=self._transparent)
        self.transient(master)
        self.resizable(False, False)

        self._button = _CaptionPinButton(
            self,
            transparent_bg=self._transparent,
            command=self._command,
        )
        self._button.pack(fill="both", expand=True, padx=0, pady=0)

        master.bind("<Configure>", self._sync_geometry, add="+")
        master.bind("<Map>", self._sync_geometry, add="+")
        master.bind("<Destroy>", self._on_master_destroy, add="+")

        self.withdraw()

    def _on_master_destroy(self, event: tk.Event) -> None:
        if event.widget is self._master:
            self.destroy()

    def set_command(self, command: Callable[[], None]) -> None:
        self._command = command
        self._button._command = command

    def show(self) -> None:
        if not self.winfo_exists():
            return
        self._visible = True
        self.attributes("-topmost", True)
        self._sync_geometry()
        self.lift()

    def hide(self) -> None:
        if not self.winfo_exists():
            self._visible = False
            return
        self._visible = False
        self.attributes("-topmost", False)
        self.withdraw()

    def _sync_geometry(self, _event=None) -> None:
        if not self._visible or not self.winfo_exists():
            return
        try:
            if not self._master.winfo_viewable() or self._master.state() == "iconic":
                self.withdraw()
                return

            width, height, x, y = _caption_layout(self._master, self._width)
            if self._master.state() == "zoomed":
                x -= 7
                y += 4

            self.geometry(f"{width}x{height}+{x}+{y}")
            self.deiconify()
            self._button._redraw()
        except Exception:
            pass
