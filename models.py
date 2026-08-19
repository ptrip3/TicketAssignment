"""Shared domain types used by both name_selector.py and db.py.

Split out from name_selector.py so the SQL data-access layer (db.py) can
build StatusDuration objects without importing the Tkinter app module (and
name_selector.py importing db.py) creating a circular import.
"""


class Status:
    AVAILABLE = "Available"
    OOO = "Out of Office"
    VACATION = "Vacation"
    # Display-only rename: internal key/column/CHECK-constraint values are
    # still "training". Renaming those too would mean a schema/data
    # migration for no user-visible benefit.
    TRAINING = "Out of Queue"
    SICK = "Sick Leave"
    # No longer independently settable from the UI -- Half Day is now a
    # checkbox on Vacation/Sick Leave entries (see StatusDuration.half_day)
    # rather than its own status. Kept here so old half_day_status entries
    # already in the database still display and expire correctly.
    HALF_DAY = "Half Day"


class StatusDuration:
    def __init__(self, start_date, end_date=None, half_day=False, half_day_period=None):
        self.start_date = start_date
        self.end_date = end_date
        # Only meaningful for vacation/sick_leave entries: marks a single
        # day as a half day rather than a full day off.
        self.half_day = half_day
        # Only meaningful when half_day is True: "morning" or "afternoon" --
        # which half of the person's schedule for that day they're out for.
        # None means "half day, but no period recorded" (e.g. data written
        # before this field existed), which name_selector.py treats as
        # blocking the whole day since there's nothing to split on.
        self.half_day_period = half_day_period

    def __eq__(self, other):
        if not isinstance(other, StatusDuration):
            return NotImplemented
        return (
            self.start_date == other.start_date
            and self.end_date == other.end_date
            and self.half_day == other.half_day
            and self.half_day_period == other.half_day_period
        )

    def __repr__(self):
        return (
            f"StatusDuration({self.start_date!r}, {self.end_date!r}, "
            f"half_day={self.half_day!r}, half_day_period={self.half_day_period!r})"
        )
