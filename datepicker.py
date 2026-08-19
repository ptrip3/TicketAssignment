"""Date picker widgets built on plain tkinter/ttk.

Two widgets:

  * DatePicker -- an inline month grid: click a day to select it, with
    prev/next month navigation. Supports highlighting a contiguous date
    range (a darker cell at each end, lighter fill between), which is what
    the Vacation/Sick Leave range selection in name_selector.py uses.
  * DateField -- a compact read-only entry showing one date, with a button
    that drops a DatePicker down beneath it. Used where only a single date
    is needed.

Both take the app's per-theme colors dict (see TicketAssignmentApp.colors)
and paint every cell explicitly, so they follow light/dark mode without
depending on ttk theme internals. Day cells are plain tk.Labels rather
than ttk widgets specifically so each one's background can be set
directly -- that per-cell control is what makes the range bar possible,
and doing it through ttk would mean generating a named style per cell.

English-only by design, matching the rest of the app: month and weekday
names are hardcoded rather than pulled from a locale database, so there's
no dependency on the host locale (or on a locale package) and the layout
is identical on every machine.
"""

import calendar
import tkinter as tk
from tkinter import ttk
from datetime import date, timedelta

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Monday-first, matching the week layout the app has always shown.
WEEKDAY_NAMES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

_WEEK_ROWS = 6  # fixed, so the widget's height never changes between months


def _month_grid(year, month):
    """A 6x7 grid of dates for `year`/`month`, padded with the adjacent
    months' days so every month renders at the same height.
    """
    first = date(year, month, 1)
    start = first - timedelta(days=first.weekday())  # back up to Monday
    return [
        [start + timedelta(days=row * 7 + col) for col in range(7)]
        for row in range(_WEEK_ROWS)
    ]


def _add_months(d, delta):
    """`d` shifted by `delta` months, clamped to a valid day-of-month."""
    month_index = d.month - 1 + delta
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class DatePicker(ttk.Frame):
    """An inline, clickable month grid.

    Emits <<DateSelected>> whenever the user clicks a day (not when the
    selection is set programmatically, unless notify=True is passed).
    """

    def __init__(self, master, colors, initial=None, **kwargs):
        super().__init__(master, **kwargs)
        self._colors = colors
        self._selected = initial
        self._range = None  # (start, end) or None
        self._shown = initial or date.today()
        self._cells = []

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        header.grid_columnconfigure(1, weight=1)

        ttk.Button(header, text="‹", width=3, command=lambda: self._step_month(-1)).grid(
            row=0, column=0, sticky="w"
        )
        self._title_var = tk.StringVar()
        ttk.Label(header, textvariable=self._title_var, anchor="center").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(header, text="›", width=3, command=lambda: self._step_month(1)).grid(
            row=0, column=2, sticky="e"
        )

        grid = tk.Frame(self, background=colors["bg"], highlightthickness=1,
                        highlightbackground=colors["border"])
        grid.grid(row=1, column=0, sticky="nsew")
        self._grid_frame = grid

        for col, name in enumerate(WEEKDAY_NAMES):
            tk.Label(
                grid, text=name, background=colors["bg"], foreground=colors["muted_fg"],
                width=4, pady=3,
            ).grid(row=0, column=col, sticky="nsew")

        for row in range(_WEEK_ROWS):
            cell_row = []
            for col in range(7):
                # padx/pady of 0 so highlighted neighbours form one
                # continuous bar rather than separated blocks.
                cell = tk.Label(grid, width=4, pady=4, cursor="hand2")
                cell.grid(row=row + 1, column=col, sticky="nsew", padx=0, pady=0)
                cell.bind("<Button-1>", self._on_cell_click)
                cell_row.append(cell)
            self._cells.append(cell_row)

        self._render()

    # -- public API -------------------------------------------------

    def get_date(self):
        """The currently selected date, or None."""
        return self._selected

    def set_date(self, value, notify=False, show=True):
        self._selected = value
        if show and value is not None:
            self._shown = value
        self._render()
        if notify:
            self.event_generate("<<DateSelected>>")

    def clear_selection(self):
        self._selected = None
        self._render()

    def set_range(self, start, end):
        """Highlight start..end inclusive. Pass (None, None) to clear."""
        if start is None:
            self._range = None
        else:
            self._range = (start, end or start)
        self._render()

    def show_month(self, value):
        """Scroll the grid to the month containing `value`."""
        self._shown = value
        self._render()

    # -- internals --------------------------------------------------

    def _step_month(self, delta):
        self._shown = _add_months(self._shown, delta)
        self._render()

    def _on_cell_click(self, event):
        clicked = getattr(event.widget, "_date", None)
        if clicked is None:
            return
        self._selected = clicked
        if clicked.month != self._shown.month or clicked.year != self._shown.year:
            # Clicking a spillover day from an adjacent month follows it,
            # matching how most calendars behave.
            self._shown = clicked
        self._render()
        self.event_generate("<<DateSelected>>")

    def _cell_colors(self, day):
        c = self._colors
        if self._range is not None:
            start, end = self._range
            if start <= day <= end:
                if day in (start, end):
                    return c["accent_bg"], c["accent_fg"]
                return c["accent_soft_bg"], c["fg"]
        if self._selected is not None and day == self._selected:
            return c["accent_bg"], c["accent_fg"]
        if day.month != self._shown.month or day.year != self._shown.year:
            return c["bg"], c["muted_fg"]
        if day.weekday() >= 5:
            return c["weekend_bg"], c["fg"]
        return c["bg"], c["fg"]

    def _render(self):
        # A queued event (e.g. the DateField popup's <FocusOut>) can fire
        # after the containing dialog has already been destroyed, at which
        # point configuring the cells raises TclError. Nothing to redraw
        # in that case.
        if not self.winfo_exists():
            return
        self._title_var.set(f"{MONTH_NAMES[self._shown.month - 1]} {self._shown.year}")
        for row, week in enumerate(_month_grid(self._shown.year, self._shown.month)):
            for col, day in enumerate(week):
                cell = self._cells[row][col]
                background, foreground = self._cell_colors(day)
                cell.configure(text=str(day.day), background=background, foreground=foreground)
                cell._date = day


class DateField(ttk.Frame):
    """A read-only entry showing one date, plus a button that drops a
    DatePicker down beneath it. Dates display and parse as YYYY-MM-DD.
    """

    def __init__(self, master, colors, initial=None, width=12, **kwargs):
        super().__init__(master, **kwargs)
        self._colors = colors
        self._value = initial or date.today()
        self._popup = None

        self._text = tk.StringVar(value=self._value.isoformat())
        self._entry = ttk.Entry(self, textvariable=self._text, width=width, state="readonly")
        self._entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(self, text="▾", width=3, command=self._toggle_popup).grid(row=0, column=1)

    def get_date(self):
        return self._value

    def set_date(self, value):
        self._value = value
        self._text.set(value.isoformat())

    def _toggle_popup(self):
        if self._popup is not None:
            self._close_popup()
            return

        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.configure(background=self._colors["border"])
        self._popup = popup

        picker = DatePicker(popup, self._colors, initial=self._value, padding=6)
        picker.grid(row=0, column=0, padx=1, pady=1)

        def _on_pick(_event=None):
            picked = picker.get_date()
            if picked is not None:
                self.set_date(picked)
            self._close_popup()

        picker.bind("<<DateSelected>>", _on_pick)
        popup.bind("<Escape>", lambda _e: self._close_popup())
        # Dismiss on click-outside. Bound on the popup rather than grabbing
        # globally so the rest of the dialog stays responsive.
        popup.bind("<FocusOut>", lambda _e: self._close_popup())

        self.update_idletasks()
        popup.geometry(f"+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height()}")
        popup.lift()
        popup.focus_set()

    def _close_popup(self):
        popup, self._popup = self._popup, None
        if popup is not None:
            try:
                popup.destroy()
            except tk.TclError:
                # Already torn down with its parent dialog -- e.g. the user
                # hit Apply while the dropdown was still open, which
                # destroys the dialog and then delivers <FocusOut> here.
                pass
