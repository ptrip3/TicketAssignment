# Security Policy

## Supported Versions

This project has a single active release line. Only the latest release on
`master` receives fixes.

| Version | Supported          |
| ------- | ------------------ |
| 2.0.x   | :white_check_mark: |
| < 2.0   | :x:                |

The 1.x line stored data in local JSON files rather than SQL Server and is no
longer maintained. If you're still using it, see [Migrate from the JSON-file
version](README.md#migrate-from-the-json-file-version).

## Reporting a Vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's [private vulnerability reporting][report] instead. Reports are
visible only to the maintainers, which allows a fix to be prepared before any
details are public.

[report]: https://github.com/ptrip3/TicketAssignment/security/advisories/new

Include the following where you can:

- The version, and the platform and backend. The `pyodbc` and `python-tds` code
  paths differ significantly.
- What an attacker would need to exploit it, such as network access to the SQL
  Server, a local account on a client machine, or write access to a shared
  install directory.
- Reproduction steps, if you have them.

Expect an initial response within two weeks. This project is maintained by one
person alongside other work.

## Known Security Characteristics

The following are deliberate design decisions rather than defects, but they
affect where this app is appropriate to deploy. You don't need to report them.
They're also listed in [Known limitations](README.md#known-limitations).

### Connection credentials are stored in plaintext

`config.ini` holds the SQL Server connection details, including a password when
one is used. The file is stored per-user, in `%APPDATA%\Ticket Assignment` on
Windows and `~/Library/Application Support/Ticket Assignment` on macOS. It isn't
encrypted. The only protection is the file system permissions on that profile
directory.

On Windows, Windows Authentication avoids storing a password and is the
recommended configuration. On macOS, both authentication modes require one.

### The app talks directly to SQL Server

There's no application server between the client and the database. Every client
opens its own TDS connection, so each user needs their own database rights, and
SQL Server permissions are the entire authorization model. The app can't enforce
access control beyond what SQL Server grants that login.

A user with database rights can therefore modify any location's data through any
client, not only through this app.

### Released binaries are unsigned

The Windows and macOS builds produced by the PyInstaller specs in `packaging/`
aren't code-signed or notarized. Confirm that any build you run came from a
source you trust. The macOS build also requires the Gatekeeper workaround
described in the README.

### SQL is parameterized, but the database name is not

All queries in `src/db.py` use `?` placeholders with tuple parameters. The one
exception is `CREATE DATABASE`, because T-SQL can't parameterize a database
name. That value is validated against `^[A-Za-z_][A-Za-z0-9_]*$` before use, and
it comes from the local `config.ini` rather than from another user's input.
