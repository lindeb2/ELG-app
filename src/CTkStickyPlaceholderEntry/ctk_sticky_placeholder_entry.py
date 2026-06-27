import customtkinter as ctk

_NO_INPUT_KEYSYMS = frozenset({
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Meta_L", "Meta_R",
    "Caps_Lock", "Num_Lock", "Scroll_Lock", "Tab", "Escape",
    "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
    "Insert", "Pause", "Print", "Win_L", "Win_R",
})

_NAVIGATION_KEYSYMS = frozenset({"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"})


class CTkStickyPlaceholderEntry(ctk.CTkEntry):
    """CTkEntry that keeps placeholder text visible while focused until real input exists."""

    def _create_bindings(self, sequence=None):
        super()._create_bindings(sequence)
        if sequence is None or sequence == "<KeyPress>":
            self._entry.bind("<KeyPress>", self._sticky_placeholder_key_press, add=True)
        if sequence is None or sequence == "<KeyRelease>":
            self._entry.bind("<KeyRelease>", self._sticky_placeholder_key_release, add=True)
        if sequence is None:
            self._entry.bind("<<Paste>>", self._sticky_placeholder_before_input, add=True)
            self._entry.bind("<Button-1>", self._sticky_placeholder_button1, add=True)
            self._entry.bind("<B1-Motion>", self._sticky_placeholder_b1_motion, add=True)
            self._entry.bind("<Double-Button-1>", self._sticky_placeholder_button1, add=True)
            self._entry.bind("<Triple-Button-1>", self._sticky_placeholder_button1, add=True)
            self._entry.bind("<<Select>>", self._sticky_placeholder_select, add=True)

    def _reset_placeholder_caret(self):
        if self._placeholder_text_active:
            self._entry.select_clear()
            self._entry.icursor(0)

    def _activate_placeholder(self):
        super()._activate_placeholder()
        self._reset_placeholder_caret()

    def _entry_focus_in(self, event=None):
        self._is_focused = True
        self._reset_placeholder_caret()
        self.after_idle(self._reset_placeholder_caret)

    def _sticky_placeholder_select(self, event=None):
        if self._placeholder_text_active:
            self.after_idle(self._reset_placeholder_caret)

    def _sticky_placeholder_button1(self, event=None):
        if not self._placeholder_text_active:
            return
        self._entry.focus_set()
        self._reset_placeholder_caret()
        return "break"

    def _sticky_placeholder_b1_motion(self, event=None):
        if self._placeholder_text_active:
            return "break"

    def _sticky_placeholder_before_input(self, event=None):
        if self._placeholder_text_active:
            self._deactivate_placeholder()

    def _delete_selection_if_any(self) -> bool:
        if self._entry.select_present():
            self._entry.delete("sel.first", "sel.last")
            return True
        return False

    def _delete_word_left(self):
        if self._delete_selection_if_any():
            return

        text = self._entry.get()
        pos = int(self._entry.index("insert"))
        if pos == 0:
            return

        start = pos
        while start > 0 and text[start - 1].isspace():
            start -= 1
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        self._entry.delete(start, pos)

    def _delete_word_right(self):
        if self._delete_selection_if_any():
            return

        text = self._entry.get()
        pos = int(self._entry.index("insert"))
        end = pos
        while end < len(text) and text[end].isspace():
            end += 1
        while end < len(text) and not text[end].isspace():
            end += 1
        self._entry.delete(pos, end)

    def _sticky_placeholder_key_press(self, event=None):
        ctrl_held = bool(event.state & 0x4)

        if ctrl_held:
            if not self._placeholder_text_active:
                if event.keysym == "BackSpace":
                    self._delete_word_left()
                    return "break"
                if event.keysym == "Delete":
                    self._delete_word_right()
                    return "break"
                return

            if event.keysym.lower() == "v":
                self._deactivate_placeholder()
                return
            return "break"

        if not self._placeholder_text_active:
            return

        if event.keysym in ("BackSpace", "Delete"):
            return "break"

        if event.keysym in _NAVIGATION_KEYSYMS:
            return "break"

        if event.keysym in _NO_INPUT_KEYSYMS or event.keysym in ("Return", "KP_Enter"):
            return

        self._deactivate_placeholder()

    def _sticky_placeholder_key_release(self, event=None):
        if self._is_focused and not self._placeholder_text_active and self._entry.get() == "":
            self._activate_placeholder()
