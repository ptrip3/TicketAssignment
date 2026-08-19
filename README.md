# Ticket Assignment

A small Tkinter desktop app for round-robin ticket/task assignment across a
team, backed by a shared SQL Server database so multiple people can use it
at once. Runs on Windows and macOS.

## What it does

- **Locations** — each "location" (e.g. an office or site) has its own
  independent round-robin queue of names, stats, schedules, and statuses.
- **Round-robin assignment** — "Assign Ticket" hands the current person a
  ticket, tallies it, and rotates to the next *available* person. "Previous"
  / "Next" move through the queue without tallying a ticket. Queue order is
  always alphabetical, so adding someone slots them into place
  automatically.
- **Status tracking** — each person can be marked Out of Office, Vacation,
  Sick Leave, or Out of Queue. Vacation and Sick Leave take a date range
  picked on a calendar; Out of Queue is an on/off toggle with no dates.
  Unavailable people are skipped by the rotation, and expired statuses are
  cleared automatically once a day.
- **Half days** — Vacation and Sick Leave entries can be marked as a half
  day, either Morning ("in late") or Afternoon ("leaving early"). The
  cutoff is that person's own schedule midpoint for the day, so someone on
  a morning half day becomes selectable again once their midpoint passes,
  without anyone having to click anything.
- **Weekly schedules** — set a bulk Monday–Friday time block or per-day time
  ranges for each person; the Selector tab shows today's schedule for
  whoever is currently up.
- **Stats** — total times-selected and a per-day breakdown, per location,
  with a reset option.
- **Searchable, sortable roster** — filter the Manage Names list as you
  type, and click any column header to sort by it; click again to reverse.
- **Dark mode**, toggled from the File menu, persisted in `config.ini`.
- **Shared storage** — all data lives in a SQL Server database, so everyone
  on the team sees the same queues. A background thread polls the database
  every 0.5s so other users' changes show up without restarting the app.

## Architecture

| File | Purpose |
|---|---|
| `name_selector.py` | The Tkinter app itself (UI + in-memory state). |
| `models.py` | `Status` / `StatusDuration` — shared domain types. |
| `datepicker.py` | Calendar date-picker widgets, built directly on tkinter. |
| `db.py` | All SQL Server access (`Database` class) — connections, schema setup, load/save. Dual-backend, see below. |
| `schema.sql` | The table definitions. Idempotent; `db.py` can also apply it for you. |
| `merge_locations.sql` | One-off administrative script for merging two locations into one. |
| `migrate_json_to_sql.py` | One-time importer for data from the older JSON-file version. |
| `TicketAssignment_windows.spec` | PyInstaller spec for building a Windows `.exe`. |
| `TicketAssignment_mac.spec` | PyInstaller spec for building a macOS `.app` bundle. |

The database has five tables: `Locations`, `Names` (one row per person per
location, with a `QueuePosition` that encodes round-robin order),
`DailyCounts`, `Schedules`, and `StatusEntries` (all five status types in
one table, keyed by type). See `schema.sql` for the full DDL and design
notes.

Every write goes through `db.py`'s `save_location()`, which replaces one
location's entire set of names/counts/schedules/statuses inside a single
transaction. Writing the whole location rather than diffing keeps things
simple and is fine for the small rosters this app manages; scoping it to
one location means saving one can't clobber concurrent edits to another.

## Cross-platform support

`db.py` has two backends, picked automatically by platform:

- **Windows: `pyodbc`**, against a system-installed ODBC Driver for SQL
  Server. Supports Windows Integrated Auth (`Trusted_Connection`) for
  seamless AD login with no password stored anywhere.
- **macOS: `python-tds`** by default, a pure-Python SQL Server client with
  no system driver to install. This keeps the macOS build a double-click
  `.app` that runs with nothing else installed — macOS has no built-in
  ODBC support the way Windows does, and requiring Homebrew + Microsoft's
  ODBC driver on every client machine is a significant deployment burden.
  The trade-offs: it connects with a SQL Server login by default (see NTLM
  below for domain credentials), and unlike `pyodbc` it can't resolve
  `host\instance`-style named-instance addresses via the SQL Browser
  service (that requires SQL Browser running and UDP 1434 reachable, which
  is frequently disabled) — so it asks for an explicit host and TCP port.
- **macOS: `python-tds` + NTLM, optionally**, for domain-joined Macs — lets
  users authenticate with their own domain username/password instead of a
  SQL Server login. Needs nothing but `pyspnego` (already in
  `requirements.txt` for non-Windows platforms): pure Python, no system
  driver, no Homebrew. The cost versus Kerberos is that it's a typed
  password rather than silent single sign-on.
- **macOS: `pyodbc`, optionally**, for domain-joined Macs that can get a
  Kerberos ticket — true single sign-on, no password prompt, the same way
  Windows clients work. Costs more to set up than NTLM: needs Microsoft's
  ODBC driver installed via Homebrew, which some organizations don't
  allow. See [Domain authentication on macOS](#domain-authentication-on-macos).

**The SQL Server itself needs TCP/IP enabled** (not just Named
Pipes/Shared Memory, which is all a default SQL Server Express install
turns on) **on a known port**, for macOS clients to reach it at all. On a
default SQL Server Express install you'll likely need to turn TCP/IP on in
SQL Server Configuration Manager (or the equivalent registry keys under
`SuperSocketNetLib\Tcp`) and restart the service. Windows clients using
`pyodbc` don't need this — they can fall back to Named Pipes/Shared
Memory.

Both backends sit behind the same `Database` class interface, so the rest
of the app doesn't know or care which one is active.

Everything else that differs by platform:

- **Fonts**: the UI asks Tk for `TkDefaultFont`'s actual family (`_font()`
  in `name_selector.py`), which Tk resolves to the right native font per
  platform — no per-platform font name guessing.
- **config.ini location**: Windows keeps it next to the script/`.exe`.
  macOS uses `~/Library/Application Support/Ticket Assignment/config.ini`
  instead, since writing inside a signed `.app` bundle isn't reliable and
  isn't where macOS apps are expected to keep settings. See
  `_get_config_dir()`.
- **Menu bar**: the File menu and tab buttons are an in-window `ttk`
  toolbar rather than a native menu bar. On Windows a native menu bar is
  drawn by Win32 and can't be themed for dark mode; using an in-window
  toolbar on both platforms keeps the app consistent and dark-mode
  correct. See `_create_menu()`.
- **Connection dialog**: on macOS it shows Server/Port/Database/SQL
  Login/Password by default (no ODBC driver picker, no Authentication
  chooser, since neither applies to plain `python-tds`). If `pyspnego`
  and/or `pyodbc` are importable, an "Authentication:" chooser appears
  with up to three options — **SQL Server Login** (always), **Domain Login
  (NTLM)** (if `pyspnego` is present), and **Domain Login (Kerberos)** (if
  `pyodbc` is present). Kerberos swaps Port for an ODBC Driver picker and
  disables SQL Login/Password, mirroring the Windows dialog's "Use Windows
  Authentication" checkbox; NTLM keeps Port and the login fields (with the
  username field relabeled "Domain Username").

### Domain authentication on macOS

Optional, for domain-joined Macs where users should authenticate as
themselves rather than through a shared SQL Server login. Two options:

#### NTLM (no Homebrew, no ODBC driver)

`pyspnego` is already part of `requirements.txt` for non-Windows
platforms, so there's normally nothing extra to install. To confirm:

```bash
python3 -c "import spnego; print('pyspnego OK')"
```

If that fails, `pip install pyspnego` (or re-run `pip install -r
requirements.txt`).

Then launch the app (or **File → Change Database Connection**), choose
**Domain Login (NTLM)**, and enter the domain username and password. The
username needs the domain prefix — `DOMAIN\username` — or SQL Server
rejects it as coming from an untrusted domain.

The account needs a SQL Server login and permissions on the target
database, the same as it would from a Windows client. Note that NTLM
authenticates whatever credentials are typed, so it does not have to be
the account currently signed in to the Mac.

The server needs nothing special for NTLM specifically: if Windows clients
already authenticate with Windows Authentication, NTLM is already enabled
(it's part of the same Windows Authentication mode, which covers both
Kerberos and NTLM). TCP/IP-on-a-known-port still applies.

#### Kerberos (single sign-on, needs Homebrew)

1. Install Microsoft's ODBC driver and `pyodbc` (deliberately not part of
   `requirements.txt` — see the comment there for why):
   ```bash
   brew install unixodbc
   brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
   brew install msodbcsql17 mssql-tools
   pip install pyodbc
   ```
2. Confirm a valid Kerberos ticket for the domain (domain-joined Macs
   often obtain one automatically at login; if not):
   ```bash
   kinit yourusername@YOURDOMAIN.COM   # realm is conventionally uppercase
   klist                               # confirm a ticket is present
   ```
3. Launch the app (or **File → Change Database Connection**), choose
   **Domain Login (Kerberos)**, pick the ODBC driver, fill in
   Server/Database, and Test Connection.

If it fails, `kinit`/`klist` are the first things to check — a missing or
expired ticket is the most likely cause.

## Requirements

### Windows
- Python 3 with Tk, and `pyodbc` (see [requirements.txt](requirements.txt)).
- An ODBC Driver for SQL Server installed. Check what you have:
  ```powershell
  Get-OdbcDriver -Platform "64-bit" | Where-Object { $_.Name -like "*SQL Server*" }
  ```
  If nothing modern shows up, install "ODBC Driver 17 (or 18) for SQL
  Server" from Microsoft, or install SQL Server itself locally (Express is
  free), which bundles a driver.

### macOS
- Python 3 with Tk (the python.org installer and Homebrew's `python` both
  include Tk; some minimal installs don't — if `python3 -m tkinter` opens
  a small test window, you're fine) and `python-tds` (see
  [requirements.txt](requirements.txt)).
- Nothing else — no Homebrew ODBC packages, no Microsoft driver install,
  unless you specifically want the Kerberos option above.

### Both platforms
- A SQL Server database everyone on the team can reach (SQL Server 2017 or
  later; developed and tested against 2017). This can be an existing
  server, or a fresh one you point the app at on first run.
- If any Mac clients will connect: TCP/IP enabled on the server (see
  [Cross-platform support](#cross-platform-support) above).

## Running it

```bash
python name_selector.py        # Windows
python3 name_selector.py       # macOS
```

On first run, a dialog asks for your SQL Server connection details.
"Test Connection" checks connectivity before you save. If the database
doesn't exist yet, you'll be asked whether to create it; either way, the
required tables are created automatically if they're missing.

Settings (connection details, dark mode, last-used location) are stored in
`config.ini` — see [Cross-platform support](#cross-platform-support) for
where, since it differs by platform. Reconfigure the connection later from
**File → Change Database Connection**.

### Setting up the database yourself instead

If you'd rather set up the schema ahead of time (e.g. so a DBA can review
it first), run `schema.sql` against your target database directly — it's
idempotent, safe to run more than once:

```powershell
sqlcmd -S YOURSERVER\INSTANCE -d TicketAssignment -i schema.sql
```

Then point `config.ini` (or the first-run dialog) at that server and
database.

### Merging two locations

If two locations need to be consolidated into one, `merge_locations.sql`
does it in a single transaction: fill in the two location names at the top
and run it against the database. It refuses to run if the same person
exists in both locations, so those can be resolved by hand first. Back up
the database before running it.

### Migrating from the older JSON-file version

Earlier versions of this app stored data as a single JSON file on a shared
network drive. If you have an existing `ticket_assignment_data.json`,
import it once with:

```bash
python migrate_json_to_sql.py --json-file "\\shared\drive\ticket_assignment_data.json"
```

By default it reads connection info from `config.ini` next to the script;
pass `--server`/`--database` (and `--uid`/`--pwd` for SQL auth) to target a
different database. Use `--dry-run` first to preview what would be
imported without writing anything. See `python migrate_json_to_sql.py --help`
for all options.

## Building standalone apps

Both builds produce something the team can run without installing Python.
PyInstaller doesn't cross-compile, so each has to be built on its own
platform.

### Windows

```powershell
python -m venv build-env
build-env\Scripts\activate
pip install -r requirements.txt
python -m PyInstaller TicketAssignment_windows.spec
```

Output: `dist\Ticket Assignment.exe` — a single file, copy it anywhere.

### macOS

```bash
python3 -m venv build-env
source build-env/bin/activate
pip install -r requirements.txt
python3 -m PyInstaller TicketAssignment_mac.spec
```

Output: `dist/Ticket Assignment.app`. Run from source first
(`python3 name_selector.py`) and confirm it works end to end before
packaging — easier to debug than a built app.

**Opening it the first time**: since this isn't signed with an Apple
Developer ID, macOS Gatekeeper will refuse to open it normally ("cannot be
opened because the developer cannot be verified"). Either right-click the
app → **Open** (and confirm) the first time, or from Terminal:
```bash
xattr -cr "dist/Ticket Assignment.app"
```
This is a one-time step per machine.

## Known limitations

- The background refresh thread polls the database every 0.5s per open
  client. Fine for a small team; if that ever becomes a concern on your
  server, the interval is a single `time.sleep(0.5)` in
  `_start_data_refresh()` in `name_selector.py`.
- Connection details, including any SQL Server or domain password, are
  stored in plain text in `config.ini` on each client machine. Use
  Windows Integrated Auth or Kerberos where possible to avoid storing a
  password at all.
- No app icon is set (both `.spec` files have `icon=None`) — drop an
  `.ico`/`.icns` file next to the spec and point `icon` at it if you want
  one.
- The macOS build is unsigned and unnotarized, hence the Gatekeeper
  workaround above. Fine for internal distribution; wider distribution
  without that warning would need an Apple Developer ID and notarization.

## License

The source in this repository is released under the
[MIT License](LICENSE).

### Third-party dependencies

The source here doesn't vendor any third-party code — everything comes
from PyPI at install time — but the **built** `.exe`/`.app` bundles those
dependencies, and their licenses travel with the binary. Every runtime
dependency is permissively licensed:

| Dependency | License |
|---|---|
| [sv-ttk](https://github.com/rdbende/Sun-Valley-ttk-theme) | MIT |
| [python-tds](https://github.com/denisenkom/pytds) | MIT |
| [pyodbc](https://github.com/mkleehammer/pyodbc) | MIT |
| [pyspnego](https://github.com/jborean93/pyspnego) | MIT |

The date pickers in the Set Status dialog are implemented directly on
`tkinter` (see `datepicker.py`) rather than pulling in a calendar
library. That's deliberate: the widely used `tkcalendar` is GPL-3.0,
which would have made a redistributed binary a combined work subject to
GPL terms. Rolling the widget in-house keeps the whole distributable
permissively licensed, and drops the transitive `Babel` dependency
(~1.1 MB off the built `.exe`).

This is a plain-language summary, not legal advice.
