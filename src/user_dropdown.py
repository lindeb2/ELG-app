from __future__ import annotations
from typing import Callable
import customtkinter as ctk

class UserDropdown:
    def __init__(
        self,
        parent,
        label,
        list_frame,
        *,
        get_users: Callable[[], list[str]],
        on_select: Callable[[str], None],
    ):
        self.parent = parent
        self.label = label
        self.list_frame = list_frame
        self.get_users = get_users
        self.on_select = on_select

    def _get_selected_user(self) -> str:
        variable_name = self.label.cget("textvariable")
        if not variable_name:
            return self.label.cget("text")
        return str(self.label.getvar(variable_name))

    def _set_selected_user(self, user: str) -> None:
        variable_name = self.label.cget("textvariable")
        if variable_name:
            self.label.setvar(variable_name, user)
        else:
            self.label.configure(text=user)

    def _dropdown_enter(self, event) -> None:
        row = event.widget if isinstance(event.widget, ctk.CTkFrame) else event.widget.master
        row.configure(fg_color="#2C2C2C")

    def _dropdown_leave(self, event) -> None:
        row = event.widget if isinstance(event.widget, ctk.CTkFrame) else event.widget.master
        row.configure(fg_color="transparent")

    def create_options(self) -> None:
        selectable_users = self.get_users()
        if not selectable_users:
            return
        if self._get_selected_user() not in selectable_users:
            self._set_selected_user(selectable_users[0])

        for user in selectable_users:
            user_frame = ctk.CTkFrame(
                self.list_frame,
                fg_color="transparent",
                height=30,
            )
            user_frame.pack(fill="x", padx=5)
            user_label = ctk.CTkLabel(
                user_frame,
                text=user,
                font=("Arial", 16),
                text_color="white",
                anchor="w",
                fg_color="transparent",
            )
            user_label._label.configure(cursor="hand2")
            user_label._canvas.configure(cursor="hand2")
            user_label.pack(fill="both", expand=True)
            for widget in (user_frame, user_label):
                widget.bind("<Enter>", self._dropdown_enter)
                widget.bind("<Leave>", self._dropdown_leave)
                widget.bind("<Button-1>", lambda _event, u=user: self._select(u))

    def destroy_options(self) -> None:
        for item in self.list_frame.winfo_children():
            item.destroy()

    def refresh_options(self) -> None:
        self.destroy_options()
        self.create_options()

    def show(self, _event=None) -> None:
        self.list_frame.place(x=6, y=52, anchor="nw")
        self.parent.bind("<Button-1>", self.hide)

    def hide(self, event=None) -> None:
        self.list_frame.place_forget()
        self.parent.unbind("<Button-1>")
        if not event:
            return
        clicked_widget = self.parent.winfo_containing(event.x_root, event.y_root)
        clicked_parent = clicked_widget.master if clicked_widget else None
        if clicked_parent != self.label:
            self.label.configure(fg_color="transparent")

    def _select(self, user: str) -> None:
        self._set_selected_user(user)
        self.hide()
        self.on_select(user)
