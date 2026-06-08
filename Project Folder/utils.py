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