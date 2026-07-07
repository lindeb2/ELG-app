"""
Diagnostic tests for CtkSmartScrollableFrame scrollbar visibility.

Not part of the packaged app — run manually during development.

Run all automated tests:
    python tools/test_ctk_smart_scrollable_frame.py

Run a single test class or method:
    python tools/test_ctk_smart_scrollable_frame.py TestParentGeometry
    python -m unittest discover -s tools -p test_ctk_smart_scrollable_frame.py -k TestInitTiming

Open an interactive visual harness (manual inspection):
    python tools/test_ctk_smart_scrollable_frame.py --visual
"""
from __future__ import annotations

import argparse
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import customtkinter as ctk

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from CtkSmartScrollableFrame import CtkSmartScrollableFrame  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def flush_events(root: ctk.CTk, *, extra_idle: int = 0, max_updates: int = 8) -> None:
    """Process pending Tk events so geometry and after_idle callbacks run."""
    for _ in range(extra_idle + 1):
        root.update_idletasks()
    for _ in range(max_updates):
        root.update()
        root.update_idletasks()
        if not root.tk.call("info", "exists", root._w):
            break


@dataclass(frozen=True)
class ScrollbarState:
    is_mapped: bool
    manager: str
    should_show: bool
    canvas_width: int
    canvas_height: int
    scrollregion: tuple[int, int, int, int] | None
    content_width: int
    content_height: int

    @property
    def is_visible(self) -> bool:
        return self.is_mapped and self.manager == "grid"

    def __str__(self) -> str:
        region = self.scrollregion or (0, 0, 0, 0)
        return (
            f"visible={self.is_visible} should_show={self.should_show} "
            f"mapped={self.is_mapped} manager={self.manager!r} "
            f"canvas=({self.canvas_width}x{self.canvas_height}) "
            f"content=({self.content_width}x{self.content_height}) "
            f"scrollregion={region}"
        )


def read_scrollbar_state(frame: CtkSmartScrollableFrame) -> ScrollbarState:
    canvas = frame._parent_canvas
    region = canvas.bbox("all")
    if region:
        content_width = region[2] - region[0]
        content_height = region[3] - region[1]
    else:
        content_width = content_height = 0

    return ScrollbarState(
        is_mapped=bool(frame._scrollbar.winfo_ismapped()),
        manager=frame._scrollbar.winfo_manager(),
        should_show=frame._scrollbar_should_show(),
        canvas_width=canvas.winfo_width(),
        canvas_height=canvas.winfo_height(),
        scrollregion=region,
        content_width=content_width,
        content_height=content_height,
    )


def mount_parent(
    root: ctk.CTk,
    child: CtkSmartScrollableFrame,
    geometry: str,
    *,
    fixed: bool = False,
    **kwargs,
) -> None:
    if fixed:
        # Size comes from CtkSmartScrollableFrame constructor, not place().
        child.place(x=8, y=8)
        return

    if geometry == "pack":
        child.pack(fill="both", expand=True, **kwargs)
    elif geometry == "grid":
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=1)
        child.grid(row=0, column=0, sticky="nsew", **kwargs)
    elif geometry == "place":
        child.place(relx=0, rely=0, relwidth=1, relheight=1, **kwargs)
    else:
        raise ValueError(f"unknown geometry {geometry!r}")


def add_vertical_content_pack(frame: CtkSmartScrollableFrame, count: int, height: int = 30) -> list[ctk.CTkLabel]:
    labels: list[ctk.CTkLabel] = []
    for index in range(count):
        label = ctk.CTkLabel(frame, text=f"row {index}", height=height)
        label.pack(fill="x", padx=4, pady=2)
        labels.append(label)
    return labels


def add_vertical_content_grid(frame: CtkSmartScrollableFrame, count: int, height: int = 30) -> list[ctk.CTkLabel]:
    labels: list[ctk.CTkLabel] = []
    for index in range(count):
        label = ctk.CTkLabel(frame, text=f"row {index}", height=height)
        label.grid(row=index, column=0, sticky="ew", padx=4, pady=2)
        labels.append(label)
    return labels


def add_vertical_content_place(frame: CtkSmartScrollableFrame, count: int, height: int = 30) -> list[ctk.CTkLabel]:
    labels: list[ctk.CTkLabel] = []
    for index in range(count):
        label = ctk.CTkLabel(frame, text=f"row {index}", height=height)
        label.place(x=4, y=4 + index * (height + 4), relwidth=0.95)
        labels.append(label)
    return labels


def add_horizontal_content_pack(frame: CtkSmartScrollableFrame, count: int, width: int = 120) -> list[ctk.CTkLabel]:
    row = ctk.CTkFrame(frame, fg_color="transparent")
    row.pack(fill="x")
    labels: list[ctk.CTkLabel] = []
    for index in range(count):
        label = ctk.CTkLabel(row, text=f"col {index}", width=width)
        label.pack(side="left", padx=4, pady=2)
        labels.append(label)
    return labels


CONTENT_BUILDERS: dict[str, Callable[[CtkSmartScrollableFrame, int], object]] = {
    "pack": lambda frame, count: add_vertical_content_pack(frame, count),
    "grid": lambda frame, count: add_vertical_content_grid(frame, count),
    "place": lambda frame, count: add_vertical_content_place(frame, count),
}


def assert_scrollbar_hidden(test: unittest.TestCase, frame: CtkSmartScrollableFrame, context: str) -> ScrollbarState:
    state = read_scrollbar_state(frame)
    test.assertFalse(
        state.is_visible,
        f"{context}: scrollbar should be hidden but {state}",
    )
    test.assertFalse(
        state.should_show,
        f"{context}: _scrollbar_should_show() should be False but {state}",
    )
    return state


def assert_scrollbar_visible(test: unittest.TestCase, frame: CtkSmartScrollableFrame, context: str) -> ScrollbarState:
    state = read_scrollbar_state(frame)
    test.assertTrue(
        state.is_visible,
        f"{context}: scrollbar should be visible but {state}",
    )
    test.assertTrue(
        state.should_show,
        f"{context}: _scrollbar_should_show() should be True but {state}",
    )
    return state


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class ScrollableFrameTestCase(unittest.TestCase):
    root: ctk.CTk
    _windows: list[ctk.CTk]

    @classmethod
    def setUpClass(cls) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

    def setUp(self) -> None:
        self.root = ctk.CTk()
        self.root.geometry("480x360")
        self.root.withdraw()
        self._windows = [self.root]

    def tearDown(self) -> None:
        for window in self._windows:
            for child in window.winfo_children():
                try:
                    child.destroy()
                except Exception:
                    pass
            try:
                window.destroy()
            except Exception:
                pass

    def make_frame(
        self,
        *,
        width: int = 200,
        height: int = 200,
        orientation: str = "vertical",
        label_text: str = "",
        fg_color: str | tuple[str, str] = "#2B2B2B",
        mount: str | None = None,
        fixed: bool = False,
    ) -> CtkSmartScrollableFrame:
        frame = CtkSmartScrollableFrame(
            self.root,
            width=width,
            height=height,
            orientation=orientation,  # type: ignore[arg-type]
            label_text=label_text,
            fg_color=fg_color,
        )
        if mount is not None:
            mount_parent(
                self.root,
                frame,
                mount,
                fixed=fixed,
            )
        self.root.deiconify()
        flush_events(self.root)
        return frame

    def destroy_frame(self, frame: CtkSmartScrollableFrame) -> None:
        frame.destroy()
        self.settle()

    def settle(self, extra_idle: int = 1, max_updates: int = 8) -> None:
        flush_events(self.root, extra_idle=extra_idle, max_updates=max_updates)

    def settle_once(self) -> None:
        self.root.update_idletasks()
        self.root.update()

    def make_frame_in_container(
        self,
        geometry: str,
        *,
        width: int = 220,
        height: int = 120,
        orientation: str = "vertical",
        label_text: str = "",
    ) -> CtkSmartScrollableFrame:
        """Mount a sized container with pack/grid/place, then place the scroll frame inside."""
        container = ctk.CTkFrame(self.root, width=width + 16, height=height + 16, fg_color="#1A1A1A")
        mount_parent(self.root, container, geometry)
        container.pack_propagate(False)
        container.grid_propagate(False)

        frame = CtkSmartScrollableFrame(
            container,
            width=width,
            height=height,
            orientation=orientation,  # type: ignore[arg-type]
            label_text=label_text,
        )
        frame.pack(padx=8, pady=8)
        self.root.deiconify()
        flush_events(self.root)
        return frame


# ---------------------------------------------------------------------------
# Init timing — primary suspect for spurious scrollbar at startup
# ---------------------------------------------------------------------------

class TestInitTiming(ScrollableFrameTestCase):
    def test_end_of_init_scrollbar_removed_before_mount(self) -> None:
        frame = CtkSmartScrollableFrame(self.root, width=200, height=200)
        state = read_scrollbar_state(frame)
        self.assertFalse(state.is_visible, state)
        self.assertEqual(state.manager, "", "grid_remove should leave no geometry manager")

    def test_end_of_init_scrollbar_state_when_parent_already_mapped(self) -> None:
        self.root.deiconify()
        flush_events(self.root)
        frame = CtkSmartScrollableFrame(self.root, width=200, height=200)
        state = read_scrollbar_state(frame)
        self.assertFalse(state.is_visible, state)

    def test_settled_empty_parent_pack(self) -> None:
        frame = CtkSmartScrollableFrame(self.root, width=200, height=200)
        mount_parent(self.root, frame, "pack", fixed=True)
        self.root.deiconify()
        self.settle()
        assert_scrollbar_hidden(self, frame, "settled empty, parent=pack")

    def test_settled_empty_parent_grid(self) -> None:
        frame = CtkSmartScrollableFrame(self.root, width=200, height=200)
        mount_parent(self.root, frame, "grid", fixed=True)
        self.root.deiconify()
        self.settle()
        assert_scrollbar_hidden(self, frame, "settled empty, parent=grid")

    def test_settled_empty_parent_place(self) -> None:
        frame = CtkSmartScrollableFrame(self.root, width=200, height=200)
        mount_parent(self.root, frame, "place", fixed=True)
        self.root.deiconify()
        self.settle()
        assert_scrollbar_hidden(self, frame, "settled empty, parent=place")

    def test_unmounted_frame_inner_frame_not_mapped(self) -> None:
        frame = self.make_frame()
        self.assertFalse(frame.winfo_ismapped())
        state = read_scrollbar_state(frame)
        self.assertFalse(state.is_visible, state)

    def test_unmounted_should_not_show_for_empty_content(self) -> None:
        frame = self.make_frame(width=200, height=200)
        state = read_scrollbar_state(frame)
        self.assertFalse(state.should_show, state)

    def test_immediately_after_pack_before_flush(self) -> None:
        frame = CtkSmartScrollableFrame(self.root, width=200, height=200)
        mount_parent(self.root, frame, "pack")
        self.root.deiconify()
        # Deliberately do NOT flush yet — captures earliest post-mount state.
        state = read_scrollbar_state(frame)
        # Record state for diagnosis; empty content should stay hidden once settled.
        self._record_immediate_state = state

        self.settle()
        assert_scrollbar_hidden(self, frame, "after settle (empty, pack)")

    def test_immediately_after_grid_before_flush(self) -> None:
        frame = CtkSmartScrollableFrame(self.root, width=200, height=200)
        mount_parent(self.root, frame, "grid")
        self.root.deiconify()
        state = read_scrollbar_state(frame)
        self._record_immediate_state = state

        self.settle()
        assert_scrollbar_hidden(self, frame, "after settle (empty, grid)")

    def test_immediately_after_place_before_flush(self) -> None:
        frame = CtkSmartScrollableFrame(self.root, width=200, height=200)
        mount_parent(self.root, frame, "place")
        self.root.deiconify()
        state = read_scrollbar_state(frame)
        self._record_immediate_state = state

        self.settle()
        assert_scrollbar_hidden(self, frame, "after settle (empty, place)")

    def test_post_init_500ms_bbox_callback_does_not_show_empty(self) -> None:
        frame = self.make_frame(mount="pack")
        self.root.after(600, lambda: None)
        self.settle(extra_idle=2)
        self.root.update()
        assert_scrollbar_hidden(self, frame, "after 500ms init bbox refresh")


# ---------------------------------------------------------------------------
# Parent geometry managers
# ---------------------------------------------------------------------------

class TestParentGeometry(ScrollableFrameTestCase):
    GEOMETRIES = ("pack", "grid", "place")

    def _run_empty(self, geometry: str) -> None:
        frame = self.make_frame(width=220, height=180, mount=geometry)
        assert_scrollbar_hidden(self, frame, f"empty content, parent={geometry}")

    def _run_overflow(self, geometry: str) -> None:
        frame = self.make_frame(width=220, height=120, mount=geometry)
        add_vertical_content_pack(frame, 20, height=28)
        self.settle()
        assert_scrollbar_visible(self, frame, f"overflow, parent={geometry}")

    def _run_resize_to_hide(self, geometry: str) -> None:
        frame = self.make_frame_in_container(geometry, width=220, height=120)
        labels = add_vertical_content_pack(frame, 12, height=28)
        self.settle()
        assert_scrollbar_visible(self, frame, f"before resize, parent={geometry}")

        for label in labels:
            label.destroy()
        frame.configure(height=300)
        self.settle()
        # Widget should hide scrollbar once content no longer overflows. A stale
        # scrollregion after child destroy is a known failure mode under test.
        assert_scrollbar_hidden(self, frame, f"after shrink content + grow frame, parent={geometry}")

    def test_pack_empty_vertical(self) -> None:
        self._run_empty("pack")

    def test_grid_empty_vertical(self) -> None:
        self._run_empty("grid")

    def test_place_empty_vertical(self) -> None:
        self._run_empty("place")

    def test_pack_overflow_vertical(self) -> None:
        self._run_overflow("pack")

    def test_grid_overflow_vertical(self) -> None:
        self._run_overflow("grid")

    def test_place_overflow_vertical(self) -> None:
        self._run_overflow("place")

    def test_pack_resize_hides_scrollbar(self) -> None:
        self._run_resize_to_hide("pack")

    def test_grid_resize_hides_scrollbar(self) -> None:
        self._run_resize_to_hide("grid")

    def test_place_resize_hides_scrollbar(self) -> None:
        self._run_resize_to_hide("place")


# ---------------------------------------------------------------------------
# Child geometry managers inside the scrollable inner frame
# ---------------------------------------------------------------------------

class TestChildGeometry(ScrollableFrameTestCase):
    def _assert_child_layout(
        self,
        child_geometry: str,
        *,
        empty: bool,
        overflow: bool,
    ) -> None:
        frame = self.make_frame(width=240, height=140, mount="pack", fixed=True)
        builder = CONTENT_BUILDERS[child_geometry]

        if not empty:
            builder(frame, 3 if not overflow else 18)
            self.settle()

        if empty:
            assert_scrollbar_hidden(self, frame, f"child={child_geometry} empty")
        else:
            if overflow:
                assert_scrollbar_visible(self, frame, f"child={child_geometry} overflow")
            else:
                assert_scrollbar_hidden(self, frame, f"child={child_geometry} small content")

    def test_child_pack_empty(self) -> None:
        self._assert_child_layout("pack", empty=True, overflow=False)

    def test_child_grid_empty(self) -> None:
        self._assert_child_layout("grid", empty=True, overflow=False)

    def test_child_place_empty(self) -> None:
        self._assert_child_layout("place", empty=True, overflow=False)

    def test_child_pack_small_content(self) -> None:
        self._assert_child_layout("pack", empty=False, overflow=False)

    def test_child_grid_small_content(self) -> None:
        self._assert_child_layout("grid", empty=False, overflow=False)

    def test_child_place_small_content(self) -> None:
        self._assert_child_layout("place", empty=False, overflow=False)

    def test_child_pack_overflow(self) -> None:
        self._assert_child_layout("pack", empty=False, overflow=True)

    def test_child_grid_overflow(self) -> None:
        self._assert_child_layout("grid", empty=False, overflow=True)

    def test_child_place_overflow(self) -> None:
        self._assert_child_layout("place", empty=False, overflow=True)


# ---------------------------------------------------------------------------
# Orientation, labels, colors, dynamic content
# ---------------------------------------------------------------------------

class TestVariantsAndEdgeCases(ScrollableFrameTestCase):
    def test_horizontal_empty(self) -> None:
        frame = self.make_frame(width=260, height=80, orientation="horizontal", mount="pack")
        assert_scrollbar_hidden(self, frame, "horizontal empty")

    def test_horizontal_overflow(self) -> None:
        frame = self.make_frame(width=180, height=70, orientation="horizontal", mount="pack")
        add_horizontal_content_pack(frame, 12, width=100)
        self.settle()
        assert_scrollbar_visible(self, frame, "horizontal overflow")

    def test_with_label_empty(self) -> None:
        frame = self.make_frame(width=220, height=160, label_text="Section", mount="grid")
        assert_scrollbar_hidden(self, frame, "labeled empty")

    def test_transparent_background_empty(self) -> None:
        frame = self.make_frame(width=220, height=160, fg_color="transparent", mount="pack")
        assert_scrollbar_hidden(self, frame, "transparent empty")

    def test_tiny_frame_empty(self) -> None:
        frame = self.make_frame(width=60, height=40, mount="pack")
        assert_scrollbar_hidden(self, frame, "tiny empty")

    def test_exact_fit_content_no_scrollbar(self) -> None:
        frame = self.make_frame(width=220, height=160, mount="pack")
        # Three 30px rows + padding should fit in 160px canvas area.
        add_vertical_content_pack(frame, 3, height=30)
        self.settle()
        assert_scrollbar_hidden(self, frame, "exact-ish fit")

    def test_dynamic_add_then_remove(self) -> None:
        frame = self.make_frame(width=220, height=120, mount="pack", fixed=True)
        labels = add_vertical_content_pack(frame, 16, height=30)
        self.settle()
        assert_scrollbar_visible(self, frame, "after dynamic add")

        for label in labels:
            label.destroy()
        self.settle()
        # Expect scrollbar to hide once children are gone and scrollregion shrinks.
        assert_scrollbar_hidden(self, frame, "after dynamic remove")

    def test_late_mount_after_children_added(self) -> None:
        frame = CtkSmartScrollableFrame(self.root, width=220, height=120)
        add_vertical_content_pack(frame, 2, height=24)
        state_before_mount = read_scrollbar_state(frame)

        mount_parent(self.root, frame, "pack")
        self.root.deiconify()
        state_immediate = read_scrollbar_state(frame)
        self.settle()
        state_after = read_scrollbar_state(frame)

        self.assertFalse(state_before_mount.is_visible, state_before_mount)
        assert_scrollbar_hidden(self, frame, "late mount small content settled")
        # Keep immediate state attached for failure diagnosis.
        self._late_mount_immediate = state_immediate
        self._late_mount_after = state_after

    @unittest.skip("frame.configure(height=) can stall the test runner due to Tk geometry churn")
    def test_configure_height_smaller_requests_overflow(self) -> None:
        frame = self.make_frame(width=220, height=220, mount="pack", fixed=True)
        add_vertical_content_pack(frame, 5, height=30)
        self.settle()
        frame.configure(height=100)
        self.settle_once()
        self.assertTrue(read_scrollbar_state(frame).should_show)

    def test_small_canvas_with_overflow_from_start(self) -> None:
        frame = self.make_frame(width=220, height=100, mount="pack", fixed=True)
        add_vertical_content_pack(frame, 8, height=30)
        self.settle()
        assert_scrollbar_visible(self, frame, "small canvas created with overflowing content")

    def test_nested_scrollable_frames_empty(self) -> None:
        outer = self.make_frame(width=260, height=200, mount="pack")
        inner = CtkSmartScrollableFrame(outer, width=180, height=120)
        inner.pack(padx=8, pady=8)
        self.settle()
        assert_scrollbar_hidden(self, outer, "outer nested empty")
        assert_scrollbar_hidden(self, inner, "inner nested empty")


# ---------------------------------------------------------------------------
# Regression harness mirroring meeting_point_manager workaround pattern
# ---------------------------------------------------------------------------

class TestSettingsLaunchScenario(ScrollableFrameTestCase):
    """Mimics settings_frame: default-size scroll, grid+nsew, content added during build."""

    def _build_settings_like_view(self) -> CtkSmartScrollableFrame:
        host = ctk.CTkFrame(self.root, fg_color="#1E1E1E")
        host.grid(row=0, column=0, sticky="nsew")
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        shell = ctk.CTkFrame(host, fg_color="transparent")
        shell.pack(fill="both", expand=True)
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(shell, text="Settings", font=("Segoe UI", 28, "bold")).grid(
            row=0, column=0, padx=24, pady=(24, 16), sticky="w"
        )

        scroll = CtkSmartScrollableFrame(shell, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 12))
        scroll.grid_columnconfigure(0, weight=1)

        row = 0
        for section in ("Account", "App", "Updates"):
            ctk.CTkLabel(scroll, text=section, font=("Segoe UI", 14, "bold")).grid(
                row=row, column=0, padx=4, pady=(0, 6), sticky="w"
            )
            row += 1
            card = ctk.CTkFrame(scroll, fg_color="#2B2B2B")
            card.grid(row=row, column=0, sticky="ew", pady=(0, 20))
            card.grid_columnconfigure(0, weight=1)
            for index in range(3):
                ctk.CTkLabel(card, text=f"{section} option {index}", height=28).grid(
                    row=index, column=0, sticky="ew", padx=12, pady=4
                )
            row += 1

        return scroll

    def test_launch_fitting_content_hides_scrollbar_after_layout(self) -> None:
        self.root.geometry("520x720")
        scroll = self._build_settings_like_view()
        self.root.deiconify()
        self.settle_once()

        # Allow post-map layout passes (same delays as widget uses on Map).
        self.root.after(400, lambda: None)
        self.settle()
        assert_scrollbar_hidden(self, scroll, "settings-like launch after layout")

    def test_launch_shrink_then_expand_hides_scrollbar(self) -> None:
        self.root.geometry("520x720")
        scroll = self._build_settings_like_view()
        self.root.deiconify()
        self.settle()

        self.root.geometry("520x360")
        self.settle()
        assert_scrollbar_visible(self, scroll, "settings-like shrunk")

        self.root.geometry("520x720")
        self.settle()
        assert_scrollbar_hidden(self, scroll, "settings-like re-expanded")


class TestBugFixFramePattern(ScrollableFrameTestCase):
    """Replicates the explicit-height CTkFrame workaround used in meeting_point_manager."""

    def test_pack_sized_bug_fix_frame_small(self) -> None:
        frame = self.make_frame(width=240, height=180, mount="grid")
        bug_fix = ctk.CTkFrame(frame, height=3 * 40, corner_radius=0, fg_color="#2B2B2B")
        bug_fix.pack()
        for index in range(3):
            ctk.CTkLabel(bug_fix, text=f"point {index}", height=36).pack(fill="x", padx=4, pady=2)
        self.settle()
        assert_scrollbar_hidden(self, frame, "bug_fix frame fits")

    def test_pack_sized_bug_fix_frame_overflow(self) -> None:
        frame = self.make_frame(width=240, height=120, mount="grid")
        bug_fix = ctk.CTkFrame(frame, height=12 * 40, corner_radius=0, fg_color="#2B2B2B")
        bug_fix.pack()
        for index in range(12):
            ctk.CTkLabel(bug_fix, text=f"point {index}", height=36).pack(fill="x", padx=4, pady=2)
        self.settle()
        assert_scrollbar_visible(self, frame, "bug_fix frame overflows")


# ---------------------------------------------------------------------------
# Diagnostic sweep — always prints a matrix, even when tests pass
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    state: ScrollbarState
    expected_visible: bool

    @property
    def ok(self) -> bool:
        return self.state.is_visible == self.expected_visible

    def __str__(self) -> str:
        status = "OK" if self.ok else "FAIL"
        return f"[{status}] {self.name}: expected_visible={self.expected_visible} | {self.state}"


def run_diagnostic_matrix() -> list[ScenarioResult]:
    results: list[ScenarioResult] = []

    root = ctk.CTk()
    root.geometry("500x400")

    matrix: list[tuple[str, bool, Callable[[ctk.CTkFrame], CtkSmartScrollableFrame]]] = [
        ("empty/default", False, lambda parent: CtkSmartScrollableFrame(parent, width=200, height=200)),
        ("empty/tiny", False, lambda parent: CtkSmartScrollableFrame(parent, width=50, height=40)),
        ("empty/transparent", False, lambda parent: CtkSmartScrollableFrame(parent, width=200, height=200, fg_color="transparent")),
        ("empty/label", False, lambda parent: CtkSmartScrollableFrame(parent, width=200, height=200, label_text="Title")),
        ("fit/pack-3", False, lambda parent: CtkSmartScrollableFrame(parent, width=220, height=160)),
        ("overflow/pack-20", True, lambda parent: CtkSmartScrollableFrame(parent, width=220, height=120)),
        ("overflow/grid-20", True, lambda parent: CtkSmartScrollableFrame(parent, width=220, height=120)),
        ("overflow/place-20", True, lambda parent: CtkSmartScrollableFrame(parent, width=220, height=120)),
        ("horizontal/overflow", True, lambda parent: CtkSmartScrollableFrame(parent, width=160, height=70, orientation="horizontal")),
    ]

    for name, expected_visible, make_frame in matrix:
        host = ctk.CTkFrame(root, width=260, height=200)
        host.pack(padx=8, pady=8)
        host.pack_propagate(False)
        frame = make_frame(host)
        frame.pack(padx=8, pady=8, anchor="nw")
        root.deiconify()

        if name == "fit/pack-3":
            add_vertical_content_pack(frame, 3, height=30)
        elif name == "overflow/pack-20":
            add_vertical_content_pack(frame, 20, height=28)
        elif name == "overflow/grid-20":
            add_vertical_content_grid(frame, 20, height=28)
        elif name == "overflow/place-20":
            add_vertical_content_place(frame, 20, height=28)
        elif name == "horizontal/overflow":
            add_horizontal_content_pack(frame, 12, width=100)

        flush_events(root, extra_idle=2)
        frame._apply_scrollbar_visibility()
        flush_events(root)
        results.append(ScenarioResult(name, read_scrollbar_state(frame), expected_visible))
        host.destroy()

    for geometry in ("pack", "grid", "place"):
        host = ctk.CTkFrame(root, width=260, height=200)
        host.pack(padx=8, pady=8)
        host.pack_propagate(False)
        frame = CtkSmartScrollableFrame(host, width=220, height=180)
        mount_parent(host, frame, geometry, fixed=True)
        root.deiconify()
        flush_events(root)
        results.append(
            ScenarioResult(
                f"parent/{geometry}/empty",
                read_scrollbar_state(frame),
                False,
            )
        )
        host.destroy()

    root.destroy()
    return results


def print_diagnostic_report(results: Iterable[ScenarioResult]) -> int:
    failures = 0
    print("\n=== CtkSmartScrollableFrame diagnostic matrix ===")
    for item in results:
        print(item)
        if not item.ok:
            failures += 1
    print(f"\n{failures} scenario(s) mismatched expected visibility.\n")
    return failures


# ---------------------------------------------------------------------------
# Optional visual harness
# ---------------------------------------------------------------------------

def run_visual_harness() -> None:
    root = ctk.CTk()
    root.title("CtkSmartScrollableFrame visual harness")
    root.geometry("720x520")

    sidebar = ctk.CTkFrame(root, width=180)
    sidebar.pack(side="left", fill="y")
    preview = ctk.CTkFrame(root)
    preview.pack(side="right", fill="both", expand=True)

    status = ctk.CTkLabel(root, text="", anchor="w")
    status.pack(side="bottom", fill="x", padx=8, pady=4)

    def load(name: str) -> None:
        for child in preview.winfo_children():
            child.destroy()
        frame = CtkSmartScrollableFrame(preview, width=280, height=220, label_text=name)
        frame.pack(padx=12, pady=12, fill="both", expand=True)
        if name == "fit":
            add_vertical_content_pack(frame, 3, height=30)
        elif name == "overflow":
            add_vertical_content_pack(frame, 18, height=28)
        elif name == "place-content":
            add_vertical_content_place(frame, 10, height=28)

        def refresh() -> None:
            flush_events(root)
            status.configure(text=f"{name} -> {read_scrollbar_state(frame)}")

        refresh()
        root.after(300, refresh)

    for label in ("empty", "fit", "overflow", "place-content"):
        ctk.CTkButton(sidebar, text=label, command=lambda n=label: load(n)).pack(fill="x", padx=8, pady=4)

    load("empty")
    root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visual", action="store_true", help="Open manual visual harness.")
    parser.add_argument("--matrix", action="store_true", help="Print diagnostic matrix only.")
    parser.add_argument("tests", nargs="*", help="Optional unittest names to run.")
    args = parser.parse_args(argv)

    if args.visual:
        run_visual_harness()
        return 0

    if args.matrix:
        return print_diagnostic_report(run_diagnostic_matrix())

    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    if args.tests:
        suite = unittest.TestSuite()
        loader = unittest.TestLoader()
        for name in args.tests:
            suite.addTests(loader.loadTestsFromName(name, module=sys.modules[__name__]))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if not args.tests:
        matrix_failures = print_diagnostic_report(run_diagnostic_matrix())
    else:
        matrix_failures = 0

    return 0 if result.wasSuccessful() and matrix_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
