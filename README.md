# Ticket Assignment

A Tkinter desktop app for round-robin ticket assignment across a team. Data is
stored in a shared SQL Server database, so several people can use the app at
the same time. Runs on Windows and macOS.

## What it does

**Locations.** Each location, such as an office or site, has its own
round-robin queue of names, along with its own stats, schedules, and statuses.

**Round-robin assignment.** **Assign Ticket** gives the current person a
ticket, adds it to their tally, and rotates to the next available person.
**Previous** and **Next** move through the queue without recording a ticket.
The queue is always in alphabetical order, so a new name is inserted in the
correct position automatically.

**Status tracking.** You can mark a person as Out of Office, Vacation, Sick
Leave, or Out of Queue. Vacation and Sick Leave use a date range chosen from a
calendar. Out of Queue is a toggle with no dates. The rotation skips anyone who
is unavailable, and expired statuses are cleared once a day.

**Half days.** You can mark a Vacation or Sick Leave entry as a half day,
either Morning or Afternoon. The cutoff is the midpoint of that person's
schedule for the day. Someone on a morning half day becomes available again
when their midpoint passes, without anyone updating the app.

**Weekly schedules.** Set a Monday through Friday time block for a person, or
set time ranges for individual days. The **Selector** tab shows today's
schedule for whoever is currently up.

**Stats.** Total times selected and a per-day breakdown, for each location.
Counts can be reset.

**Searchable roster.** Filter the **Manage Names** list as you type. Select a
column header to sort by it, and select it again to reverse the order.

**Dark mode.** Toggle it from the **File** menu. The setting is saved in
`config.ini`.

**Shared storage.** All data is stored in SQL Server, so everyone sees the same
queues. A background thread polls the database every 0.5 seconds, so changes
made by other people appear without restarting the app.

## Architecture

```
src/          the application
  name_selector.py   Tkinter app (UI and in-memory state)
  db.py              all SQL Server access; two backends, described below
  models.py          Status and StatusDuration domain types
  datepicker.py      calendar date-picker widgets, built on tkinter
  schema.sql         table definitions, loaded at runtime by db.py
packaging/    building and deploying
  TicketAssignment_windows.spec   PyInstaller spec for the Windows build
  TicketAssignment_mac.spec       PyInstaller spec for the macOS .app
  install.ps1                     all-users Windows installer and updater
  ticketassignment.png            2048px icon master
  ticketassignment.ico            Windows icon, generated from the master
  ticketassignment.icns           macOS icon, generated from the master
tools/        one-off admin scripts
  migrate_json_to_sql.py   importer for the older JSON-file version
  merge_locations.sql      merges two locations into one
  assignment_history.sql   reporting queries over the assignment audit log
```

`schema.sql` is stored next to `db.py` rather than in `tools/` because it's a
runtime resource. `db.py` reads it to create missing tables, and it's bundled
into the built app.

The database has six tables: `Locations`, `Names`, `DailyCounts`, `Schedules`,
`StatusEntries`, and `Assignments`. `Names` holds one row per person per
location, and its `QueuePosition` column encodes the round-robin order.
`StatusEntries` holds all status types in one table, keyed by type. For the
full DDL and design notes, see `schema.sql`.

`Assignments` is an append-only audit log, one row per ticket assigned: when,
which location, who it went to, and which account did it. The assigner and
hostname are filled in by SQL Server itself from the connection
(`SUSER_SNAME()`, `HOST_NAME()`), so they reflect who actually connected rather
than anything the client claims. With Windows Integrated Auth or NTLM that is
the caller's domain account — note it records the account the app is *running
as*, which is the elevated one if it was launched that way.

It deliberately has no foreign keys, unlike every other table. A location save
deletes and reinserts that location's `Names`, regenerating every `NameId`, so
a reference to one would be cascade-deleted almost immediately. Storing names
and locations as text also means the history outlives a renamed, merged or
deleted location. It is written on its own path (`record_assignment()`), never
through `save_location()`, and a failure to log is reported but never aborts
the assignment itself. `tools/assignment_history.sql` has ready-made reporting
queries, plus a retention statement — this is the one table that grows without
bound.

Every write goes through `save_location()` in `db.py`. This replaces one
location's entire set of names, counts, schedules, and statuses in a single
transaction. Writing the whole location instead of comparing changes keeps the
code simple, which suits the small rosters this app manages. Because the write
is scoped to one location, saving it can't overwrite concurrent edits to a
different one.

## Cross-platform support

`db.py` has two backends and selects one based on the platform.

| Platform | Backend | Authentication |
| --- | --- | --- |
| Windows | `pyodbc` | Windows Integrated Auth, or a SQL Server login |
| macOS | `python-tds` | SQL Server login, or NTLM domain login |

**Windows** uses `pyodbc` with a system-installed ODBC Driver for SQL Server.
It supports Windows Integrated Auth, so users sign in with their domain account
and no password is stored.

**macOS** uses `python-tds`, a SQL Server client written in pure Python that
needs no system driver. This keeps the macOS build a double-click `.app` that
runs without anything else installed. macOS has no built-in ODBC support, so
the alternative would be installing Homebrew and Microsoft's ODBC driver on
every client.

Using `python-tds` has two trade-offs:

- It connects with a SQL Server login by default. For domain credentials, see
  [Domain authentication on macOS](#domain-authentication-on-macos).
- It can't resolve `host\instance` named-instance addresses through the SQL
  Browser service, so it asks for an explicit host and TCP port. SQL Browser
  requires UDP 1434 to be reachable, which is often disabled.

> [!IMPORTANT]
> For macOS clients to connect at all, the SQL Server instance must have TCP/IP
> enabled on a known port. A default SQL Server Express installation enables
> only Named Pipes and Shared Memory. To fix this, enable TCP/IP in SQL Server
> Configuration Manager, or set the equivalent registry keys under
> `SuperSocketNetLib\Tcp`, and restart the service. Windows clients don't need
> this, because `pyodbc` can fall back to Named Pipes or Shared Memory.

Both backends are behind the same `Database` class, so the rest of the app
doesn't depend on which one is active.

### Other platform differences

**Fonts.** The UI asks Tk for the actual family behind `TkDefaultFont`, which
Tk resolves to the correct native font on each platform. See `_font()` in
`src/name_selector.py`.

**config.ini location.** Windows uses `%APPDATA%\Ticket Assignment`, which
keeps settings per-account and writable when the app is installed to Program
Files. If `%APPDATA%` can't be written, the app falls back to
`%LOCALAPPDATA%\Ticket Assignment`. This matters in domains where folder
redirection points roaming AppData at a network share. macOS uses
`~/Library/Application Support/Ticket Assignment/config.ini`, because writing
inside an `.app` bundle isn't reliable and isn't where macOS apps store
settings. See `_get_config_dir()`.

**Menu bar.** The File menu and tab buttons are an in-window `ttk` toolbar
rather than a native menu bar. On Windows, a native menu bar is drawn by Win32
and can't be themed for dark mode. Using an in-window toolbar on both platforms
keeps the app consistent. See `_create_menu()`.

**Connection dialog.** Windows shows an ODBC driver list and a **Use Windows
Authentication** checkbox. macOS shows a **Port** field instead, because
`python-tds` has no driver concept. When `pyspnego` can be imported, macOS also
shows an **Authentication** list with **SQL Server Login** and **Domain Login
(NTLM)**.

### Domain authentication on macOS

Use this on domain-joined Macs when users should authenticate as themselves
instead of through a shared SQL Server login.

`pyspnego` is already listed in `requirements.txt` for non-Windows platforms,
so there's usually nothing extra to install. To confirm it's available, run:

```bash
python3 -c "import spnego; print('pyspnego OK')"
```

If that fails, run `pip install -r requirements.txt` again.

To connect with domain credentials:

1. Start the app, or select **File** > **Change Database Connection**.
2. In the **Authentication** list, select **Domain Login (NTLM)**.
3. Enter the domain username in `DOMAIN\username` format, and the password.

> [!NOTE]
> The domain prefix is required. Without it, SQL Server rejects the login as
> coming from an untrusted domain.

The account needs a SQL Server login and permissions on the target database,
the same as it would from a Windows client. NTLM authenticates whatever
credentials you enter, so it doesn't have to be the account signed in to the
Mac.

The server needs no extra configuration for NTLM. If Windows clients already
use Windows Authentication, NTLM is enabled, because both are part of the same
authentication mode. The TCP/IP requirement described earlier still applies.

## Requirements

### Windows

- Python 3 with Tk, and `pyodbc`. See [requirements.txt](requirements.txt).
- An ODBC Driver for SQL Server. To see which drivers are installed, run:

  ```powershell
  Get-OdbcDriver -Platform "64-bit" | Where-Object { $_.Name -like "*SQL Server*" }
  ```

  If no current driver is listed, install ODBC Driver 17 or 18 for SQL Server
  from Microsoft. Installing SQL Server Express also installs a driver.

### macOS

- Python 3 with Tk, and `python-tds`. See [requirements.txt](requirements.txt).
- Nothing else. No Homebrew, no ODBC packages, and no Microsoft driver. This is
  the reason for the `python-tds` backend.

To confirm Tk is present, run `python3 -m tkinter`. A small test window opens if
it is. The python.org installer and Homebrew's `python` both include Tk, but
some minimal installations don't.

### Both platforms

- A SQL Server database that everyone on the team can reach. SQL Server 2017 or
  later is required; the app was developed and tested against 2017. Use an
  existing server, or point the app at a new one on first run.
- If Mac clients will connect, TCP/IP must be enabled on the server. See
  [Cross-platform support](#cross-platform-support).

## Running the app

```bash
python src\name_selector.py     # Windows
python3 src/name_selector.py    # macOS
```

On first run, a dialog asks for your SQL Server connection details. Select
**Test Connection** to check connectivity before saving. If the database
doesn't exist, the app asks whether to create it. Either way, the app creates
any missing tables.

Connection details, dark mode, and the last-used location are stored in
`config.ini`. The location differs by platform; see
[Cross-platform support](#cross-platform-support). To change the connection
later, select **File** > **Change Database Connection**.

### Create the database schema in advance

To review the schema before the app runs, or to have a DBA apply it, run
`schema.sql` against the target database. The script is idempotent, so running
it more than once is safe.

```powershell
sqlcmd -S YOURSERVER\INSTANCE -d TicketAssignment -i src\schema.sql
```

Then point `config.ini`, or the first-run dialog, at that server and database.

### Merge two locations

To consolidate two locations into one, use `tools/merge_locations.sql`. The
script runs in a single transaction, and stops if the same person exists in
both locations so you can resolve the duplicates first.

1. Back up the database.
2. Enter the two location names at the top of the script.
3. Run the script against the database.

### Migrate from the JSON-file version

Earlier versions stored data in a single JSON file on a network share. To
import an existing `ticket_assignment_data.json`, run:

```bash
python tools/migrate_json_to_sql.py --json-file "\\shared\drive\ticket_assignment_data.json"
```

The script reads connection details from the app's `config.ini` by default. To
target a different database, use `--server` and `--database`, plus `--uid` and
`--pwd` for SQL authentication. Use `--dry-run` first to preview the import
without writing anything. For all options, run
`python tools/migrate_json_to_sql.py --help`.

## Building standalone apps

Each build produces an app the team can run without installing Python.
PyInstaller doesn't cross-compile, so build each one on its own platform.

Build output always goes to the project root, whichever directory you run the
build from. Each spec writes to its own folder, `Ticket Assignment Windows` or
`Ticket Assignment macOS`, so builds for both platforms can exist side by side.
The `.exe` and `.app` inside each folder keep the product name.

### Windows

```powershell
python -m venv build-env
build-env\Scripts\activate
pip install -r requirements.txt
python -m PyInstaller packaging\TicketAssignment_windows.spec
```

The output is `dist\Ticket Assignment Windows\`, which contains the launcher
`.exe` and an `_internal\` folder, about 19 MB in total. Both must stay
together.

To install for all users on the current machine:

```powershell
.\packaging\install.ps1
```

Add `-Desktop` to also create a desktop shortcut. The script copies the build
to `%ProgramFiles%\Ticket Assignment` and creates an all-users Start Menu
shortcut, so users never see the `_internal\` folder. The script requires
administrator rights and restarts itself elevated if needed.

The install is machine-wide by design. A per-user install goes into one
account's profile, which other accounts can't read. That matters when the app
is started as a different account than the one signed in, which is a common way
to reach a database the signed-in account has no rights to.

Settings are stored in each account's `%APPDATA%\Ticket Assignment`, not in the
install directory, because Program Files is read-only for standard users.
Settings survive updates and uninstalls, and a `config.ini` from an older
per-user install is migrated automatically. To remove the app and its shortcuts
while keeping settings, run `.\packaging\install.ps1 -Uninstall`.

`install.ps1` is also copied into the build folder, so
`dist\Ticket Assignment Windows` is self-contained. To deploy to other
machines, copy that folder, for example to a network share, and run the
`install.ps1` inside it. The installed copy keeps its own `install.ps1`, so
`-Uninstall` works from there too.

> [!NOTE]
> This build deliberately doesn't use PyInstaller's single-file mode. Onefile
> re-extracts the whole bundle to a temporary directory on every launch, which
> measured 1.9 seconds to first window, compared to 0.46 seconds for this
> layout.

### macOS

```bash
python3 -m venv build-env
source build-env/bin/activate
pip install -r requirements.txt
python3 -m PyInstaller packaging/TicketAssignment_mac.spec
```

The output is `dist/Ticket Assignment.app`, next to the intermediate
`dist/Ticket Assignment macOS/` folder that PyInstaller builds it from.

Before packaging, run the app from source with
`python3 src/name_selector.py` and confirm it works. A frozen bundle is harder
to debug than source.

The macOS spec removes the same unused Tcl/Tk data as the Windows spec:
timezone tables, locale message catalogs, and sample images. Unlike the Windows
spec, it keeps `ssl` and `_ssl`, because `pyspnego` needs them for NTLM domain
login.

> [!IMPORTANT]
> The macOS build isn't signed with an Apple Developer ID, so Gatekeeper blocks
> it the first time it runs and reports that the developer can't be verified.
> To open it, right-click the app and select **Open**, then confirm. You can
> also clear the quarantine attribute from Terminal:
>
> ```bash
> xattr -cr "dist/Ticket Assignment.app"
> ```
>
> This is a one-time step on each machine.

## Known limitations

- The background refresh thread polls the database every 0.5 seconds for each
  open client. This is suitable for a small team. To change the interval, edit
  the `time.sleep(0.5)` call in `_start_data_refresh()` in
  `src/name_selector.py`.
- Connection details, including SQL Server and domain passwords, are stored as
  plain text in `config.ini` on each client machine. On Windows, Windows
  Authentication avoids storing a password. On macOS, both authentication modes
  require one.
- The macOS build is unsigned and unnotarized, which is why Gatekeeper shows a
  warning. This is acceptable for internal distribution. Wider distribution
  without the warning requires an Apple Developer ID and notarization.

## License

The source in this repository is released under the [MIT License](LICENSE).

### Third-party dependencies

The source doesn't vendor any third-party code; dependencies are installed from
PyPI. The built `.exe` and `.app` do bundle those dependencies, and their
licenses travel with the binary. Every runtime dependency is permissively
licensed.

| Dependency | License |
|---|---|
| [sv-ttk](https://github.com/rdbende/Sun-Valley-ttk-theme) | MIT |
| [python-tds](https://github.com/denisenkom/pytds) | MIT |
| [pyodbc](https://github.com/mkleehammer/pyodbc) | MIT |
| [pyspnego](https://github.com/jborean93/pyspnego) | MIT |

The date pickers in the **Set Status** dialog are built directly on `tkinter`,
in `datepicker.py`, rather than using a calendar library. This is deliberate.
The widely used `tkcalendar` is licensed under GPL-3.0, which would make a
redistributed binary a combined work subject to GPL terms. Building the widget
in-house keeps the distributable permissively licensed and removes the
transitive `Babel` dependency, which is about 1.1 MB in the built `.exe`.

This summary is provided for convenience and isn't legal advice.
