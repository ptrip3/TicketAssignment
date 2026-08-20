import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import tkinter.font as tkfont
from collections import deque, defaultdict
import os
import time
from datetime import datetime, date, timedelta, time as dt_time
from calendar import day_name
import shutil
import threading
import configparser
import sys

import db  # noqa: F401 (module referenced as db.DEFAULT_PORT etc. below)
from db import Database, DatabaseError, empty_location_data, STATUS_TYPES
from models import Status, StatusDuration
from datepicker import DateField, DatePicker
import sv_ttk

def _try_optional_import(module_name, purpose):
    """Import an optional, feature-gating dependency without letting any
    failure in it -- not just "not installed", literally anything, since
    this runs at module load time before any of our own error handling
    exists yet -- take the whole app down with it. Returns the module, or
    None (after printing what happened) if it couldn't be loaded.

    Deliberately broader than `except ImportError`: a mismatched compiled
    wheel, a missing system library one of its dependencies needs, etc.
    would all raise something else, and this app runs fine without any of
    these optional imports -- a Mac with a half-broken pyspnego install
    should still be able to open the app and use plain SQL login, not
    fail to launch at all.
    """
    try:
        return __import__(module_name)
    except Exception as e:
        print(f"Optional dependency {module_name!r} ({purpose}) unavailable: {type(e).__name__}: {e}")
        return None


# Windows only: pyodbc is the backend there (see db.py), and this module
# reference is just for listing the installed ODBC drivers in the
# connection dialog. macOS/Linux use python-tds exclusively -- SQL Server
# login or NTLM domain login -- so pyodbc is never loaded there.
pyodbc = _try_optional_import("pyodbc", "ODBC driver list") if sys.platform == "win32" else None

def _ntlm_available():
    """Whether NTLM domain login can be offered (pyspnego importable).

    Checked lazily, when the connection dialog is actually opened, rather
    than at import time: pyspnego pulls in `cryptography` and costs well
    over 100ms to import, which is pure startup latency for a feature most
    launches never touch. Cached after the first call.

    (db.py imports pyspnego itself, via pytds, only when a connection
    actually uses NTLM -- this is purely about whether to offer it.)
    """
    if not hasattr(_ntlm_available, "_cached"):
        _ntlm_available._cached = _try_optional_import("spnego", "NTLM domain login") is not None
    return _ntlm_available._cached

APP_VERSION = "2.0.0"
APP_DATE = "2026-08-18"
APP_NAME = "Ticket Assignment"


def _app_dir():
    """The directory the running script or frozen executable lives in."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_config_dir():
    """Directory to store config.ini in, appropriate for the platform.

    Windows: %APPDATA%/Ticket Assignment. Earlier versions kept it next
    to the .exe, which only worked while the app was installed somewhere
    the user could write. Installing to Program Files makes that
    directory read-only for standard users, and settings are per-user
    anyway, so they belong in the roaming profile. _migrate_legacy_config
    moves an older config across on first run.

    macOS: ~/Library/Application Support/Ticket Assignment. Writing inside
    a signed .app bundle isn't reliable (code-signing expects the bundle's
    contents not to change after signing) and isn't where macOS apps are
    expected to keep user settings anyway.

    Anything else (e.g. Linux): ~/.config/Ticket Assignment, following the
    XDG Base Directory convention, for the same reason.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, APP_NAME)
    elif sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    else:
        return os.path.join(os.path.expanduser("~"), ".config", APP_NAME)


def _migrate_legacy_config(config_file):
    """Copy a pre-existing config.ini from beside the .exe, if there's
    one there and none at the current location yet.

    Windows builds used to keep config.ini next to the executable. Anyone
    upgrading from one of those would otherwise be asked for their
    database connection again.
    """
    if sys.platform != "win32" or os.path.exists(config_file):
        return
    legacy = os.path.join(_app_dir(), "config.ini")
    if not os.path.exists(legacy):
        return
    try:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        shutil.copyfile(legacy, config_file)
        print(f"Migrated settings from {legacy} to {config_file}")
    except OSError as e:
        print(f"Could not migrate settings from {legacy}: {e}")


_ui_font_family = None


def _font(size, *modifiers):
    """A (family, size, *modifiers) tuple for font= kwargs, using this
    platform's actual native UI font instead of hardcoding "Segoe UI",
    which doesn't exist on macOS and would silently fall back to some
    unintended default there. Queries Tk's own TkDefaultFont -- Tk already
    resolves that to the right native font per platform (Segoe UI on
    Windows, San Francisco on macOS, etc.), so there's no need to guess a
    platform-specific family name here. Requires a Tk root to already
    exist, which is true everywhere this is called from in this app.
    """
    global _ui_font_family
    if _ui_font_family is None:
        _ui_font_family = tkfont.nametofont("TkDefaultFont").actual("family")
    return (_ui_font_family, size) + modifiers


WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

# The five status dict keys in each location's data, e.g. "vacation_status".
# Derived from db.STATUS_TYPES (the single source of truth for the status
# type names themselves) instead of spelling all five out again here --
# used everywhere this app needs to loop over "every status a person could
# have" instead of each call site repeating the same 5-tuple.
STATUS_KEYS = tuple(f"{status_type}_status" for status_type in STATUS_TYPES)


class TicketAssignmentApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Ticket Assignment")
        self.root.geometry("900x660")

        self.dark_mode = tk.BooleanVar(value=False)

        self.locations = {}
        self.current_location = tk.StringVar()

        self.config = self._load_config()
        self.db = None

        verify_schema = False
        if not self._has_database_config():
            self._configure_database_connection(required=True)
            if self.db is None:
                # User cancelled first-run setup; _configure_database_connection
                # already destroyed self.root, so stop building the rest of the
                # UI on a dead window instead of crashing on missing self.db.
                return
        else:
            self.db = Database.from_config(self.config)
            try:
                # Defensive: makes config.ini alone sufficient to run the
                # app even if the tables were never created (e.g. config.ini
                # was copied by hand, or the database was recreated).
                # Deliberately does NOT create the database itself -- that's
                # a bigger, less reversible step that only happens through
                # the connection dialog, where the user confirms it.
                #
                # Folded into load_data() so startup opens one connection
                # and asks one cheap question, instead of a whole extra
                # connection replaying every DDL batch on every launch.
                verify_schema = True
            except DatabaseError as e:
                print(f"Could not verify/create database schema: {e}")

        self.load_data(verify_schema=verify_schema)

        default_location = self.config.get("Settings", "last_location", fallback=None)
        if default_location and default_location in self.locations:
            self.current_location.set(default_location)
        else:
            available_locations = list(self.locations.keys())
            if available_locations:
                self.current_location.set(available_locations[0])

        self.current_location.trace_add("write", self._on_location_changed)

        if self.current_location.get():
            self._initialize_location_data(self.current_location.get())

        self.style = ttk.Style()
        # sv_ttk (Sun Valley) is a ready-made Windows 11-style ttk theme --
        # it handles every stock ttk widget (buttons incl. "Accent.TButton",
        # Treeview, Combobox, Entry, Checkbutton, Notebook, Scrollbar, etc.)
        # with real hover/pressed/selected states, in both light and dark,
        # so the app doesn't have to hand-roll any of that.

        # sv_ttk only themes ttk widgets. What's left here is strictly for
        # things it doesn't reach: raw (non-ttk) tk widgets (status_text,
        # dialog backgrounds, the date picker's day cells) and our own
        # semantic status colors (available/unavailable), which aren't
        # part of any generic theme.
        self.colors = {
            "light": {
                "bg": "#fafafa",
                "fg": "#1c1c1c",
                "muted_fg": "#a0a0a0",
                "border": "#d0d0d0",
                "weekend_bg": "#f0f0f0",
                "status_available_fg": "#1B7A3D",
                "status_unavailable_fg": "#B45309",
                # Soft fill for the days *between* a picked date range in
                # the status dialog's calendar -- the start/end days
                # themselves use accent_bg, matching the reference of a
                # dark bar at each end with a lighter bar between.
                "accent_soft_bg": "#CFE3F7",
            },
            "dark": {
                "bg": "#1c1c1c",
                "fg": "#fafafa",
                "muted_fg": "#595959",
                "border": "#404040",
                "weekend_bg": "#2b2b2b",
                "status_available_fg": "#4ADE80",
                "status_unavailable_fg": "#FBBF24",
                "accent_soft_bg": "#1F3A52",
            },
        }
        # sv_ttk's own selection blue -- same value in both its light and
        # dark themes, and already what Treeview/Combobox selection looks
        # like under this theme, so reusing it here keeps the calendar's
        # highlighted dates visually consistent with the rest of the app.
        for theme_colors in self.colors.values():
            theme_colors["accent_bg"] = "#2f60d8"
            theme_colors["accent_fg"] = "#ffffff"

        self.is_initialized = False
        self.refresh_thread = None
        self.status_text = None

        self.current_name_var = None
        self.schedule_var = None
        self.schedule_name_var = None
        self.schedule_day_var = None
        self.time_var = None
        self.bulk_time_var = None
        self.name_status_tree = None
        self.schedule_tree = None
        self.stats_tree = None
        self.daily_stats_tree = None
        self.date_label = None
        self.day_label = None
        self._sort_column = None
        self._sort_reverse = False

        self.selector_frame = ttk.Frame(self.root)
        self.stats_frame = ttk.Frame(self.root)
        self.manage_frame = ttk.Frame(self.root)
        self.schedule_frame = ttk.Frame(self.root)

        self._apply_theme_styles()

        self._create_menu()

        self._setup_selector_tab()
        self._setup_stats_tab()
        self._setup_manage_tab()
        self._setup_schedule_tab()

        # Re-apply theming now that status_text/name_status_tree exist --
        # the first call above ran before those widgets existed, so their
        # tag_configure() calls (status color-coding) were skipped.
        self._apply_theme_styles()

        self._show_frame(self.selector_frame)

        self.last_check = datetime.now()
        self._start_status_checker()

        self._update_datetime()

        self.is_initialized = True
        self._start_data_refresh()

        self.root.after(100, self._update_status_overview)

        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _create_menu(self):
        """Create the top toolbar (File menu + tab switcher).

        Used to be a native OS menu bar (tk.Menu attached via
        root.config(menu=...)). On Windows, a native menu bar -- and any
        tk.Menu popped up from one -- is drawn by Win32, not Tk, so no ttk
        theme (sv_ttk included) can recolor it for dark mode; it always
        looks like a plain light Windows menu. Rebuilt here as an
        in-window ttk toolbar instead, which sv_ttk does theme. The File
        dropdown's *contents* are still a native tk.Menu (unchanged) --
        only the bar itself needed to move, and a fully custom dropdown
        (matching dark mode down to the popup list) is a much bigger,
        separate undertaking not worth it just for that.

        On macOS this trades the traditional top-of-screen application
        menu for the same in-window bar Windows gets -- a deliberate,
        consistent-everywhere choice, not a bug. Tk still supplies its own
        minimal default Application menu (Quit/Hide/etc.) there regardless
        of whether root.config(menu=...) is ever called.
        """
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side="top", fill="x")

        file_menu = tk.Menu(self.root, tearoff=0)

        self.location_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Location", menu=self.location_menu)

        self._update_location_menu()

        file_menu.add_command(label="Change Database Connection", command=self._configure_database_connection)
        file_menu.add_checkbutton(label="Dark Mode", variable=self.dark_mode, command=self._toggle_dark_mode)
        file_menu.add_separator()
        file_menu.add_command(label="About", command=self._show_about)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_closing)

        ttk.Menubutton(toolbar, text="File", menu=file_menu).pack(side="left", padx=(5, 0), pady=4)

        for label, frame_attr in (
            ("Selector", "selector_frame"),
            ("Statistics", "stats_frame"),
            ("Manage Names", "manage_frame"),
            ("Schedule", "schedule_frame"),
        ):
            ttk.Button(
                toolbar, text=label, command=lambda f=frame_attr: self._show_frame(getattr(self, f))
            ).pack(side="left", padx=2, pady=4)

        ttk.Separator(self.root, orient="horizontal").pack(side="top", fill="x")

    def _show_frame(self, frame):
        """Show the selected frame and hide others."""
        frames = [self.selector_frame, self.stats_frame, self.manage_frame, self.schedule_frame]
        for f in frames:
            f.pack_forget()
        frame.pack(expand=True, fill="both", padx=10, pady=10)

    def _load_config(self):
        """Load or create configuration file."""
        config = configparser.ConfigParser()
        config_file = os.path.join(_get_config_dir(), "config.ini")
        _migrate_legacy_config(config_file)
        if os.path.exists(config_file):
            try:
                config.read(config_file)
                if "Settings" in config and "dark_mode" in config["Settings"]:
                    self.dark_mode.set(config["Settings"].getboolean("dark_mode"))
            except Exception as e:
                print(f"Error loading config: {e}")
        if "Settings" not in config:
            config["Settings"] = {"dark_mode": "False", "last_location": "Location 1"}
        if "Database" not in config:
            config["Database"] = {
                "backend": db.default_backend(),
                "driver": "ODBC Driver 17 for SQL Server",
                "server": "",
                "port": str(db.DEFAULT_PORT),
                "database": "",
                # True is the sensible fresh-install default on Windows
                # (Windows Auth just works there, no setup). On macOS it
                # would silently pre-select NTLM for a first-time user
                # just because pyspnego happens to be installed, so SQL
                # login is the right zero-setup default there.
                "trusted_connection": "True" if sys.platform == "win32" else "False",
                "uid": "",
                "pwd": "",
            }
        return config

    def _has_database_config(self):
        """Whether config.ini has a server + database filled in.

        This only checks that the fields are present, not that the server
        is actually reachable -- connectivity is verified when it matters
        (Test Connection / Save in the config dialog, and error handling
        in load_data/save_data for the case where a previously-working
        server later becomes unreachable).
        """
        if "Database" not in self.config:
            return False
        section = self.config["Database"]
        return bool(section.get("server", "").strip()) and bool(section.get("database", "").strip())

    def _save_config(self):
        """Save configuration to file."""
        try:
            config_file = os.path.join(_get_config_dir(), "config.ini")
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            with open(config_file, "w") as f:
                self.config.write(f)
        except Exception as e:
            print(f"Error saving config: {e}")
            messagebox.showerror("Error", "Could not save configuration file. Settings may not persist.")

    def _position_dialog_beside_root(self, dialog):
        """Pin dialog directly beside the main window (ThrottleStop-style
        companion window) instead of leaving it at Tk's default placement,
        which ignores where the app window actually is and can land
        anywhere -- including a completely different monitor in a
        multi-monitor setup.

        Prefers snapping to the right edge of the main window; falls back
        to the left edge if there's no room on the right, and to a small
        overlap of the main window if there's no room on either side.
        """
        self.root.update_idletasks()
        dialog.update_idletasks()

        root_x, root_y = self.root.winfo_x(), self.root.winfo_y()
        root_w = self.root.winfo_width()
        dialog_w = dialog.winfo_reqwidth()
        screen_w = self.root.winfo_screenwidth()

        x = root_x + root_w + 8
        if x + dialog_w > screen_w:
            x = root_x - dialog_w - 8
            if x < 0:
                x = root_x + 40
        dialog.geometry(f"+{x}+{root_y}")

    def _configure_database_connection(self, required=False):
        """Show a dialog to configure the SQL Server connection.

        required=True is used for the mandatory first-run setup, called
        before the rest of the UI exists yet -- so this dialog deliberately
        doesn't use self.style/self.colors, which aren't set up at that
        point. required=False is the "Change Database Connection" menu item
        on an already-running app.
        """
        is_windows = sys.platform == "win32"
        # macOS/Linux offer a SQL Server login always, plus NTLM domain
        # login when pyspnego is importable (it ships in requirements.txt,
        # so normally it is). Windows keeps its own Windows Authentication
        # checkbox instead.
        ntlm_available = _ntlm_available()

        dialog = tk.Toplevel(self.root)
        dialog.title("Configure Database Connection")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        message = (
            "Enter the SQL Server connection details for the shared Ticket Assignment "
            "database.\n\nThis should point at a database everyone on the team can reach."
        )
        ttk.Label(dialog, text=message, wraplength=380, justify="left").grid(
            row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w"
        )

        db_section = self.config["Database"] if "Database" in self.config else {}

        server_var = tk.StringVar(value=db_section.get("server", ""))
        database_var = tk.StringVar(value=db_section.get("database", ""))
        uid_var = tk.StringVar(value=db_section.get("uid", ""))
        pwd_var = tk.StringVar(value=db_section.get("pwd", ""))

        row = 1
        ttk.Label(dialog, text="Server:").grid(row=row, column=0, sticky="w", padx=15, pady=4)
        ttk.Entry(dialog, textvariable=server_var, width=35).grid(row=row, column=1, sticky="ew", padx=15, pady=4)
        row += 1

        instance_hint = "Example: SQLBOX\\SQLEXPRESS or myserver.company.com"
        port_hint = (
            "Hostname or IP only (e.g. sqlbox.company.com) -- use the Port field "
            "below instead of \\instance notation."
        )
        server_hint_var = tk.StringVar(value=instance_hint if is_windows else port_hint)
        ttk.Label(dialog, textvariable=server_hint_var, font=_font(8), wraplength=350, justify="left").grid(
            row=row, column=1, sticky="w", padx=15
        )
        row += 1
        ttk.Label(dialog, text="Database:").grid(row=row, column=0, sticky="w", padx=15, pady=4)
        ttk.Entry(dialog, textvariable=database_var, width=35).grid(row=row, column=1, sticky="ew", padx=15, pady=4)
        row += 1

        # Windows uses an ODBC driver; macOS/Linux use python-tds, which
        # has no driver concept but needs an explicit port (it can't
        # resolve named instances via SQL Browser, which is usually off).
        # Only the one that applies is ever built.
        if is_windows:
            try:
                driver_names = sorted({d for d in pyodbc.drivers() if "SQL Server" in d})
            except Exception:
                driver_names = []
            if not driver_names:
                driver_names = ["ODBC Driver 17 for SQL Server"]
            current_driver = db_section.get("driver", driver_names[-1])
            if current_driver not in driver_names:
                driver_names.append(current_driver)
            driver_var = tk.StringVar(value=current_driver)
            ttk.Label(dialog, text="ODBC Driver:").grid(row=row, column=0, sticky="w", padx=15, pady=4)
            ttk.Combobox(
                dialog, textvariable=driver_var, values=driver_names, state="readonly", width=33
            ).grid(row=row, column=1, sticky="ew", padx=15, pady=4)
            row += 1
            port_var = None
        else:
            driver_var = None
            port_frame = ttk.Frame(dialog)
            port_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
            row += 1
            port_var = tk.StringVar(value=db_section.get("port", "") or str(db.DEFAULT_PORT))
            ttk.Label(port_frame, text="Port:").grid(row=0, column=0, sticky="w", padx=15, pady=4)
            ttk.Entry(port_frame, textvariable=port_var, width=10).grid(row=0, column=1, sticky="w", padx=15, pady=4)
            ttk.Label(
                port_frame,
                text="The server needs TCP/IP enabled on this port. SQL Server Express often "
                "only enables Named Pipes by default -- ask whoever manages the server to "
                "confirm TCP/IP is turned on.",
                font=_font(8), wraplength=380, justify="left",
            ).grid(row=1, column=0, columnspan=2, sticky="w", padx=15)

        # Windows: a single "Windows Authentication" on/off checkbox,
        # always pyodbc either way.
        #
        # macOS/Linux: two modes over python-tds -- a plain SQL Server
        # login, or NTLM domain login (adds pyspnego, which is pip-only,
        # no system driver). The chooser only appears if pyspnego is
        # importable; without it there's nothing to choose between.
        if is_windows:
            trusted_var = tk.BooleanVar(
                value=str(db_section.get("trusted_connection", "True")).strip().lower() in ("1", "true", "yes")
            )
            auth_mode_var = None
        else:
            trusted_var = None
            saved_trusted = str(db_section.get("trusted_connection", "False")).strip().lower() in ("1", "true", "yes")
            auth_mode_var = tk.StringVar(value="ntlm" if (saved_trusted and ntlm_available) else "sql")

        sql_login_label_var = tk.StringVar(value="SQL Login:")
        login_hint_var = tk.StringVar(value="")

        def _current_auth_mode():
            """"windows" (trusted connection), "sql", or "ntlm"."""
            if is_windows:
                return "windows" if trusted_var.get() else "sql"
            return auth_mode_var.get()

        def _toggle_auth_fields():
            mode = _current_auth_mode()
            if is_windows:
                # Windows Authentication uses the signed-in identity, so
                # the login fields don't apply.
                state = "disabled" if mode == "windows" else "normal"
                uid_entry.configure(state=state)
                pwd_entry.configure(state=state)
                return
            # macOS/Linux: both modes take a username and password; only
            # the labelling differs.
            if mode == "ntlm":
                sql_login_label_var.set("Domain Username:")
                login_hint_var.set(
                    "Format: DOMAIN\\username (e.g. CONTOSO\\jsmith) -- the domain prefix is "
                    "required, or SQL Server will reject it as an untrusted domain."
                )
            else:
                sql_login_label_var.set("SQL Login:")
                login_hint_var.set("")

        if is_windows:
            ttk.Checkbutton(
                dialog, text="Use Windows Authentication (Trusted Connection)",
                variable=trusted_var, command=_toggle_auth_fields,
            ).grid(row=row, column=0, columnspan=2, sticky="w", padx=15, pady=(8, 4))
            row += 1
        elif ntlm_available:
            ttk.Label(dialog, text="Authentication:", font=_font(9, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w", padx=15, pady=(8, 0)
            )
            row += 1
            ttk.Radiobutton(
                dialog, text="SQL Server Login", value="sql", variable=auth_mode_var, command=_toggle_auth_fields,
            ).grid(row=row, column=0, columnspan=2, sticky="w", padx=25, pady=2)
            row += 1
            ttk.Radiobutton(
                dialog, text="Domain Login (NTLM)", value="ntlm",
                variable=auth_mode_var, command=_toggle_auth_fields,
            ).grid(row=row, column=0, columnspan=2, sticky="w", padx=25, pady=2)
            row += 1

        ttk.Label(dialog, textvariable=sql_login_label_var).grid(row=row, column=0, sticky="w", padx=15, pady=4)
        uid_entry = ttk.Entry(dialog, textvariable=uid_var, width=35)
        uid_entry.grid(row=row, column=1, sticky="ew", padx=15, pady=4)
        row += 1
        ttk.Label(dialog, textvariable=login_hint_var, font=_font(8), wraplength=350, justify="left").grid(
            row=row, column=1, sticky="w", padx=15
        )
        row += 1
        ttk.Label(dialog, text="Password:").grid(row=row, column=0, sticky="w", padx=15, pady=4)
        pwd_entry = ttk.Entry(dialog, textvariable=pwd_var, width=35, show="*")
        pwd_entry.grid(row=row, column=1, sticky="ew", padx=15, pady=4)
        row += 1

        _toggle_auth_fields()

        status_var = tk.StringVar(value="")
        status_label = ttk.Label(dialog, textvariable=status_var, wraplength=380, justify="left")
        status_label.grid(row=row, column=0, columnspan=2, sticky="w", padx=15, pady=(4, 4))
        row += 1

        def _build_db():
            if is_windows:
                return Database(
                    backend="pyodbc",
                    driver=driver_var.get(),
                    server=server_var.get().strip(),
                    database=database_var.get().strip(),
                    trusted_connection=trusted_var.get(),
                    uid=uid_var.get().strip() or None,
                    pwd=pwd_var.get() or None,
                )
            mode = _current_auth_mode()
            try:
                port = int(port_var.get().strip() or db.DEFAULT_PORT)
            except ValueError:
                port = db.DEFAULT_PORT
            return Database(
                backend="pytds",
                server=server_var.get().strip(),
                port=port,
                database=database_var.get().strip(),
                # "ntlm" mode is what makes db.py authenticate with
                # uid/pwd as domain credentials (via pyspnego) instead of
                # a plain SQL Server login -- see _pytds_ntlm_auth().
                trusted_connection=(mode == "ntlm"),
                uid=uid_var.get().strip() or None,
                pwd=pwd_var.get() or None,
            )

        def _fields_valid():
            if not server_var.get().strip() or not database_var.get().strip():
                status_label.configure(foreground="red")
                status_var.set("Server and Database are required.")
                return False
            mode = _current_auth_mode()
            # Windows Authentication is the only mode that doesn't take a
            # username and password.
            if mode != "windows" and not (uid_var.get().strip() and pwd_var.get()):
                status_label.configure(foreground="red")
                status_var.set(
                    "Domain username and password are required."
                    if mode == "ntlm"
                    else "SQL Login and Password are required."
                )
                return False
            return True

        def _test_connection():
            if not _fields_valid():
                return
            test_db = _build_db()
            try:
                if test_db.database_exists():
                    test_db.test_connection()
                    status_var.set(f"Connected OK. Database '{test_db.database}' exists.")
                else:
                    test_db.with_database("master").test_connection()
                    status_var.set(
                        f"Connected to server OK. Database '{test_db.database}' does not exist yet "
                        "-- it will be created on Save."
                    )
                status_label.configure(foreground="green")
            except DatabaseError as e:
                status_label.configure(foreground="red")
                status_var.set(f"Connection failed: {e}")

        def _on_save():
            if not _fields_valid():
                return
            new_db = _build_db()
            try:
                if not new_db.database_exists():
                    if not messagebox.askyesno(
                        "Create Database?",
                        f"Database '{new_db.database}' does not exist on {new_db.server}.\n\nCreate it now?",
                        parent=dialog,
                    ):
                        return
                    new_db.create_database()
                new_db.ensure_schema()
            except DatabaseError as e:
                status_label.configure(foreground="red")
                status_var.set(f"Could not set up database: {e}")
                return
            except Exception as e:
                status_label.configure(foreground="red")
                status_var.set(f"Unexpected error: {e}")
                return

            self.config["Database"] = {
                "backend": new_db.backend,
                "driver": new_db.driver or "",
                "server": new_db.server,
                "port": str(new_db.port),
                "database": new_db.database,
                "trusted_connection": str(new_db.trusted_connection),
                "uid": new_db.uid or "",
                "pwd": new_db.pwd or "",
            }
            self._save_config()
            self.db = new_db

            self.load_data()

            if hasattr(self, "name_status_tree") and self.name_status_tree:
                self._update_name_status_tree()
            if hasattr(self, "stats_tree") and self.stats_tree:
                self._update_stats_display()
            if hasattr(self, "schedule_tree") and self.schedule_tree:
                self._update_schedule_tree()
            if hasattr(self, "schedule_var") and self.schedule_var:
                self._update_schedule_display()

            dialog.grab_release()
            dialog.destroy()

            messagebox.showinfo(
                "Success",
                "Database connection configured successfully.\n\nThe application will now use this database for all data storage.",
            )

        def _on_cancel():
            dialog.grab_release()
            dialog.destroy()
            if required and self.db is None:
                messagebox.showerror("Error", "A database connection must be configured to use the application.")
                # __init__ can't safely continue past this point (there's no
                # database to load from), so tear the half-built window down
                # rather than let the next line crash on missing state.
                self.root.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=row, column=0, columnspan=2, sticky="e", padx=15, pady=(4, 15))
        ttk.Button(button_frame, text="Test Connection", command=_test_connection).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Save", command=_on_save).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Cancel", command=_on_cancel).pack(side="left", padx=5)

        dialog.protocol("WM_DELETE_WINDOW", _on_cancel)
        dialog.columnconfigure(1, weight=1)

        self._position_dialog_beside_root(dialog)

        dialog.wait_window()

    def _start_data_refresh(self):
        """Start a thread to periodically refresh data from file."""
        if self.refresh_thread and self.refresh_thread.is_alive():
            return

        def refresh_data():
            while self.is_initialized:
                try:
                    if hasattr(self, "root") and self.root.winfo_exists():
                        self._refresh_data()
                    else:
                        return
                except Exception as e:
                    print(f"Background refresh error: {e}")
                time.sleep(0.5)

        self.refresh_thread = threading.Thread(target=refresh_data, daemon=True)
        self.refresh_thread.start()

    def _refresh_data(self):
        """Refresh data from the database without disrupting current operation."""
        try:
            new_locations = self.db.load_all()

            current_snapshot = {
                location: {**data, "names": list(data["names"])}
                for location, data in self.locations.items()
            }
            if new_locations == current_snapshot:
                return

            for location, location_data in new_locations.items():
                if location not in self.locations:
                    # A different client created this location. Add it to our
                    # in-memory cache directly rather than going through
                    # _initialize_location_data(), which would save an EMPTY
                    # version of it and clobber the real data we just read.
                    fresh = empty_location_data()
                    fresh["names"] = deque()
                    self.locations[location] = fresh

                loc_data = self.locations[location]
                names_list = location_data["names"]

                # db.load_all() always returns names in queue-position order
                # with the "current" person already at index 0, so replacing
                # the deque wholesale (only when it actually changed) is
                # enough to keep rotation position correct -- no extra
                # rotate-into-place step needed.
                if (set(names_list) != set(loc_data["names"])
                        or (names_list and (not loc_data["names"] or loc_data["names"][0] != names_list[0]))):
                    loc_data["names"] = deque(names_list)

                loc_data["name_counts"] = location_data["name_counts"]
                loc_data["daily_counts"] = location_data["daily_counts"]
                loc_data["schedules"] = location_data["schedules"]
                for status_type in STATUS_TYPES:
                    key = f"{status_type}_status"
                    loc_data[key] = location_data[key]

            if hasattr(self, "root") and self.root.winfo_exists():
                self.root.after_idle(self._update_location_menu)
                self.root.after_idle(self._update_all_displays)
                self.root.after_idle(self._update_status_overview)
                self.root.after_idle(self._update_schedule_display)

            if hasattr(self, "schedule_name_combo"):
                current_location = self.current_location.get()
                if current_location in self.locations:
                    values = sorted(list(self.locations[current_location]["names"]))
                    self.root.after_idle(lambda: self.schedule_name_combo.configure(values=values))
        except Exception as e:
            print(f"Data refresh error: {e}")

    def _update_all_displays(self):
        """Update all GUI displays if they exist."""
        try:
            if not (hasattr(self, "root") and self.root.winfo_exists()):
                return
            if hasattr(self, "current_name_var") and self.current_name_var:
                current_name = self.get_current_name()
                if current_name:
                    self.current_name_var.set(current_name)
            if hasattr(self, "name_status_tree") and self.name_status_tree:
                self._update_name_status_tree()
            if hasattr(self, "stats_tree") and self.stats_tree:
                self._update_stats_display()
            if hasattr(self, "schedule_tree") and self.schedule_tree:
                self._update_schedule_tree()
            if hasattr(self, "schedule_var") and self.schedule_var:
                self._update_schedule_display()
            if hasattr(self, "status_text") and self.status_text:
                # Required for the Selector tab's "Current Status Overview"
                # box to follow a location switch -- without it, that box
                # keeps showing the previous location's statuses until some
                # other action happens to refresh it.
                self._update_status_overview()
        except Exception as e:
            print(f"Error updating displays: {e}")

    def save_data(self, location_name=None):
        """Save in-memory location data to the database.

        Pass location_name to save just that one location -- almost every
        action only ever changes the current location, so most call sites
        do this. Omit it to save every location currently in memory (used
        by the few actions, like clearing expired statuses across all
        locations, that can genuinely touch more than one).
        """
        try:
            if location_name is not None:
                if location_name in self.locations:
                    self.db.save_location(location_name, self.locations[location_name])
                return
            for location, location_data in self.locations.items():
                self.db.save_location(location, location_data)
        except DatabaseError as e:
            print(f"Error saving data: {e}")
            messagebox.showwarning("Database Error", f"Could not save data. Please try again later.\n\n{e}")
        except Exception as e:
            print(f"Error saving data: {e}")
            messagebox.showwarning("Database Error", "Could not save data. Please try again later.")

    def _on_closing(self):
        """Handle window closing event."""
        # Stop the background poller *before* saving, not after. It hits
        # the database every 0.5s, so leaving it running during the
        # closing save means the two compete for the connection while the
        # window sits there unresponsive.
        self.is_initialized = False
        try:
            # Every action already saves as it happens, so this is only a
            # safety net -- scoped to the current location, since that's
            # the only one this client could have pending changes for.
            self.save_data(self.current_location.get())
        finally:
            self.root.destroy()

    def _start_status_checker(self):
        """Start the background thread for checking status expiration and
        refreshing half-day morning/afternoon boundaries.
        """
        def check_statuses():
            while True:
                current_time = datetime.now()
                if current_time.date() > self.last_check.date():
                    self._clear_expired_statuses()
                    self.last_check = current_time
                if self._has_active_half_day_periods() and hasattr(self, "root") and self.root.winfo_exists():
                    # A half-day boundary may have just passed -- refresh so
                    # the UI reflects it without the user having to click
                    # anything. Runs on the same 60-second cadence as the
                    # expiry check above rather than its own timer.
                    self.root.after_idle(self._update_all_displays)
                    self.root.after_idle(self._update_status_overview)
                time.sleep(60)

        thread = threading.Thread(target=check_statuses, daemon=True)
        thread.start()

    def _clear_expired_statuses(self):
        """Clear any expired statuses, across every location.

        Runs once a day per open client; iterates every location (not just
        the current one) since self.locations is kept in sync for all
        locations by the background refresh, and whichever client notices
        first should clear it for everyone.
        """
        today = date.today()
        changed_locations = []
        for location_name, loc_data in self.locations.items():
            expired_here = False
            for status_type in STATUS_KEYS:
                status_dict = loc_data[status_type]
                for name, duration in list(status_dict.items()):
                    if duration.end_date and duration.end_date < today:
                        status_dict.pop(name)
                        expired_here = True
            if expired_here:
                changed_locations.append(location_name)

        if changed_locations:
            # Was self._update_name_listbox() — that method doesn't exist anywhere in the
            # class, so this raised AttributeError the first time a status actually expired.
            # _update_name_status_tree() is the method that keeps the "Manage Names" tree
            # in sync elsewhere in the file, so that's what belongs here.
            self._update_name_status_tree()
            self._update_status_overview()
            for location_name in changed_locations:
                self.save_data(location_name)
            self._update_status_overview()

    def _update_datetime(self):
        """Update the date and time display."""
        current = datetime.now()
        if hasattr(self, "date_label") and self.date_label:
            self.date_label.config(text=f"Date: {current.strftime('%Y-%m-%d')}")
        if hasattr(self, "day_label") and self.day_label:
            self.day_label.config(text=f"Day: {current.strftime('%A')}")
        self.root.after(1000, self._update_datetime)

    def _select_next_name(self):
        """Select the next available name in the rotation."""
        if not self.refresh_thread:
            self._start_data_refresh()

        loc_data = self.locations[self.current_location.get()]

        if len(loc_data["names"]) == 0:
            messagebox.showwarning("No Names", "Please add names before selecting.")
            return

        today = date.today()
        available_names = []
        name_list = list(loc_data["names"])

        for name in name_list:
            is_available = True
            for status_type in STATUS_KEYS:
                status_dict = loc_data[status_type]
                if name in status_dict:
                    duration = status_dict[name]
                    if self._status_currently_blocks(name, duration, today=today):
                        is_available = False
                        break
            if is_available:
                available_names.append(name)

        if not available_names:
            messagebox.showwarning("No Available Names", "All names are currently unavailable.")
            return

        current_name = self.get_current_name()
        if current_name and current_name in available_names:
            loc_data["name_counts"][current_name] = loc_data["name_counts"].get(current_name, 0) + 1
            current_day = datetime.now().strftime("%A")
            if current_name not in loc_data["daily_counts"]:
                loc_data["daily_counts"][current_name] = {day: 0 for day in day_name}
            loc_data["daily_counts"][current_name][current_day] += 1

        next_name = None
        names_list = list(loc_data["names"])
        current_index = names_list.index(current_name) if current_name in names_list else -1

        for i in range(len(names_list)):
            index = (current_index + i + 1) % len(names_list)
            if names_list[index] in available_names:
                next_name = names_list[index]
                break

        if next_name:
            loc_data["names"] = deque(names_list)
            if loc_data["names"]:
                while loc_data["names"] and loc_data["names"][0] != next_name:
                    loc_data["names"].rotate(-1)

            if hasattr(self, "current_name_var"):
                self.current_name_var.set(next_name)

            self._update_stats_display()
            self._update_schedule_display()
            self._update_name_status_tree()
            self._update_status_overview()

            self.save_data(self.current_location.get())

    def _update_schedule_display(self):
        """Update the schedule display for the current name."""
        if not hasattr(self, "schedule_var"):
            return
        current_name = self.get_current_name()
        if not current_name:
            self.schedule_var.set("No schedule available")
            return
        current_day = datetime.now().strftime("%A")
        loc_data = self.locations[self.current_location.get()]
        if (
            current_name in loc_data["schedules"]
            and current_day in loc_data["schedules"][current_name]
            and loc_data["schedules"][current_name][current_day]
        ):
            times = ", ".join(loc_data["schedules"][current_name][current_day])
            self.schedule_var.set(f"Today's Schedule: {times}")
            return
        self.schedule_var.set("No schedule for today")

    def _rename_location(self):
        """Rename the current location."""
        current_name = self.current_location.get()
        new_name = simpledialog.askstring(
            "Rename Location", f"Enter new name for '{current_name}':", parent=self.root
        )

        if new_name and new_name.strip():
            new_name = new_name.strip()
            if new_name != current_name:
                if new_name in self.locations:
                    messagebox.showerror("Error", "This location name already exists.")
                    return
                try:
                    self.locations[new_name] = self.locations.pop(current_name)
                    # Rename the row in the database too -- save_data() alone
                    # would only write the new name (self.locations no longer
                    # has the old key), leaving an orphaned row with no names
                    # under the old location name.
                    self.db.rename_location(current_name, new_name)
                    self.current_location.set(new_name)
                    self.config["Settings"]["last_location"] = new_name
                    self._save_config()
                    self.save_data(new_name)
                    self._update_location_menu()
                    messagebox.showinfo("Success", f"Location renamed to '{new_name}'")
                except Exception as e:
                    if new_name in self.locations:
                        self.locations[current_name] = self.locations.pop(new_name)
                    self.current_location.set(current_name)
                    print(f"Error renaming location: {e}")
                    messagebox.showerror("Error", "Failed to rename location. Please try again.")

    def _setup_selector_tab(self):
        # place() with a relative anchor keeps this reliably centered no
        # matter how wide the window gets (e.g. maximized on a wide
        # monitor) -- the previous pack(expand=True) + fill="x" nesting
        # left content lopsided and partially off-screen once the window
        # was much wider than its 900px default.
        content_frame = ttk.Frame(self.selector_frame, padding=40)
        content_frame.place(relx=0.5, rely=0.5, anchor="center")

        date_frame = ttk.Frame(content_frame)
        date_frame.pack(fill="x", pady=(0, 20))

        self.date_label = ttk.Label(date_frame, font=_font(14))
        self.date_label.pack(side="left", padx=10)

        self.day_label = ttk.Label(date_frame, font=_font(14))
        self.day_label.pack(side="right", padx=10)

        name_frame = ttk.Frame(content_frame)
        name_frame.pack(fill="x", pady=20)

        name_label = ttk.Label(name_frame, text="Current Assignment:", font=_font(12))
        name_label.pack()

        self.current_name_var = tk.StringVar(value="Add names to begin")
        current_name = self.get_current_name()
        if current_name:
            self.current_name_var.set(current_name)

        current_name_label = ttk.Label(
            name_frame, textvariable=self.current_name_var, font=_font(24, "bold")
        )
        current_name_label.pack(pady=10)

        schedule_frame = ttk.Frame(content_frame)
        schedule_frame.pack(fill="x", pady=20)

        schedule_header = ttk.Label(schedule_frame, text="Today's Schedule", font=_font(12))
        schedule_header.pack()

        self.schedule_var = tk.StringVar(value="No schedule available")
        schedule_label = ttk.Label(schedule_frame, textvariable=self.schedule_var, font=_font(14))
        schedule_label.pack(pady=10)

        self._update_schedule_display()

        button_frame = ttk.Frame(content_frame)
        button_frame.pack(pady=30)

        nav_button_style = {"width": 12, "padding": 10}

        prev_button = ttk.Button(
            button_frame, text="⟵ Previous", command=lambda: self._rotate(1), **nav_button_style
        )
        prev_button.pack(side="left", padx=10)

        assign_button = ttk.Button(
            button_frame,
            text="Assign Ticket",
            style="Accent.TButton",
            width=15,
            command=self._select_next_name,
        )
        assign_button.pack(side="left", padx=10)

        next_button = ttk.Button(button_frame, text="Next ⟶", command=lambda: self._rotate(-1), **nav_button_style)
        next_button.pack(side="left", padx=10)

        ttk.Separator(content_frame, orient="horizontal").pack(fill="x", pady=20)

        status_frame = ttk.LabelFrame(content_frame, text="Current Status Overview")
        status_frame.pack(fill="x", pady=10)

        text_frame = ttk.Frame(status_frame)
        text_frame.pack(fill="x", padx=10, pady=5)
        text_frame.grid_columnconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(text_frame, orient="vertical")

        self.status_text = tk.Text(
            text_frame, wrap=tk.WORD, font=_font(11), height=10, yscrollcommand=scrollbar.set
        )

        scrollbar.config(command=self.status_text.yview)

        self.status_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="nsew")

        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)

        self.status_text.bind("<<Modified>>", self._adjust_text_height)

        # Colors get set for real below, once _apply_theme_styles() runs
        # again after every tab exists (see __init__) -- this widget just
        # needs to exist first.
        self.status_text.config(state="disabled")

        self._update_status_overview()

    def _adjust_text_height(self, event):
        """Adjust the height of the status text widget based on content."""
        if not (hasattr(self, "status_text") and self.status_text):
            return
        num_lines = int(self.status_text.index("end-1c").split(".")[0])
        min_height = 6
        max_height = 15
        new_height = min(max(min_height, num_lines), max_height)
        if int(self.status_text["height"]) != new_height:
            self.status_text.configure(height=new_height)
        if event:
            self.status_text.edit_modified(False)
            return

    def _rotate(self, direction):
        """Rotate to the next (direction=-1) or previous (direction=1)
        available name without tallying a ticket.
        """
        loc_data = self.locations[self.current_location.get()]
        if len(loc_data["names"]) == 0:
            return
        today = date.today()
        original_position = list(loc_data["names"])
        rotations = 0
        while rotations < len(loc_data["names"]):
            loc_data["names"].rotate(direction)
            current_name = loc_data["names"][0]
            is_available = True
            for status_type in STATUS_KEYS:
                if current_name in loc_data[status_type]:
                    duration = loc_data[status_type][current_name]
                    if self._status_currently_blocks(current_name, duration, today=today):
                        is_available = False
                        break
            if is_available:
                self.current_name_var.set(current_name)
                self._update_schedule_display()
                self.save_data(self.current_location.get())
                return
            rotations += 1
        loc_data["names"] = deque(original_position)
        self._update_schedule_display()

    def _setup_stats_tab(self):
        stats_notebook = ttk.Notebook(self.stats_frame)
        stats_notebook.pack(expand=True, fill="both", padx=5, pady=5)

        total_frame = ttk.Frame(stats_notebook)
        stats_notebook.add(total_frame, text="Total Stats")

        daily_frame = ttk.Frame(stats_notebook)
        stats_notebook.add(daily_frame, text="Daily Stats")

        self.stats_tree = ttk.Treeview(total_frame, columns=("Name", "Count"), show="headings")
        self.stats_tree.heading("Name", text="Name")
        self.stats_tree.heading("Count", text="Total Times Selected")
        self.stats_tree.column("Name", width=200)
        self.stats_tree.column("Count", width=150)
        self.stats_tree.pack(expand=True, fill="both", padx=5, pady=5)

        self.daily_stats_tree = ttk.Treeview(daily_frame, columns=("Name",) + tuple(day_name), show="headings")
        self.daily_stats_tree.heading("Name", text="Name")
        for day in day_name:
            self.daily_stats_tree.heading(day, text=day[:3])
            self.daily_stats_tree.column(day, width=70)
        self.daily_stats_tree.column("Name", width=100)
        self.daily_stats_tree.pack(expand=True, fill="both", padx=5, pady=5)

        reset_frame = ttk.Frame(self.stats_frame)
        reset_frame.pack(fill="x", padx=5, pady=5)

        reset_button = ttk.Button(reset_frame, text="Reset All Statistics", command=self._reset_statistics)
        reset_button.pack(side="right", padx=5)

        self._update_stats_display()

    def _setup_manage_tab(self):
        main_frame = ttk.Frame(self.manage_frame)
        main_frame.pack(fill="both", expand=True, padx=10, pady=5)

        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(0, weight=1)

        list_frame = ttk.LabelFrame(main_frame, text="Manage Names and Status")
        list_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(1, weight=1)

        search_frame = ttk.Frame(list_frame)
        search_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 0))
        ttk.Label(search_frame, text="Search:").pack(side="left", padx=(0, 5))
        self.name_search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.name_search_var)
        search_entry.pack(side="left", fill="x", expand=True)
        # Re-filter the tree as the team roster grows -- trace fires on
        # every keystroke, cheap enough for the small rosters this app manages.
        self.name_search_var.trace_add("write", lambda *args: self._update_name_status_tree())
        ttk.Button(search_frame, text="Clear", command=lambda: self.name_search_var.set("")).pack(
            side="left", padx=(5, 0)
        )

        # Set Status lives in this box's own toolbar, next to the list it
        # acts on: one click sets the selected row's status, with no dialog
        # needed for the statuses that don't take dates (Available/Out of
        # Queue/Out of Office).
        self.status_menu_button = ttk.Button(search_frame, text="Set Status ▾", style="Accent.TButton")
        self.status_menu_button.configure(command=lambda: self._show_status_menu(self.status_menu_button))
        self.status_menu_button.pack(side="left", padx=(10, 0))

        tree_frame = ttk.Frame(list_frame)
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        self.name_status_tree = ttk.Treeview(
            tree_frame,
            columns=("Name", "Current Status", "Current Duration", "Upcoming Status", "Upcoming Duration"),
            show="headings",
            selectmode="browse",
        )

        self.name_status_tree.heading("Name", text="Name", command=lambda: self._sort_treeview("Name"))
        self.name_status_tree.heading(
            "Current Status", text="Current Status", command=lambda: self._sort_treeview("Current Status")
        )
        self.name_status_tree.heading(
            "Current Duration", text="Current Duration", command=lambda: self._sort_treeview("Current Duration")
        )
        self.name_status_tree.heading(
            "Upcoming Status", text="Upcoming Status", command=lambda: self._sort_treeview("Upcoming Status")
        )
        self.name_status_tree.heading(
            "Upcoming Duration", text="Upcoming Duration", command=lambda: self._sort_treeview("Upcoming Duration")
        )

        self.name_status_tree.column("Name", width=150, minwidth=100)
        self.name_status_tree.column("Current Status", width=150, minwidth=100)
        self.name_status_tree.column("Current Duration", width=200, minwidth=150)
        self.name_status_tree.column("Upcoming Status", width=150, minwidth=100)
        self.name_status_tree.column("Upcoming Duration", width=200, minwidth=150)

        y_scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.name_status_tree.yview)
        x_scrollbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.name_status_tree.xview)
        self.name_status_tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)

        self.name_status_tree.grid(row=0, column=0, sticky="nsew")
        y_scrollbar.grid(row=0, column=1, sticky="ns")
        x_scrollbar.grid(row=1, column=0, sticky="ew")

        # Double-click a row as a shortcut to the same status dialog the
        # "Set Status ▾" dropdown opens.
        self.name_status_tree.bind("<Double-1>", lambda event: self._open_status_dialog_for_selection())

        # Round-robin order is always alphabetical (see
        # _normalize_rotation_order), so there's no manual reordering UI.
        action_frame = ttk.LabelFrame(main_frame, text="Name Actions")
        action_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        action_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(action_frame, text="New Name:").grid(row=0, column=0, padx=5, pady=2)
        self.new_name_var = tk.StringVar()
        name_entry = ttk.Entry(action_frame, textvariable=self.new_name_var)
        name_entry.grid(row=0, column=1, padx=5, pady=2, sticky="ew")

        button_frame = ttk.Frame(action_frame)
        button_frame.grid(row=0, column=2, padx=5, pady=2)

        ttk.Button(button_frame, text="Add Name", command=self._add_name).pack(side="left", padx=2)
        ttk.Button(button_frame, text="Edit Selected", command=self._edit_name).pack(side="left", padx=2)
        ttk.Button(button_frame, text="Remove Selected", command=self._remove_name).pack(side="left", padx=2)

        # Unlike _setup_stats_tab/_setup_schedule_tab, this tab's data control
        # was never populated on initial creation -- it silently sat empty
        # until some other tab's action (e.g. Assign Ticket) incidentally
        # called _update_name_status_tree(). Populate it immediately instead.
        self._update_name_status_tree()

    def _get_status_enum(self, status_type):
        """Convert status type to Status enum value."""
        status_map = {
            "out_of_office_status": Status.OOO,
            "vacation_status": Status.VACATION,
            "training_status": Status.TRAINING,
            "sick_leave_status": Status.SICK,
            "half_day_status": Status.HALF_DAY,
        }
        return status_map.get(status_type)

    def _status_display_name(self, status_type, duration):
        """Human-readable label for a status entry, e.g. "Vacation" or
        "Vacation (Half Day - Morning)". status_type is the "..._status"
        dict key; duration is the StatusDuration for that entry.

        Half Day is a flag on vacation/sick_leave entries (see
        StatusDuration.half_day), not its own status type, so it needs
        folding into the label wherever a status gets displayed.
        """
        label = self._get_status_enum(status_type)
        if status_type in ("vacation_status", "sick_leave_status") and getattr(duration, "half_day", False):
            period = getattr(duration, "half_day_period", None)
            suffix = f" - {period.capitalize()}" if period else ""
            label = f"{label} (Half Day{suffix})"
        return label

    def _half_day_boundary(self, name, weekday_name):
        """The clock time that splits `name`'s half day on `weekday_name`
        into a blocked half and an available half.

        Derived from the midpoint of their scheduled hours that day (the
        earliest start to the latest end, across however many time-range
        entries they have) -- e.g. a 9:00-17:00 schedule splits at 13:00.
        Falls back to 1:00 PM if they have no (parseable) schedule entries
        for that day, since there's nothing else to split on.
        """
        loc_data = self.locations[self.current_location.get()]
        fallback = dt_time(13, 0)
        ranges = loc_data.get("schedules", {}).get(name, {}).get(weekday_name, [])
        starts, ends = [], []
        for time_range in ranges:
            try:
                start_str, end_str = time_range.split("-", 1)
                starts.append(datetime.strptime(start_str.strip(), "%H:%M").time())
                ends.append(datetime.strptime(end_str.strip(), "%H:%M").time())
            except ValueError:
                continue
        if not starts or not ends:
            return fallback
        day_start = datetime.combine(date.today(), min(starts))
        day_end = datetime.combine(date.today(), max(ends))
        if day_end <= day_start:
            return fallback
        return (day_start + (day_end - day_start) / 2).time()

    def _is_half_day_currently_blocking(self, name, duration, now=None):
        """Whether a half-day status entry is still blocking `name` right
        now, as opposed to having already flipped over to their available
        half of the day.

        Only called for entries where duration.start_date is today (a half
        day is always exactly one day) -- any other date is handled by the
        plain date-range check in _status_currently_blocks.
        """
        now = now or datetime.now()
        period = getattr(duration, "half_day_period", None)
        if not period:
            # Half day with no recorded period (e.g. older data) -- nothing
            # to split on, so treat it as blocking the whole day.
            return True
        boundary = self._half_day_boundary(name, now.strftime("%A"))
        if period == "afternoon":
            # Available in the morning, blocked from the boundary onward.
            return now.time() >= boundary
        # "morning": blocked until the boundary, available after.
        return now.time() < boundary

    def _status_currently_blocks(self, name, duration, today=None, now=None):
        """Whether this status entry makes `name` unavailable right now.

        Normally a pure date-range check (today within [start_date,
        end_date]). Half-day entries with a recorded morning/afternoon
        period narrow that further to whichever half of today it actually
        is -- see _is_half_day_currently_blocking.
        """
        now = now or datetime.now()
        today = today or now.date()
        in_range = (not duration.end_date or duration.end_date >= today) and duration.start_date <= today
        if not in_range:
            return False
        if getattr(duration, "half_day", False) and duration.start_date == now.date():
            return self._is_half_day_currently_blocking(name, duration, now=now)
        return True

    def _has_active_half_day_periods(self):
        """Whether any location has a half-day-with-period status dated
        today -- used to decide whether the 60-second background tick
        needs to refresh displays for a morning/afternoon boundary that
        may have just passed.
        """
        today = date.today()
        for loc_data in self.locations.values():
            for status_key in ("vacation_status", "sick_leave_status"):
                for duration in loc_data[status_key].values():
                    if (
                        getattr(duration, "half_day", False)
                        and getattr(duration, "half_day_period", None)
                        and duration.start_date == today
                    ):
                        return True
        return False

    def _update_status_overview(self):
        """Update the status overview text with current and future non-available statuses."""
        if not (hasattr(self, "status_text") and self.status_text):
            return
        try:
            self.status_text.config(state="normal")
            self.status_text.delete("1.0", tk.END)

            today = date.today()
            current_status_info = []
            future_status_info = []
            loc_data = self.locations[self.current_location.get()]

            for name in sorted(loc_data["names"]):
                current_statuses = []
                future_statuses = []

                for status_type in STATUS_KEYS:
                    status_dict = loc_data[status_type]

                    if name in status_dict:
                        duration = status_dict[name]
                        status_text = self._status_display_name(status_type, duration)
                        if self._status_currently_blocks(name, duration, today=today):
                            if duration.end_date:
                                current_statuses.append(f"{status_text} (until {duration.end_date})")
                            else:
                                current_statuses.append(f"{status_text}")
                        elif duration.start_date > today:
                            future_statuses.append(
                                f"{status_text} ({duration.start_date} to "
                                f"{duration.end_date if duration.end_date else 'ongoing'})"
                            )

                if current_statuses:
                    current_status_info.append(f"{name}: {', '.join(current_statuses)}")
                if future_statuses:
                    future_status_info.append(f"{name}: {', '.join(future_statuses)}")

            self.status_text.insert("end", "Current Statuses:\n")
            if current_status_info:
                for line in current_status_info:
                    self.status_text.insert("end", line + "\n", ("status-current",))
            else:
                self.status_text.insert("end", "All team members are available\n")

            if future_status_info:
                self.status_text.insert("end", "\nUpcoming Statuses:\n")
                for line in future_status_info:
                    self.status_text.insert("end", line + "\n", ("status-upcoming",))

            self.status_text.config(state="disabled")
        except Exception as e:
            print(f"Error updating status overview: {e}")
            messagebox.showerror("Error", "Failed to update status overview. Please try again.")

    def _reset_statistics(self):
        if messagebox.askyesno("Confirm Reset", "Are you sure you want to reset all statistics?"):
            loc_data = self.locations[self.current_location.get()]
            loc_data["name_counts"] = {name: 0 for name in loc_data["names"]}
            loc_data["daily_counts"] = {}
            loc_data["schedules"] = {}
            for status_key in STATUS_KEYS:
                loc_data[status_key] = {}

            self._initialize_daily_counts_for_location(self.current_location.get())
            self._initialize_schedules_for_location(self.current_location.get())

            self._update_stats_display()
            self._update_name_status_tree()
            self._update_status_overview()
            self._update_schedule_display()
            self.save_data(self.current_location.get())

    def _update_stats_display(self):
        """Update both total and daily statistics displays."""
        if not (hasattr(self, "stats_tree") and hasattr(self, "daily_stats_tree")):
            return

        loc_data = self.locations[self.current_location.get()]

        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)

        sorted_names = sorted(loc_data["name_counts"].items(), key=lambda x: x[0])

        for name, count in sorted_names:
            self.stats_tree.insert("", "end", values=(name, count))

        for item in self.daily_stats_tree.get_children():
            self.daily_stats_tree.delete(item)

        for name in sorted(loc_data["names"]):
            if name in loc_data["daily_counts"]:
                values = [name] + [loc_data["daily_counts"][name].get(day, 0) for day in day_name]
                self.daily_stats_tree.insert("", "end", values=values)

    def _setup_schedule_tab(self):
        select_frame = ttk.Frame(self.schedule_frame)
        select_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(select_frame, text="Select Name:").pack(side="left", padx=5)

        self.schedule_name_var = tk.StringVar()
        self.schedule_name_combo = ttk.Combobox(
            select_frame, textvariable=self.schedule_name_var, state="readonly"
        )
        self.schedule_name_combo.pack(side="left", padx=5)

        self._update_schedule_name_combo()

        bulk_frame = ttk.LabelFrame(self.schedule_frame, text="Weekday Schedule (Mon-Fri)")
        bulk_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(bulk_frame, text="Time (HH:MM-HH:MM):").pack(side="left", padx=5)
        self.bulk_time_var = tk.StringVar()
        time_entry = ttk.Entry(bulk_frame, textvariable=self.bulk_time_var)
        time_entry.pack(side="left", padx=5)

        ttk.Button(bulk_frame, text="Set Weekday Schedule", command=self._set_bulk_schedule).pack(
            side="left", padx=5
        )

        individual_frame = ttk.LabelFrame(self.schedule_frame, text="Individual Day Schedule")
        individual_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(individual_frame, text="Select Day:").pack(side="left", padx=5)
        self.schedule_day_var = tk.StringVar()
        day_combo = ttk.Combobox(individual_frame, textvariable=self.schedule_day_var, values=list(day_name))
        day_combo.pack(side="left", padx=5)

        ttk.Label(individual_frame, text="Time (HH:MM-HH:MM):").pack(side="left", padx=5)
        self.time_var = tk.StringVar()
        time_entry = ttk.Entry(individual_frame, textvariable=self.time_var)
        time_entry.pack(side="left", padx=5)

        ttk.Button(individual_frame, text="Add Schedule", command=self._add_schedule).pack(side="left", padx=5)

        self.schedule_tree = ttk.Treeview(self.schedule_frame, columns=("Name", "Day", "Time"), show="headings")
        self.schedule_tree.heading("Name", text="Name")
        self.schedule_tree.heading("Day", text="Day")
        self.schedule_tree.heading("Time", text="Time")

        self.schedule_tree.column("Name", width=150)
        self.schedule_tree.column("Day", width=100)
        self.schedule_tree.column("Time", width=150)

        self.schedule_tree.pack(fill="both", expand=True, padx=10, pady=5)

        remove_button = ttk.Button(
            self.schedule_frame, text="Remove Selected Schedule", command=self._remove_schedule
        )
        remove_button.pack(pady=5)

        self._update_schedule_tree()

    def _add_schedule(self):
        name = self.schedule_name_var.get()
        day = self.schedule_day_var.get()
        time = self.time_var.get()

        if not all([name, day, time]):
            messagebox.showwarning("Invalid Input", "Please fill in all fields.")
            return

        if not self._validate_time_format(time):
            messagebox.showwarning("Invalid Time Format", "Please use format HH:MM-HH:MM (24-hour)")
            return

        loc_data = self.locations[self.current_location.get()]

        if name not in loc_data["schedules"]:
            loc_data["schedules"][name] = {d: [] for d in day_name}
        elif day not in loc_data["schedules"][name]:
            loc_data["schedules"][name][day] = []

        loc_data["schedules"][name][day].append(time)
        loc_data["schedules"][name][day].sort()

        self._update_schedule_tree()
        self._update_schedule_display()
        self.save_data(self.current_location.get())

        self.time_var.set("")

    def _validate_time_format(self, time_str):
        try:
            start, end = time_str.split("-")
            datetime.strptime(start.strip(), "%H:%M")
            datetime.strptime(end.strip(), "%H:%M")
            return True
        except Exception:
            return False

    def _remove_schedule(self):
        selection = self.schedule_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a schedule to remove.")
            return

        item = self.schedule_tree.item(selection[0])
        name, day, time = item["values"]

        loc_data = self.locations[self.current_location.get()]

        if name in loc_data["schedules"] and day in loc_data["schedules"][name]:
            if time in loc_data["schedules"][name][day]:
                loc_data["schedules"][name][day].remove(time)
                self._update_schedule_tree()
                self._update_schedule_display()
                self.save_data(self.current_location.get())

    def _update_schedule_tree(self):
        """Update the schedule treeview to show all schedules."""
        for item in self.schedule_tree.get_children():
            self.schedule_tree.delete(item)

        loc_data = self.locations[self.current_location.get()]

        for name in sorted(loc_data["names"]):
            schedule_groups = defaultdict(list)
            for day in day_name:
                if name in loc_data["schedules"] and day in loc_data["schedules"][name]:
                    times = tuple(sorted(loc_data["schedules"][name][day]))
                    if times:
                        schedule_groups[times].append(day)

            for times, days in schedule_groups.items():
                day_ranges = self._get_day_ranges(days)
                for day_range in day_ranges:
                    self.schedule_tree.insert("", "end", values=(name, day_range, ", ".join(times)))

    def _get_day_ranges(self, days):
        """Convert list of days into condensed ranges (e.g., 'Monday-Friday')"""
        if not days:
            return []

        day_indices = {day: idx for idx, day in enumerate(day_name)}
        indexed_days = sorted([day_indices[day] for day in days])

        ranges = []
        range_start = indexed_days[0]
        prev_idx = indexed_days[0]

        for idx in indexed_days[1:] + [None]:
            if idx != prev_idx + 1:
                range_end = prev_idx
                if range_start == range_end:
                    ranges.append(day_name[range_start])
                else:
                    ranges.append(f"{day_name[range_start]}-{day_name[range_end]}")
                if idx is not None:
                    range_start = idx
            prev_idx = idx if idx is not None else prev_idx

        return ranges

    def _set_bulk_schedule(self):
        name = self.schedule_name_var.get()
        time = self.bulk_time_var.get()

        if not all([name, time]):
            messagebox.showwarning("Invalid Input", "Please select a name and enter time.")
            return

        if not self._validate_time_format(time):
            messagebox.showwarning("Invalid Time Format", "Please use format HH:MM-HH:MM (24-hour)")
            return

        loc_data = self.locations[self.current_location.get()]

        if name not in loc_data["schedules"]:
            loc_data["schedules"][name] = {d: [] for d in day_name}

        for day in WEEKDAYS:
            loc_data["schedules"][name][day] = [time]

        self._update_schedule_tree()
        self._update_schedule_display()
        self.save_data(self.current_location.get())

        self.bulk_time_var.set("")

    def _toggle_dark_mode(self):
        """Toggle between light and dark mode."""
        self._apply_theme_styles()
        self.config["Settings"]["dark_mode"] = str(self.dark_mode.get())
        self._save_config()

    def _apply_theme_styles(self):
        """Apply the current theme.

        sv_ttk handles every ttk widget on its own (see the set_theme()
        call in __init__) -- all that's left here is the handful of things
        it doesn't reach: raw tk widgets, the date picker's day cells, and
        our own semantic status colors.
        """
        theme = "dark" if self.dark_mode.get() else "light"
        colors = self.colors[theme]

        if getattr(self, "_applied_theme", None) != theme:
            sv_ttk.set_theme(theme, root=self.root)
            self._applied_theme = theme

        self.root.configure(bg=colors["bg"])

        if hasattr(self, "status_text") and self.status_text:
            self.status_text.configure(
                background=colors["bg"],
                foreground=colors["fg"],
                selectbackground=colors["accent_bg"],
                selectforeground=colors["accent_fg"],
                borderwidth=1,
                relief="solid",
            )
            self.status_text.tag_configure("status-current", foreground=colors["status_unavailable_fg"])
            self.status_text.tag_configure("status-upcoming", foreground=colors["fg"])

        if hasattr(self, "name_status_tree") and self.name_status_tree:
            self.name_status_tree.tag_configure("status-available", foreground=colors["status_available_fg"])
            self.name_status_tree.tag_configure("status-unavailable", foreground=colors["status_unavailable_fg"])

        # The Combobox dropdown list is a plain tk.Listbox under the hood,
        # not ttk -- sv_ttk doesn't theme it, so it still needs this.
        self.root.option_add("*TCombobox*Listbox.background", colors["bg"])
        self.root.option_add("*TCombobox*Listbox.foreground", colors["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", colors["accent_bg"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", colors["accent_fg"])

    def _show_about(self):
        """Display the About dialog with version information."""
        about_text = (
            f"\n{APP_NAME}\n\nVersion: {APP_VERSION}\nRelease Date: {APP_DATE}\n\n"
            "A modern ticket assignment application designed \n"
            "for efficient team task management.\n\n"
            "Features:\n"
            "• Multi-user support with network drive compatibility\n"
            "• Real-time status tracking and updates\n"
            "• Customizable schedules and availability management\n"
            "• Dark mode support\n"
            "• Windows 11 modern UI design\n\n"
            "© 2024 All rights reserved.\n"
        )
        messagebox.showinfo("About", about_text.strip())

    def _apply_status(self, name, status_type, start_date=None, end_date=None, half_day=False, half_day_period=None):
        """Set name's status from the status dialog.

        status_type is "available" (clears any current status) or one of
        "out_of_office"/"vacation"/"training"/"sick_leave". start_date/
        end_date are ignored for "available" and "training". half_day and
        half_day_period ("morning"/"afternoon"/None) are only meaningful
        for "vacation"/"sick_leave" -- half_day marks the entry as a single
        half day rather than a full day off, and half_day_period says
        which half is blocked (see _half_day_boundary).

        Dates come in already parsed, from _open_status_dialog's calendar
        pickers.
        """
        try:
            loc_data = self.locations[self.current_location.get()]
            today = date.today()

            if status_type == "available":
                for status_key in STATUS_KEYS:
                    status_dict = loc_data[status_key]
                    if name in status_dict and status_dict[name].start_date <= today:
                        status_dict.pop(name)
            elif status_type == "training":
                # Toggle: Out of Queue needs no dates, so re-applying it clears it.
                if name in loc_data["training_status"]:
                    del loc_data["training_status"][name]
                else:
                    loc_data["training_status"][name] = StatusDuration(today, None)
            else:
                status_key = f"{status_type}_status"
                loc_data[status_key].pop(name, None)
                loc_data[status_key][name] = StatusDuration(
                    start_date, end_date, half_day=half_day, half_day_period=half_day_period
                )

            self._update_status_display(name)
            self.save_data(self.current_location.get())
        except Exception as e:
            print(f"Error setting status: {e}")
            messagebox.showerror("Error", "Failed to set status. Please try again.")

    def _get_active_status(self, name):
        today = date.today()
        statuses = []
        loc_data = self.locations[self.current_location.get()]

        for status_type in STATUS_KEYS:
            status_dict = loc_data[status_type]
            if name in status_dict:
                duration = status_dict[name]
                if self._status_currently_blocks(name, duration, today=today):
                    remaining = ""
                    if duration.end_date:
                        days_left = (duration.end_date - today).days
                        remaining = f" ({days_left} days remaining)"
                    statuses.append(f"{self._status_display_name(status_type, duration)}{remaining}")

        return statuses

    def _update_status_display(self, name):
        """Update the status display for a specific name."""
        statuses = self._get_active_status(name)
        loc_data = self.locations[self.current_location.get()]

        status_text = f"Status for {name}:\n{', '.join(statuses) if statuses else Status.AVAILABLE}"

        if statuses:
            status_text += "\n\nDuration:"
            for status_type in ("out_of_office_status", "vacation_status", "training_status", "sick_leave_status"):
                if name in loc_data[status_type]:
                    duration = loc_data[status_type][name]
                    end_date = duration.end_date or "Indefinite"
                    status_text += f"\n{duration.start_date} to {end_date}"

        self._update_name_status_tree()

        if self.is_initialized and hasattr(self, "status_text") and self.status_text:
            self._update_status_overview()

    def _remove_upcoming_status(self, name):
        """Remove any upcoming (future-dated) statuses for `name`."""
        try:
            today = date.today()
            loc_data = self.locations[self.current_location.get()]

            for status_type in STATUS_KEYS:
                status_dict = loc_data[status_type]
                if name in status_dict:
                    duration = status_dict[name]
                    if duration.start_date > today:
                        status_dict.pop(name)

            self._update_status_display(name)
            self.save_data(self.current_location.get())
        except Exception as e:
            print(f"Error removing upcoming status: {e}")
            messagebox.showerror("Error", "Failed to remove upcoming statuses. Please try again.")

    def _selected_manage_name(self):
        """The name selected in the Manage Names tree, or None (after warning)."""
        selection = self.name_status_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a name.")
            return None
        return self.name_status_tree.item(selection[0])["values"][0]

    def _open_status_dialog_for_selection(self):
        """Open the status dialog for whichever name is selected in the tree."""
        name = self._selected_manage_name()
        if name:
            self._open_status_dialog(name)

    def _show_status_menu(self, button):
        """Pop up the quick Set Status dropdown for the selected name.

        Available, Out of Queue, and Out of Office all need no more than
        "today", so they apply directly from here with one click. Vacation
        and Sick Leave need a date range, so those open the status dialog
        (with its calendar) preselected to that status. "Set Status..."
        opens the dialog as-is, for reviewing/editing whatever's already
        set, including scheduling a future Out of Office day.
        """
        name = self._selected_manage_name()
        if not name:
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Set Status...", command=lambda: self._open_status_dialog(name))
        menu.add_separator()
        menu.add_command(label=Status.AVAILABLE, command=lambda: self._apply_status(name, "available"))
        today = date.today()
        menu.add_command(
            label=Status.OOO, command=lambda: self._apply_status(name, "out_of_office", today, today)
        )
        menu.add_command(
            label=Status.VACATION, command=lambda: self._open_status_dialog(name, preselect="vacation")
        )
        menu.add_command(
            label=Status.SICK, command=lambda: self._open_status_dialog(name, preselect="sick_leave")
        )
        menu.add_command(label=Status.TRAINING, command=lambda: self._apply_status(name, "training"))
        menu.add_separator()
        menu.add_command(label="Clear Upcoming Status", command=lambda: self._remove_upcoming_status(name))

        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _open_status_dialog(self, name, preselect=None):
        """Open a dialog to view/set name's status.

        One dialog per person: a status selector plus calendar date
        pickers (see datepicker.py) for whichever dates that status
        actually needs. Pre-fills from whatever status is currently
        active, so this doubles as an editor, not just a one-shot setter.

        preselect optionally starts the dialog on a specific status
        (e.g. "vacation") instead of whatever's currently active -- used
        by the quick Set Status menu so picking "Vacation" there jumps
        straight to the date pickers instead of making the user click the
        radio button again.
        """
        loc_data = self.locations[self.current_location.get()]
        today = date.today()

        current_type = "available"
        current_duration = None
        for status_type in STATUS_TYPES:
            key = f"{status_type}_status"
            if name in loc_data[key]:
                duration = loc_data[key][name]
                if self._status_currently_blocks(name, duration, today=today):
                    current_type = status_type
                    current_duration = duration
                    break

        if preselect and preselect != current_type:
            # Preselecting a status other than whatever's actually active --
            # don't carry that other status's dates over into this one.
            current_duration = None
        if preselect:
            current_type = preselect

        upcoming = []
        for status_type in STATUS_TYPES:
            key = f"{status_type}_status"
            if name in loc_data[key]:
                duration = loc_data[key][name]
                if duration.start_date > today:
                    upcoming.append(
                        f"{self._status_display_name(key, duration)} ({duration.start_date} to "
                        f"{duration.end_date if duration.end_date else 'ongoing'})"
                    )

        theme = "dark" if self.dark_mode.get() else "light"
        colors = self.colors[theme]

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Set Status — {name}")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(bg=colors["bg"])

        # Anchor next to the Set Status button instead of leaving this at
        # Tk's default placement -- on a multi-monitor setup the default
        # can land the dialog on a completely different screen than the
        # one the app window (and the button just clicked) is on.
        self.root.update_idletasks()
        anchor = getattr(self, "status_menu_button", None)
        if anchor is not None and anchor.winfo_exists():
            x = anchor.winfo_rootx()
            y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        else:
            x = self.root.winfo_rootx() + 40
            y = self.root.winfo_rooty() + 40
        dialog.geometry(f"+{x}+{y}")

        dialog.grab_set()

        row = 0
        ttk.Label(dialog, text=name, font=_font(14, "bold")).grid(
            row=row, column=0, columnspan=2, padx=15, pady=(15, 5), sticky="w"
        )
        row += 1

        active = self._get_active_status(name)
        info_text = "Currently: " + (", ".join(active) if active else Status.AVAILABLE)
        ttk.Label(dialog, text=info_text, font=_font(9)).grid(row=row, column=0, columnspan=2, padx=15, sticky="w")
        row += 1
        if upcoming:
            ttk.Label(dialog, text="Upcoming: " + ", ".join(upcoming), font=_font(9)).grid(
                row=row, column=0, columnspan=2, padx=15, pady=(0, 5), sticky="w"
            )
            row += 1

        ttk.Separator(dialog, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", padx=15, pady=8)
        row += 1

        ttk.Label(dialog, text="New status:", font=_font(10, "bold")).grid(
            row=row, column=0, columnspan=2, padx=15, sticky="w"
        )
        row += 1

        status_var = tk.StringVar(value=current_type)
        status_options = [
            ("available", Status.AVAILABLE),
            ("out_of_office", Status.OOO),
            ("vacation", Status.VACATION),
            ("training", Status.TRAINING),
            ("sick_leave", Status.SICK),
        ]
        radio_frame = ttk.Frame(dialog)
        radio_frame.grid(row=row, column=0, columnspan=2, padx=15, sticky="w")
        row += 1

        # date_frame: Available/Out of Queue's hint text, and Out of
        # Office's single date. range_frame: Vacation/Sick Leave's range
        # calendar. Both grid into the same row and only one is ever
        # shown -- toggled by _refresh_date_fields() below.
        date_frame = ttk.Frame(dialog)
        date_frame.grid(row=row, column=0, columnspan=2, padx=15, pady=(8, 0), sticky="w")
        range_frame = ttk.Frame(dialog)
        range_frame.grid(row=row, column=0, columnspan=2, padx=15, pady=(8, 0), sticky="w")
        row += 1

        hint_var = tk.StringVar()
        hint_label = ttk.Label(date_frame, textvariable=hint_var, font=_font(9), wraplength=380, justify="left")
        start_label = ttk.Label(date_frame, text="Date:")

        default_start = current_duration.start_date if (current_duration and current_type == "out_of_office") else today
        start_entry = DateField(date_frame, colors, initial=default_start)

        # Vacation/Sick Leave: a range calendar -- click a start date (the
        # end defaults to the following day), then click again to pick the
        # actual end date; the days in between get a highlighted bar.
        # Half Day is a checkbox here rather than its own status -- it
        # collapses the range to a single day.
        range_calendar = DatePicker(range_frame, colors)
        range_calendar.grid(row=0, column=0, sticky="w")

        range_label_var = tk.StringVar()
        ttk.Label(range_frame, textvariable=range_label_var, font=_font(9)).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )

        half_day_var = tk.BooleanVar(value=bool(current_duration and getattr(current_duration, "half_day", False)))

        if current_type in ("vacation", "sick_leave") and current_duration is not None:
            range_state = {
                "start": current_duration.start_date,
                "end": current_duration.end_date or current_duration.start_date,
                "end_is_default": False,
            }
        else:
            range_state = {"start": None, "end": None, "end_is_default": True}

        def _refresh_range_display():
            # The picker paints only what set_range() tells it to, so the
            # highlighted cells always match range_state exactly -- no
            # separate "selected day" state to keep in sync.
            range_calendar.clear_selection()
            start, end = range_state["start"], range_state["end"]
            if start is None:
                range_calendar.set_range(None, None)
                range_label_var.set("Click a date to select a start date.")
                _refresh_period_hint()
                return
            end = end or start
            range_calendar.set_range(start, end)
            if half_day_var.get() or start == end:
                range_label_var.set(f"{start}" + (" (half day)" if half_day_var.get() else ""))
            else:
                range_label_var.set(f"{start} to {end}")
            range_calendar.show_month(start)
            _refresh_period_hint()

        def _on_range_click(event=None):
            clicked = range_calendar.get_date()
            if clicked is None:
                return
            if half_day_var.get():
                range_state["start"] = clicked
                range_state["end"] = clicked
                range_state["end_is_default"] = False
            elif range_state["start"] is None or not range_state["end_is_default"]:
                # Fresh pick (nothing selected yet, or the last range was
                # already explicit) -- this click is a new start, with the
                # end defaulting to the following day.
                range_state["start"] = clicked
                range_state["end"] = clicked + timedelta(days=1)
                range_state["end_is_default"] = True
            elif clicked <= range_state["start"]:
                # An earlier (or same) date than the current start -- treat
                # it as restarting the pick from there.
                range_state["start"] = clicked
                range_state["end"] = clicked + timedelta(days=1)
            else:
                range_state["end"] = clicked
                range_state["end_is_default"] = False
            _refresh_range_display()

        range_calendar.bind("<<DateSelected>>", _on_range_click)

        # Which half of the day this half day blocks, and why: derived from
        # this person's own schedule for the day picked (see
        # _half_day_boundary), not a fixed universal cutoff -- the hint
        # below spells out the actual time so it's never a guess.
        half_day_period_var = tk.StringVar(
            value=(getattr(current_duration, "half_day_period", None) if current_duration else None) or "morning"
        )
        period_hint_var = tk.StringVar()

        def _refresh_period_hint():
            if not half_day_var.get() or range_state["start"] is None:
                period_hint_var.set("")
                return
            boundary = self._half_day_boundary(name, range_state["start"].strftime("%A"))
            boundary_str = boundary.strftime("%I:%M %p").lstrip("0")
            if half_day_period_var.get() == "afternoon":
                period_hint_var.set(f"Available until {boundary_str}, then out for the rest of the day.")
            else:
                period_hint_var.set(f"Out until {boundary_str}, then available for the rest of the day.")

        period_frame = ttk.Frame(range_frame)
        ttk.Radiobutton(
            period_frame, text="Morning (in late)", value="morning", variable=half_day_period_var,
            command=_refresh_period_hint,
        ).grid(row=0, column=0, sticky="w", padx=(0, 15))
        ttk.Radiobutton(
            period_frame, text="Afternoon (leaving early)", value="afternoon", variable=half_day_period_var,
            command=_refresh_period_hint,
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(period_frame, textvariable=period_hint_var, font=_font(9), wraplength=380, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )

        def _on_half_day_toggle():
            if half_day_var.get():
                if range_state["start"] is not None:
                    range_state["end"] = range_state["start"]
                    range_state["end_is_default"] = False
                period_frame.grid(row=3, column=0, sticky="w", pady=(4, 0))
            else:
                period_frame.grid_remove()
                if range_state["start"] is not None and range_state["end"] == range_state["start"]:
                    range_state["end"] = range_state["start"] + timedelta(days=1)
                    range_state["end_is_default"] = True
            _refresh_range_display()

        half_day_check = ttk.Checkbutton(
            range_frame, text="Half day", variable=half_day_var, command=_on_half_day_toggle
        )
        half_day_check.grid(row=2, column=0, sticky="w", pady=(4, 0))
        if half_day_var.get():
            period_frame.grid(row=3, column=0, sticky="w", pady=(4, 0))

        def _refresh_date_fields():
            for w in (hint_label, start_label, start_entry):
                w.grid_forget()
            date_frame.grid_remove()
            range_frame.grid_remove()
            status = status_var.get()
            if status == "available":
                hint_var.set("Clears any current status for this person.")
                hint_label.grid(row=0, column=0, sticky="w")
                date_frame.grid()
            elif status == "training":
                hint_var.set("No dates needed — Apply toggles Out of Queue on or off.")
                hint_label.grid(row=0, column=0, sticky="w")
                date_frame.grid()
            elif status == "out_of_office":
                start_label.grid(row=0, column=0, sticky="w", padx=(0, 5))
                start_entry.grid(row=0, column=1, sticky="w")
                date_frame.grid()
            else:  # vacation, sick_leave
                range_frame.grid()
                _refresh_range_display()

        for i, (value, label) in enumerate(status_options):
            ttk.Radiobutton(
                radio_frame, text=label, value=value, variable=status_var,
                command=_refresh_date_fields,
            ).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 25), pady=3)

        _refresh_date_fields()

        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=row, column=0, columnspan=2, sticky="e", padx=15, pady=15)

        def _close():
            dialog.grab_release()
            dialog.destroy()

        def _on_apply():
            status = status_var.get()
            start = end = None
            half_day = False
            if status == "out_of_office":
                start = end = start_entry.get_date()
            elif status in ("vacation", "sick_leave"):
                if range_state["start"] is None:
                    messagebox.showerror(
                        "No Date Selected", "Please select a date on the calendar.", parent=dialog
                    )
                    return
                half_day = half_day_var.get()
                start = range_state["start"]
                end = start if half_day else range_state["end"]
            half_day_period = half_day_period_var.get() if half_day else None
            self._apply_status(name, status, start, end, half_day=half_day, half_day_period=half_day_period)
            _close()

        def _on_clear_upcoming():
            self._remove_upcoming_status(name)
            _close()

        ttk.Button(button_frame, text="Clear Upcoming", command=_on_clear_upcoming).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Cancel", command=_close).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Apply", style="Accent.TButton", command=_on_apply).pack(side="left", padx=5)

        dialog.protocol("WM_DELETE_WINDOW", _close)

    def _initialize_location_data(self, location):
        """Initialize empty data structures for a location.

        Same shape db.empty_location_data() already defines (that's the
        single source of truth for it) -- just with "names" as a deque
        instead of a list, since that's what the rest of this app expects
        for an in-memory (not freshly-loaded-from-the-DB) location.
        """
        if location not in self.locations:
            fresh = empty_location_data()
            fresh["names"] = deque()
            self.locations[location] = fresh
            self.save_data(location)

    def _on_location_changed(self, *args):
        """Handle location change."""
        new_location = self.current_location.get()

        if new_location not in self.locations:
            self._initialize_location_data(new_location)

        self.config["Settings"]["last_location"] = new_location
        self._save_config()

        if hasattr(self, "current_name_var"):
            current_name = self.get_current_name()
            if current_name:
                self.current_name_var.set(current_name)
            else:
                self.current_name_var.set("Add names to begin")

        self._update_schedule_name_combo()

        self._update_all_displays()

    def _update_schedule_name_combo(self):
        """Update the schedule name combo with names from current location."""
        if not hasattr(self, "schedule_name_combo"):
            return

        loc_data = self.locations[self.current_location.get()]

        self.schedule_name_combo["values"] = sorted(list(loc_data["names"]))

        current_name = self.schedule_name_var.get()
        if current_name and current_name not in loc_data["names"]:
            self.schedule_name_var.set("")

    def _initialize_daily_counts_for_location(self, location):
        """Initialize the daily counts for all names in a location."""
        loc_data = self.locations[location]
        for name in loc_data["names"]:
            if name not in loc_data["daily_counts"]:
                loc_data["daily_counts"][name] = {}
            for day in day_name:
                if day not in loc_data["daily_counts"][name]:
                    loc_data["daily_counts"][name][day] = 0

    def _initialize_schedules_for_location(self, location):
        """Initialize the schedules for all names in a location."""
        loc_data = self.locations[location]
        for name in loc_data["names"]:
            if name not in loc_data["schedules"]:
                loc_data["schedules"][name] = {day: [] for day in day_name}

    def _update_location_menu(self):
        """Update the location menu items."""
        if not self.location_menu:
            return

        self.location_menu.delete(0, tk.END)

        for location in sorted(self.locations.keys()):
            self.location_menu.add_radiobutton(
                label=location, variable=self.current_location, value=location
            )

        self.location_menu.add_separator()
        self.location_menu.add_command(label="Rename Current Location...", command=self._rename_location)

    def get_current_name(self):
        """Get the current name from the current location's data."""
        try:
            current_location = self.current_location.get()

            if current_location not in self.locations:
                self._initialize_location_data(current_location)
                return None

            loc_data = self.locations[current_location]
            if not (loc_data and loc_data.get("names")):
                return None

            names = list(loc_data["names"])
            return names[0] if names else None
        except Exception as e:
            print(f"Error getting current name: {str(e)}")
            return None

    def _normalize_rotation_order(self, loc_data):
        """Keep the rotation queue sorted alphabetically, preserving whoever
        is currently "up" (position 0).

        The round robin is always alphabetical -- there's no manual
        reordering (the old Move Up/Down buttons) anymore. _add_name,
        _edit_name, and _remove_name all call this after touching
        loc_data["names"] instead of splicing it by hand, so a new name
        lands in its correct alphabetical position and Next/Previous keep
        cycling A-Z regardless of the order names were added in.
        """
        if not loc_data["names"]:
            return
        current = loc_data["names"][0]
        sorted_names = sorted(loc_data["names"])
        idx = sorted_names.index(current) if current in sorted_names else 0
        loc_data["names"] = deque(sorted_names[idx:] + sorted_names[:idx])

    def _refresh_after_roster_change(self):
        """Refresh every display an Add/Edit/Remove Name action could
        affect, and persist the change. Shared by _add_name/_edit_name/
        _remove_name.
        """
        self._update_name_status_tree()
        self._update_stats_display()
        self._update_schedule_name_combo()
        self._update_status_overview()
        self._update_schedule_tree()
        self.save_data(self.current_location.get())

    def _add_name(self):
        """Add a new name to the current location."""
        new_name = self.new_name_var.get().strip()

        if not new_name:
            messagebox.showwarning("Invalid Input", "Please enter a name.")
            return

        loc_data = self.locations[self.current_location.get()]

        if new_name in loc_data["names"]:
            messagebox.showwarning("Duplicate Name", "This name already exists in the current location.")
            return

        loc_data["names"].append(new_name)
        self._normalize_rotation_order(loc_data)
        loc_data["name_counts"][new_name] = 0
        loc_data["daily_counts"][new_name] = {day: 0 for day in day_name}
        loc_data["schedules"][new_name] = {day: [] for day in day_name}

        self._refresh_after_roster_change()

        self.new_name_var.set("")

    def _edit_name(self):
        """Edit the selected name."""
        selection = self.name_status_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a name to edit.")
            return

        item = self.name_status_tree.item(selection[0])
        old_name = item["values"][0]

        new_name = self.new_name_var.get().strip()

        if not new_name:
            messagebox.showwarning("Invalid Input", "Please enter a new name.")
            return

        loc_data = self.locations[self.current_location.get()]

        if new_name in loc_data["names"] and new_name != old_name:
            messagebox.showwarning("Duplicate Name", "This name already exists in the current location.")
            return

        names_list = list(loc_data["names"])
        index = names_list.index(old_name)
        names_list[index] = new_name
        loc_data["names"] = deque(names_list)
        self._normalize_rotation_order(loc_data)

        if old_name in loc_data["name_counts"]:
            loc_data["name_counts"][new_name] = loc_data["name_counts"].pop(old_name)

        if old_name in loc_data["daily_counts"]:
            loc_data["daily_counts"][new_name] = loc_data["daily_counts"].pop(old_name)

        if old_name in loc_data["schedules"]:
            loc_data["schedules"][new_name] = loc_data["schedules"].pop(old_name)

        for status_type in STATUS_KEYS:
            if old_name in loc_data[status_type]:
                loc_data[status_type][new_name] = loc_data[status_type].pop(old_name)

        self._refresh_after_roster_change()

        self.new_name_var.set("")

    def _remove_name(self):
        """Remove the selected name."""
        selection = self.name_status_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a name to remove.")
            return

        item = self.name_status_tree.item(selection[0])
        name = item["values"][0]

        if not messagebox.askyesno("Confirm Removal", f"Are you sure you want to remove {name}?"):
            return

        loc_data = self.locations[self.current_location.get()]

        names_list = list(loc_data["names"])
        names_list.remove(name)
        loc_data["names"] = deque(names_list)
        self._normalize_rotation_order(loc_data)

        loc_data["name_counts"].pop(name, None)
        loc_data["daily_counts"].pop(name, None)
        loc_data["schedules"].pop(name, None)

        for status_type in STATUS_KEYS:
            loc_data[status_type].pop(name, None)

        self._refresh_after_roster_change()

    def _update_name_status_tree(self):
        """Update the name status tree with current data."""
        if not (hasattr(self, "name_status_tree") and self.name_status_tree):
            return

        for item in self.name_status_tree.get_children():
            self.name_status_tree.delete(item)

        today = date.today()
        loc_data = self.locations[self.current_location.get()]

        names_list = sorted(list(loc_data["names"]))

        search_text = getattr(self, "name_search_var", None)
        if search_text is not None:
            search_text = search_text.get().strip().lower()
            if search_text:
                names_list = [n for n in names_list if search_text in n.lower()]

        for name in names_list:
            current_status = []
            current_duration = []
            upcoming_status = []
            upcoming_duration = []

            for status_type in STATUS_KEYS:
                if name in loc_data[status_type]:
                    duration = loc_data[status_type][name]
                    status_text = self._status_display_name(status_type, duration)

                    if self._status_currently_blocks(name, duration, today=today):
                        current_status.append(status_text)
                        if duration.end_date:
                            current_duration.append(f"{duration.start_date} to {duration.end_date}")
                        else:
                            current_duration.append("Ongoing")
                    elif duration.start_date > today:
                        upcoming_status.append(status_text)
                        upcoming_duration.append(f"{duration.start_date} to {duration.end_date or 'ongoing'}")

            self.name_status_tree.insert(
                "",
                "end",
                values=(
                    name,
                    ", ".join(current_status) or Status.AVAILABLE,
                    ", ".join(current_duration) or "N/A",
                    ", ".join(upcoming_status) or "None",
                    ", ".join(upcoming_duration) or "N/A",
                ),
                tags=("status-unavailable" if current_status else "status-available",),
            )

        # Re-apply whatever sort the user last clicked, so it survives the
        # rebuild instead of silently reverting to name order on every save.
        if self._sort_column:
            self._sort_treeview(self._sort_column, reverse=self._sort_reverse)

    def _sort_treeview(self, column, reverse=None):
        """Sort the Manage Names tree by a column; clicking a header again reverses it.

        Wired up from every column heading in _setup_manage_tab (this was
        referenced there but never implemented anywhere in the class).
        """
        tree = self.name_status_tree
        if not (hasattr(self, "name_status_tree") and tree):
            return

        columns = ("Name", "Current Status", "Current Duration", "Upcoming Status", "Upcoming Duration")
        if column not in columns:
            return

        if reverse is None:
            reverse = self._sort_column == column and not self._sort_reverse

        items = [(tree.set(item, column), item) for item in tree.get_children("")]
        items.sort(key=lambda pair: pair[0].lower(), reverse=reverse)
        for index, (_, item) in enumerate(items):
            tree.move(item, "", index)

        self._sort_column = column
        self._sort_reverse = reverse

        for col in columns:
            arrow = (" ▼" if reverse else " ▲") if col == column else ""
            tree.heading(col, text=col + arrow)

    def load_data(self, verify_schema=False):
        """Load data from the database and initialize data structures.

        verify_schema=True also checks (and repairs, if needed) the tables
        on the same connection -- used once at startup.
        """
        if self.db is None:
            return
        try:
            locations = self.db.load_all(verify_schema=verify_schema)
            self.locations.clear()
            for location, location_data in locations.items():
                location_data["names"] = deque(location_data["names"])
                self.locations[location] = location_data
        except DatabaseError as e:
            print(f"Error loading data: {e}")
            messagebox.showerror("Error", f"Failed to load data from the database.\n\n{e}")
        except Exception as e:
            print(f"Error loading data: {e}")
            messagebox.showerror("Error", "Failed to load data. Please check your database connection.")

if __name__ == "__main__":
    root = tk.Tk()
    app = TicketAssignmentApp(root)
    if getattr(app, "db", None) is not None:
        root.mainloop()
