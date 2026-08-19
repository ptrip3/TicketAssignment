"""SQL Server data-access layer for the Ticket Assignment app.

All persistence for location data (names, rotation order, counts,
schedules, statuses) goes through this module; name_selector.py does not
touch the filesystem for app data, only for config.ini.

Two backends, chosen automatically by platform (override with the
`backend` constructor arg if needed):

  - "pyodbc" (Windows): uses the system's ODBC Driver for SQL Server.
    Supports Windows Integrated Auth (Trusted_Connection) for seamless
    domain login with no stored password.
  - "pytds" (everywhere else, in particular macOS): python-tds is a pure
    Python TDS-protocol client with no system driver to install, which
    matters for a double-click-and-run app bundle -- it avoids requiring
    Homebrew + Microsoft's ODBC driver on every client. By default it
    connects with a SQL Server login (username/password); it can also do
    NTLM domain authentication when pyspnego is installed (see
    _pytds_ntlm_auth). It cannot resolve `host\\instance`-style
    named-instance addresses via the SQL Browser service the way pyodbc
    can (that requires SQL Browser to be running and UDP 1434 reachable,
    which is often disabled) -- so it needs an explicit host and TCP port,
    and the target server needs TCP/IP enabled (not just Named
    Pipes/Shared Memory, which is all a default SQL Server Express install
    turns on).

Both backends are wrapped behind the same Database interface below; every
query in this file is written once, with `?`-style placeholders and tuple
params, and works unchanged on both (see _CursorAdapter).

Every write is wrapped in an explicit transaction, so SQL Server's own
locking handles concurrent access between clients. A fresh connection is
opened per call rather than held open, since the app talks to the database
from both the UI thread and a background polling thread, and neither
backend's connections are safe to share across threads.

Targets SQL Server 2017+ (see schema.sql for the DDL this expects).
"""

import os
import re
import sys

from models import StatusDuration

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
STATUS_TYPES = ("out_of_office", "vacation", "training", "sick_leave", "half_day")

if getattr(sys, "frozen", False):
    # Inside a PyInstaller bundle, schema.sql isn't at a path relative to
    # this file on disk -- PyInstaller extracts bundled data files to a
    # temp dir at runtime, exposed via sys._MEIPASS. See the .spec file's
    # `datas` entry, which is what puts schema.sql there in the first place.
    _APP_DIR = sys._MEIPASS
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_PATH = os.path.join(_APP_DIR, "schema.sql")

DEFAULT_PYODBC_DRIVER = "ODBC Driver 17 for SQL Server"
DEFAULT_PORT = 1433


def default_backend():
    """Which DB driver backend to use on this platform, absent an override."""
    return "pyodbc" if sys.platform == "win32" else "pytds"


class DatabaseError(Exception):
    """Connection/config/query problems the UI should surface to the user."""


def empty_location_data():
    """Return a fresh, empty location dict in the shape the app expects."""
    return {
        "names": [],
        "name_counts": {},
        "daily_counts": {},
        "schedules": {},
        "out_of_office_status": {},
        "vacation_status": {},
        "training_status": {},
        "sick_leave_status": {},
        "half_day_status": {},
    }


class _CursorAdapter:
    """Wraps a raw DB-API cursor so every call site in this file can always
    write `?`-style placeholders with params as a tuple, regardless of
    backend. pyodbc understands `?` natively; pytds uses pyformat (`%s`)
    placeholders, so this translates for that backend only.
    """

    def __init__(self, raw_cursor, backend):
        self._cursor = raw_cursor
        self._backend = backend

    def execute(self, sql, params=()):
        if self._backend == "pytds":
            sql = sql.replace("?", "%s")
        self._cursor.execute(sql, params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class Database:
    def __init__(
        self,
        server,
        database,
        backend=None,
        driver=None,
        port=DEFAULT_PORT,
        trusted_connection=True,
        uid=None,
        pwd=None,
        timeout=10,
    ):
        self.backend = backend or default_backend()
        self.server = server
        self.database = database
        self.driver = driver or DEFAULT_PYODBC_DRIVER  # pyodbc only
        self.port = port  # pytds only
        # "Authenticate as a domain identity instead of a SQL Server
        # login" -- for pyodbc this means silent Windows/Kerberos
        # integrated auth (uid/pwd ignored); for pytds there's no
        # cross-platform OS-level SSO to hook into, so it means NTLM
        # using uid/pwd as the domain username/password instead of a SQL
        # login (see _pytds_ntlm_auth()). Same intent, different
        # mechanism per backend.
        self.trusted_connection = trusted_connection
        self.uid = uid
        self.pwd = pwd
        self.timeout = timeout

        if self.backend == "pyodbc":
            try:
                import pyodbc
            except ImportError as e:
                raise DatabaseError(
                    "pyodbc isn't installed (pip install pyodbc). Required on Windows; on "
                    "macOS/Linux it also needs a system ODBC driver -- see README."
                ) from e
            self._driver_module = pyodbc
        elif self.backend == "pytds":
            try:
                import pytds
            except ImportError as e:
                raise DatabaseError(
                    "python-tds is required on this platform but isn't installed "
                    "(pip install python-tds)."
                ) from e
            self._driver_module = pytds
        else:
            raise DatabaseError(f"Unknown database backend: {self.backend!r}")

    def with_database(self, database_name):
        """A copy of this Database pointed at a different database name on
        the same server/credentials -- e.g. to connect to "master" for
        setup checks without repeating every backend-specific field.
        """
        return Database(
            backend=self.backend,
            server=self.server,
            database=database_name,
            driver=self.driver,
            port=self.port,
            trusted_connection=self.trusted_connection,
            uid=self.uid,
            pwd=self.pwd,
            timeout=self.timeout,
        )

    @classmethod
    def from_config(cls, config):
        """Build a Database from the [Database] section of a configparser config."""
        if "Database" not in config:
            raise DatabaseError("No [Database] section in config.ini")
        section = config["Database"]
        return cls(
            server=section.get("server", ""),
            database=section.get("database", ""),
            backend=section.get("backend", fallback=None) or None,
            driver=section.get("driver", fallback=None) or None,
            port=section.getint("port", fallback=DEFAULT_PORT),
            trusted_connection=section.getboolean("trusted_connection", fallback=True),
            uid=section.get("uid", fallback=None) or None,
            pwd=section.get("pwd", fallback=None) or None,
        )

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------

    def _pyodbc_connection_string(self, database):
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server}",
            f"DATABASE={database}",
        ]
        if self.trusted_connection:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.uid}")
            parts.append(f"PWD={self.pwd}")
        return ";".join(parts) + ";"

    def _connect(self, database=None, autocommit=False):
        database = database if database is not None else self.database
        try:
            if self.backend == "pyodbc":
                conn_str = self._pyodbc_connection_string(database)
                return self._driver_module.connect(conn_str, timeout=self.timeout, autocommit=autocommit)
            else:  # pytds
                connect_kwargs = dict(
                    server=self.server,
                    port=self.port,
                    database=database,
                    timeout=self.timeout,
                    login_timeout=self.timeout,
                    autocommit=autocommit,
                )
                if self.trusted_connection:
                    connect_kwargs["auth"] = self._pytds_ntlm_auth()
                else:
                    connect_kwargs["user"] = self.uid
                    connect_kwargs["password"] = self.pwd
                return self._driver_module.connect(**connect_kwargs)
        except self._driver_module.Error as e:
            raise DatabaseError(str(e)) from e
        except DatabaseError:
            raise
        except Exception as e:
            # Covers failures from the NTLM path that aren't pytds.Error --
            # a missing pyspnego install, or an auth/crypto failure from
            # inside it during the handshake itself.
            raise DatabaseError(str(e)) from e

    def _pytds_ntlm_auth(self):
        """Build a pytds auth object for NTLM authentication -- lets
        macOS/Linux clients authenticate with their own domain username
        and password without needing Microsoft's ODBC driver installed
        (unlike the pyodbc+Kerberos path this app also supports), just
        the pure-Python `pyspnego` package (pip install pyspnego; no
        system libraries, no Homebrew).

        NTLM specifically, not "negotiate" (which would silently prefer
        Kerberos when a ticket happens to be available): a mode that
        depends on ambient ticket state makes failures harder to diagnose.
        Use the pyodbc backend if you want Kerberos.
        """
        if not self.uid or not self.pwd:
            raise DatabaseError("NTLM authentication needs a domain username and password.")
        from pytds.login import SpnegoAuth

        try:
            return SpnegoAuth(
                username=self.uid,
                password=self.pwd,
                hostname=self.server,
                service="MSSQLSvc",
                protocol="ntlm",
            )
        except ImportError as e:
            raise DatabaseError(
                "NTLM authentication needs the 'pyspnego' package (pip install pyspnego)."
            ) from e

    def _cursor(self, conn):
        return _CursorAdapter(conn.cursor(), self.backend)

    def test_connection(self):
        """Raise DatabaseError if the connection fails; return True otherwise."""
        conn = self._connect(autocommit=True)
        conn.close()
        return True

    def database_exists(self):
        """Check whether self.database exists on the server (connects to master)."""
        conn = self._connect(database="master", autocommit=True)
        try:
            cur = self._cursor(conn)
            cur.execute("SELECT 1 FROM sys.databases WHERE name = ?", (self.database,))
            return cur.fetchone() is not None
        finally:
            conn.close()

    def create_database(self):
        """CREATE DATABASE self.database if it doesn't already exist."""
        conn = self._connect(database="master", autocommit=True)
        try:
            cur = self._cursor(conn)
            cur.execute("SELECT 1 FROM sys.databases WHERE name = ?", (self.database,))
            if not cur.fetchone():
                # Database name can't be parameterized; it's only ever the
                # value the user configured for this app, not free-form
                # external input, but guard against obviously bad names.
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.database):
                    raise DatabaseError(f"Refusing to create database with unusual name: {self.database!r}")
                cur.execute(f"CREATE DATABASE [{self.database}]")
        finally:
            conn.close()

    def ensure_schema(self):
        """Create tables from schema.sql if they don't exist yet (idempotent)."""
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            sql = f.read()
        batches = [b.strip() for b in re.split(r"(?m)^\s*GO\s*$", sql) if b.strip()]
        conn = self._connect(autocommit=True)
        try:
            cur = self._cursor(conn)
            for batch in batches:
                cur.execute(batch)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def load_all(self):
        """Load every location's full data.

        Returns {location_name: location_data} where location_data matches
        the shape the app keeps in memory, except "names" is a plain list
        (the app wraps it in a deque itself) -- see empty_location_data().
        """
        conn = self._connect(autocommit=True)
        try:
            cur = self._cursor(conn)
            locations = {}

            cur.execute("SELECT LocationName FROM dbo.Locations")
            for (loc_name,) in cur.fetchall():
                locations[loc_name] = empty_location_data()

            cur.execute("""
                SELECT L.LocationName, N.NameId, N.PersonName, N.TotalCount
                FROM dbo.Names N
                JOIN dbo.Locations L ON N.LocationId = L.LocationId
                ORDER BY L.LocationName, N.QueuePosition
            """)
            name_rows = cur.fetchall()
            name_id_to_person = {}
            for loc_name, name_id, person_name, total_count in name_rows:
                loc = locations[loc_name]
                loc["names"].append(person_name)
                loc["name_counts"][person_name] = total_count
                loc["schedules"][person_name] = {d: [] for d in DAY_NAMES}
                name_id_to_person[name_id] = (loc_name, person_name)

            if name_id_to_person:
                cur.execute("SELECT NameId, DayOfWeek, DayCount FROM dbo.DailyCounts")
                for name_id, day, count in cur.fetchall():
                    if name_id not in name_id_to_person:
                        continue
                    loc_name, person_name = name_id_to_person[name_id]
                    daily = locations[loc_name]["daily_counts"].setdefault(
                        person_name, {d: 0 for d in DAY_NAMES}
                    )
                    daily[day] = count

                cur.execute("SELECT NameId, DayOfWeek, TimeRange FROM dbo.Schedules ORDER BY NameId, DayOfWeek, TimeRange")
                for name_id, day, time_range in cur.fetchall():
                    if name_id not in name_id_to_person:
                        continue
                    loc_name, person_name = name_id_to_person[name_id]
                    locations[loc_name]["schedules"][person_name][day].append(time_range)

                cur.execute(
                    "SELECT NameId, StatusType, StartDate, EndDate, HalfDay, HalfDayPeriod "
                    "FROM dbo.StatusEntries"
                )
                for name_id, status_type, start, end, half_day, half_day_period in cur.fetchall():
                    if name_id not in name_id_to_person:
                        continue
                    loc_name, person_name = name_id_to_person[name_id]
                    locations[loc_name][f"{status_type}_status"][person_name] = StatusDuration(
                        start, end, half_day=bool(half_day), half_day_period=half_day_period
                    )

            return locations
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_location(self, location_name, location_data):
        """Persist one location's full in-memory state.

        This deletes and reinserts that location's Names (and, via
        ON DELETE CASCADE, their DailyCounts/Schedules/StatusEntries) inside
        one transaction, then writes the current in-memory state back.
        Writing the whole location rather than diffing is simple and
        correct for the small rosters this app manages. Scoping it to the
        one location being saved means concurrent edits to a different
        location can't be clobbered.
        """
        conn = self._connect(autocommit=False)
        try:
            cur = self._cursor(conn)

            cur.execute("SELECT LocationId FROM dbo.Locations WHERE LocationName = ?", (location_name,))
            row = cur.fetchone()
            if row:
                location_id = row[0]
                cur.execute("DELETE FROM dbo.Names WHERE LocationId = ?", (location_id,))
            else:
                cur.execute(
                    "INSERT INTO dbo.Locations (LocationName) OUTPUT INSERTED.LocationId VALUES (?)",
                    (location_name,),
                )
                location_id = cur.fetchone()[0]

            names_list = list(location_data.get("names", []))
            name_ids = {}
            for position, person_name in enumerate(names_list):
                total_count = location_data.get("name_counts", {}).get(person_name, 0)
                cur.execute(
                    "INSERT INTO dbo.Names (LocationId, PersonName, QueuePosition, TotalCount) "
                    "OUTPUT INSERTED.NameId VALUES (?, ?, ?, ?)",
                    (location_id, person_name, position, total_count),
                )
                name_ids[person_name] = cur.fetchone()[0]

            daily_counts = location_data.get("daily_counts", {})
            for person_name, name_id in name_ids.items():
                for day, count in daily_counts.get(person_name, {}).items():
                    if count:
                        cur.execute(
                            "INSERT INTO dbo.DailyCounts (NameId, DayOfWeek, DayCount) VALUES (?, ?, ?)",
                            (name_id, day, count),
                        )

            schedules = location_data.get("schedules", {})
            for person_name, name_id in name_ids.items():
                for day, times in schedules.get(person_name, {}).items():
                    for time_range in times:
                        cur.execute(
                            "INSERT INTO dbo.Schedules (NameId, DayOfWeek, TimeRange) VALUES (?, ?, ?)",
                            (name_id, day, time_range),
                        )

            for status_type in STATUS_TYPES:
                status_dict = location_data.get(f"{status_type}_status", {})
                for person_name, duration in status_dict.items():
                    name_id = name_ids.get(person_name)
                    if name_id is None:
                        continue
                    cur.execute(
                        "INSERT INTO dbo.StatusEntries "
                        "(NameId, StatusType, StartDate, EndDate, HalfDay, HalfDayPeriod) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (name_id, status_type, duration.start_date, duration.end_date,
                         getattr(duration, "half_day", False), getattr(duration, "half_day_period", None)),
                    )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def rename_location(self, old_name, new_name):
        conn = self._connect(autocommit=True)
        try:
            cur = self._cursor(conn)
            cur.execute("UPDATE dbo.Locations SET LocationName = ? WHERE LocationName = ?", (new_name, old_name))
        finally:
            conn.close()
