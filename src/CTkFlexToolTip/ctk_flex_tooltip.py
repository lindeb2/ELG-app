"""
CTkToolTip Widget
version: 0.8
"""

import time
import sys
import customtkinter
from tkinter import Toplevel, Frame

class CTkFlexToolTip(Toplevel):
    """
    Creates a ToolTip (pop-up) widget for customtkinter.
    """

    def __init__(
            self,
            widget: any = None,
            message: str = None,
            delay: float = 0.2,
            follow: bool = True,
            x_offset: int = +20,
            y_offset: int = +10,
            bg_color: str = None,
            corner_radius: int = 10,
            border_width: int = 0,
            border_color: str = None,
            alpha: float = 0.95,
            padding: tuple = (10, 2),
            static_anchor: str = None,
            **message_kwargs):

        super().__init__()

        self.widget = widget

        self.withdraw()

        # Disable ToolTip's title bar
        self.overrideredirect(True)

        if sys.platform.startswith("win"):
            self.transparent_color = self.widget._apply_appearance_mode(
                customtkinter.ThemeManager.theme["CTkToplevel"]["fg_color"])
            self.attributes("-transparentcolor", self.transparent_color)
            self.transient()
        elif sys.platform.startswith("darwin"):
            self.transparent_color = 'systemTransparent'
            self.attributes("-transparent", True)
            self.transient(self.master)
        else:
            self.transparent_color = '#000001'
            corner_radius = 0
            self.transient()

        self.resizable(width=True, height=True)

        # Make the background transparent
        self.config(background=self.transparent_color)

        # StringVar instance for msg string
        self.messageVar = customtkinter.StringVar()
        self.message = message
        self.messageVar.set(self.message)

        self.delay = delay
        self.follow = follow
        self.x_offset = x_offset
        self.y_offset = y_offset
        self.corner_radius = corner_radius
        self.alpha = alpha
        self.border_width = border_width
        self.padding = padding
        self.bg_color = customtkinter.ThemeManager.theme["CTkFrame"]["fg_color"] if bg_color is None else bg_color
        self.border_color = border_color
        self.disable = False
        self.static_anchor = static_anchor

        # visibility status of the ToolTip inside|outside|visible
        self.status = "outside"
        self.last_moved = 0
        self.attributes('-alpha', self.alpha)

        if sys.platform.startswith("win"):
            if self.widget._apply_appearance_mode(self.bg_color) == self.transparent_color:
                self.transparent_color = "#000001"
                self.config(background=self.transparent_color)
                self.attributes("-transparentcolor", self.transparent_color)

        # Add the message widget inside the tooltip
        self.transparent_frame = Frame(self, bg=self.transparent_color)
        self.transparent_frame.pack(padx=0, pady=0, fill="both", expand=True)

        self.frame = customtkinter.CTkFrame(self.transparent_frame, bg_color=self.transparent_color,
                                            corner_radius=self.corner_radius,
                                            border_width=self.border_width, fg_color=self.bg_color,
                                            border_color=self.border_color)
        self.frame.pack(padx=0, pady=0, fill="both", expand=True)

        self.message_label = customtkinter.CTkLabel(self.frame, textvariable=self.messageVar, **message_kwargs, height=0)
        self.message_label.pack(fill="both", padx=self.padding[0] + self.border_width,
                                pady=self.padding[1] + self.border_width, expand=True)

        if self.widget.winfo_name() != "tk":
            if self.frame.cget("fg_color") == self.widget.cget("bg_color"):
                if not bg_color:
                    self._top_fg_color = self.frame._apply_appearance_mode(
                        customtkinter.ThemeManager.theme["CTkFrame"]["top_fg_color"])
                    if self._top_fg_color != self.transparent_color:
                        self.frame.configure(fg_color=self._top_fg_color)

        # Add bindings to the widget without overriding the existing ones
        self.widget.bind("<Enter>", self.on_enter, add="+")
        self.widget.bind("<Leave>", self.on_leave, add="+")
        self.widget.bind("<Motion>", self.on_enter, add="+")
        self.widget.bind("<B1-Motion>", self.on_enter, add="+")
        self.widget.bind("<Destroy>", lambda _: self.hide(), add="+")

    def show(self) -> None:
        """
        Enable the widget.
        """
        self.disable = False

    def on_enter(self, event) -> None:
        """
        Processes motion within the widget including entering and moving.
        """

        if self.disable:
            return
        self.last_moved = time.time()

        # Set the status as inside for the very first time
        if self.status == "outside":
            self.status = "inside"

        # If the follow flag is not set, motion within the widget will make the ToolTip dissapear
        if not self.follow:
            self.status = "inside"
            self.withdraw()

        if self.static_anchor:
            # Get widget's absolute position and size
            widget_x = self.widget.winfo_rootx()
            widget_y = self.widget.winfo_rooty()
            widget_w = self.widget.winfo_width()
            widget_h = self.widget.winfo_height()
            self.update_idletasks()
            tip_w = self.winfo_reqwidth()
            tip_h = self.winfo_reqheight()

            anchor = self.static_anchor.lower()
            # Default offset
            offset = 0
            if anchor == "n":  # Top center
                x = widget_x + widget_w // 2 - tip_w // 2 + self.x_offset
                y = widget_y - tip_h - offset + self.y_offset
            elif anchor == "s":  # Bottom center
                x = widget_x + widget_w // 2 - tip_w // 2 + self.x_offset
                y = widget_y + widget_h + offset + self.y_offset
            elif anchor == "e":  # Right center
                x = widget_x + widget_w + offset + self.x_offset
                y = widget_y + widget_h // 2 - tip_h // 2 + self.y_offset
            elif anchor == "w":  # Left center
                x = widget_x - tip_w - offset + self.x_offset
                y = widget_y + widget_h // 2 - tip_h // 2 + self.y_offset
            elif anchor == "ne":  # Top right
                x = widget_x + widget_w + offset + self.x_offset
                y = widget_y - tip_h - offset + self.y_offset
            elif anchor == "nw":  # Top left
                x = widget_x - tip_w - offset + self.x_offset
                y = widget_y - tip_h - offset + self.y_offset
            elif anchor == "se":  # Bottom right
                x = widget_x + widget_w + offset + self.x_offset
                y = widget_y + widget_h + offset + self.y_offset
            elif anchor == "sw":  # Bottom left
                x = widget_x - tip_w - offset + self.x_offset
                y = widget_y + widget_h + offset + self.y_offset
            elif anchor == "center":
                x = widget_x + widget_w // 2 - tip_w // 2 + self.x_offset
                y = widget_y + widget_h // 2 - tip_h // 2 + self.y_offset
            else:
                # Fallback to default mouse-following if anchor is invalid
                x = event.x_root + self.x_offset
                y = event.y_root + self.y_offset
            self.geometry(f"+{x}+{y}")
        else:
            # Calculate available space on the right side of the widget relative to the screen
            root_width = self.winfo_screenwidth()
            widget_x = event.x_root
            space_on_right = root_width - widget_x

            # Calculate the width of the tooltip's text based on the length of the message string
            text_width = self.message_label.winfo_reqwidth()

            # Calculate the offset based on available space and text width to avoid going off-screen on the right side
            offset_x = self.x_offset
            if space_on_right < text_width + 20:  # Adjust the threshold as needed
                offset_x = -text_width - 20  # Negative offset when space is limited on the right side

            # Offsets the ToolTip using the coordinates od an event as an origin
            self.geometry(f"+{event.x_root + offset_x}+{event.y_root + self.y_offset}")

        # Time is in integer: milliseconds
        self.after(int(self.delay * 1000), self._show)

    def on_leave(self, event=None) -> None:
        """
        Hides the ToolTip temporarily.
        """

        if self.disable: return
        self.status = "outside"
        self.withdraw()

    def _show(self) -> None:
        """
        Displays the ToolTip.
        """

        if not self.widget.winfo_exists():
            self.hide()
            self.destroy()

        if self.status == "inside" and time.time() - self.last_moved >= self.delay:
            self.status = "visible"
            self.deiconify()

    def hide(self) -> None:
        """
        Disable the widget from appearing.
        """
        if not self.winfo_exists():
            return
        self.withdraw()
        self.disable = True

    def is_disabled(self) -> None:
        """
        Return the window state
        """
        return self.disable

    def get(self) -> None:
        """
        Returns the text on the tooltip.
        """
        return self.messageVar.get()

    def configure(self, message: str = None, delay: float = None, bg_color: str = None, **kwargs):
        """
        Set new message or configure the label parameters.
        """
        if delay: self.delay = delay
        if bg_color: self.frame.configure(fg_color=bg_color)

        self.messageVar.set(message)
        self.message_label.configure(**kwargs) 