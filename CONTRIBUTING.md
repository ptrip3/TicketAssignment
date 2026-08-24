# Contributing

Thanks for taking a look. This is a small, focused tool — a round-robin ticket
assigner for a single team — so the bar for new features is "does this help a
team rotate tickets fairly", not "is this generally useful".

By participating you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting set up

You need Python 3 **with Tk**. Both the python.org installer and Homebrew's
`python` include it; some minimal installs do not. Check with:

```bash
python3 -m tkinter
```

If a small test window opens, you are fine.

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
source .venv/bin/activate      # macOS
pip install -r requirements.txt
python src/name_selector.py
```

`requirements.txt` uses environment markers, so `pip` picks the right database
driver for your platform automatically — `pyodbc` on Windows, `python-tds` and
`pyspnego` elsewhere. Do not install the other platform's driver by hand; the
PyInstaller specs deliberately exclude it.

**Windows only:** `pyodbc` needs a system ODBC driver, which is not a pip
package. See [Requirements](README.md#requirements) for how to check what you
have.

You will also need a SQL Server instance to point at. It does not have to be
shared — a local SQL Server Express is fine for development, as long as TCP/IP
is enabled if you want to exercise the macOS code path.

## How the code is laid out

| Path | What it is |
| --- | --- |
| `src/name_selector.py` | The Tkinter UI and all in-memory state. Most of the app. |
| `src/db.py` | Every SQL Server query. Two backends behind one interface. |
| `src/models.py` | Shared types, kept separate so `db.py` need not import the UI. |
| `src/datepicker.py` | Custom Tk calendar widget. |
| `src/schema.sql` | The DDL `ensure_schema()` applies. |
| `packaging/` | PyInstaller specs and the Windows installer script. |
| `tools/` | One-off migration and maintenance scripts. |

Two things worth knowing before you change anything:

- **`db.py` supports two backends.** Every query is written once with `?`
  placeholders and tuple params, and `_CursorAdapter` translates for
  `python-tds`. If you add a query, keep that convention — do not write
  backend-specific SQL.
- **`models.py` exists to avoid a circular import.** `db.py` needs
  `StatusDuration`; `name_selector.py` imports `db`. Do not move domain types
  back into `name_selector.py`.

## Style

Match the surrounding code. This codebase comments *why*, not *what*, and it
does so more heavily than most Python projects — particularly where behaviour is
non-obvious or a naive change would break something. Several comments exist
specifically to stop a future reader "simplifying" something load-bearing (see
the `unicodedata` note in the PyInstaller specs for a representative example).
If you find yourself removing one of those, that is a sign the change needs more
thought, not less.

Otherwise: standard PEP 8, 4-space indents, and no new runtime dependencies
without discussing it first. Every added dependency has to survive being frozen
into a PyInstaller bundle on two platforms.

## Testing

There is no automated test suite. Changes are verified by running the app
against a real SQL Server.

At minimum, before opening a PR, exercise the paths your change touches:

- Connect, load an existing location, and confirm the roster renders
- Assign a ticket and confirm the rotation advances and counts increment
- Set a status with a date range, including a half day, and confirm availability
  is respected
- Make a change, then confirm a second client picks it up (the background thread
  polls every 0.5s)
- Quit and relaunch, confirming settings and data persisted

If your change touches `db.py`, test on both backends if you can. If you only
have one platform, say so in the PR — that is useful information, not a
disqualification.

If your change touches `packaging/`, build the app and run the built artifact.
A frozen bundle fails in ways running from source does not.

## Pull requests

- Branch from `master`.
- Keep PRs focused. One behavioural change per PR.
- Explain *why* in the description, not just what — see the
  [PR template](.github/PULL_REQUEST_TEMPLATE.md).
- Note which platform(s) you tested on.

## Schema changes

`ensure_schema()` applies `src/schema.sql` and is expected to be idempotent, and
`_schema_is_current()` does a cheap check to avoid replaying it on every launch.
If you add a table or column:

1. Add it to `schema.sql` in an `IF NOT EXISTS`-guarded form
2. Update the counts in `_schema_is_current()` so the check still detects a
   current schema
3. Make sure existing databases upgrade cleanly — people run this against live
   data, and there is no migration framework

## Reporting bugs

Use the [issue templates](https://github.com/ptrip3/TicketAssignment/issues/new/choose).
The platform and backend fields matter more than they might look — a good share
of issues turn out to be specific to one of the two database drivers.
