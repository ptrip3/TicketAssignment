# Contributing

This is a focused tool for round-robin ticket assignment across a team. The
test for a new feature is whether it helps a team rotate tickets fairly, not
whether it's generally useful.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Set up a development environment

You need Python 3 with Tk. To confirm Tk is present, run:

```bash
python3 -m tkinter
```

A small test window opens if it is. The python.org installer and Homebrew's
`python` both include Tk, but some minimal installations don't.

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
source .venv/bin/activate      # macOS
pip install -r requirements.txt
python src/name_selector.py
```

`requirements.txt` uses environment markers, so `pip` installs the correct
database driver for your platform: `pyodbc` on Windows, `python-tds` and
`pyspnego` elsewhere.

> [!IMPORTANT]
> Don't install the other platform's driver manually. The PyInstaller specs
> exclude it, so a manually installed driver works in development and then
> fails in the built app.

On Windows, `pyodbc` also needs a system ODBC driver, which isn't a pip
package. See [Requirements](README.md#requirements).

You also need a SQL Server instance. It doesn't have to be shared. A local SQL
Server Express instance works, as long as you enable TCP/IP if you want to test
the macOS code path.

## How the code is organized

| Path | Contents |
| --- | --- |
| `src/name_selector.py` | The Tkinter UI and all in-memory state. Most of the app. |
| `src/db.py` | Every SQL Server query. Two backends behind one interface. |
| `src/models.py` | Shared domain types, kept separate from the UI. |
| `src/datepicker.py` | Custom Tk calendar widget. |
| `src/schema.sql` | The DDL that `ensure_schema()` applies. |
| `packaging/` | PyInstaller specs, icons, and the Windows installer. |
| `tools/` | One-off migration and maintenance scripts. |

Two constraints to be aware of before changing anything:

**`db.py` supports two backends.** Each query is written once with `?`
placeholders and tuple parameters, and `_CursorAdapter` translates them for
`python-tds`. If you add a query, follow that pattern. Don't write
backend-specific SQL.

**`models.py` exists to prevent a circular import.** `db.py` needs
`StatusDuration`, and `name_selector.py` imports `db`. Don't move domain types
back into `name_selector.py`.

## Style

Match the surrounding code. This codebase explains why, not what, and does so
more heavily than most Python projects, especially where the behavior is
non-obvious or where a naive change would break something. Several comments
exist specifically to prevent a future reader from simplifying away something
load-bearing. For an example, see the `unicodedata` note in the PyInstaller
specs. If you're removing a comment like that, the change needs more scrutiny,
not less.

Otherwise, follow PEP 8 with 4-space indents.

Discuss new runtime dependencies before adding one. Every dependency has to
work when frozen into a PyInstaller bundle on both platforms.

## Testing

There's no automated test suite. Changes are verified by running the app
against a real SQL Server instance.

Before opening a pull request, exercise the paths your change affects:

1. Connect, load an existing location, and confirm the roster displays.
2. Assign a ticket. Confirm the rotation advances and the counts increase.
3. Set a status with a date range, including a half day, and confirm the
   rotation respects it.
4. Make a change and confirm a second client picks it up. The background thread
   polls every 0.5 seconds.
5. Quit and restart the app. Confirm settings and data persisted.

If your change affects `db.py`, test both backends if you can. If you have only
one platform, say so in the pull request. That's useful information, not a
problem.

If your change affects `packaging/`, build the app and run the result. A frozen
bundle fails in ways that running from source doesn't.

## Pull requests

- Branch from `master`.
- Keep each pull request to one behavioral change.
- Explain why the change is needed, not only what it does. See the
  [pull request template](.github/PULL_REQUEST_TEMPLATE.md).
- State which platforms you tested on.

## Schema changes

`ensure_schema()` applies `src/schema.sql` and must remain idempotent.
`_schema_is_current()` does a single-query check so the DDL isn't replayed on
every launch.

To add a table or column:

1. Add it to `schema.sql`, guarded so it's only created if it doesn't exist.
2. Update the counts in `_schema_is_current()` so the check still detects a
   current schema.
3. Confirm an existing database upgrades cleanly. There's no migration
   framework, and this app runs against live data.

## Reporting bugs

Use the [issue templates](https://github.com/ptrip3/TicketAssignment/issues/new/choose).
The platform and backend fields matter more than they might appear. Many issues
turn out to affect only one of the two database drivers.

For security issues, don't open a public issue. See [SECURITY.md](SECURITY.md).
