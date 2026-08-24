# Security Policy

## Supported Versions

This is a small internal-tooling project with a single active line. Only the
latest release on `master` receives fixes.

| Version | Supported          |
| ------- | ------------------ |
| 2.0.x   | :white_check_mark: |
| < 2.0   | :x:                |

The 1.x line stored data in local JSON files rather than SQL Server and is no
longer maintained. See [Migrating from the older JSON-file
version](README.md#migrating-from-the-older-json-file-version) if you are still
on it.

## Reporting a Vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's [private vulnerability reporting][report] instead. It is visible
only to the maintainers, and lets us discuss and patch before anything is
public.

[report]: https://github.com/ptrip3/TicketAssignment/security/advisories/new

Useful things to include:

- Which version, and which platform (Windows/pyodbc or macOS/python-tds — the
  two backends have quite different code paths)
- What an attacker would need in order to exploit it: network access to the SQL
  Server, a local account on a client machine, write access to a shared install
  directory, and so on
- A reproduction if you have one

Expect an initial response within a couple of weeks. This is maintained by one
person alongside other work, so please be patient.

## Known Security Characteristics

These are deliberate design properties rather than bugs, but they matter when
deciding where to deploy this app. Reporting them is not necessary — they are
already documented here and in the README's [Known
limitations](README.md#known-limitations).

### Connection credentials are stored in plaintext

`config.ini` holds the SQL Server connection details, including a password when
one is used. It is stored per-user (`%APPDATA%\Ticket Assignment` on Windows,
`~/Library/Application Support/Ticket Assignment` on macOS) with no encryption
beyond the filesystem permissions of that profile directory.

On Windows, using Windows Authentication avoids storing a password at all, and
is the recommended configuration. On macOS both available modes (SQL Server
login and NTLM domain login) require a password.

### The app talks directly to SQL Server

There is no application server in between. Every client opens its own TDS
connection, so each user needs database rights of their own, and the database's
own permissions are the entire authorization model. The app does not, and
cannot, enforce access control beyond what SQL Server grants that login.

This means a user with database rights can modify any location's data by any
means available to that login, not just through this app.

### Released binaries are unsigned

The Windows and macOS builds produced by the PyInstaller specs in `packaging/`
are not code-signed or notarized. Verify you obtained a build from a source you
trust; the macOS build additionally requires a Gatekeeper workaround documented
in the README.

### SQL is parameterized, but the database name is not

All queries in `src/db.py` use `?`-style placeholders with tuple parameters. The
one exception is `CREATE DATABASE`, whose name cannot be parameterized in T-SQL;
it is instead validated against `^[A-Za-z_][A-Za-z0-9_]*$` before use. That value
comes from the local `config.ini`, not from other users' input.
