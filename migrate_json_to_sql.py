"""One-time importer: existing ticket_assignment_data.json -> SQL Server.

Run this once when cutting a team over from the old JSON-file storage to
the new SQL Server backend, so their current rosters, stats, schedules,
and statuses aren't lost.

Usage:
    python migrate_json_to_sql.py --json-file "\\\\shared\\drive\\ticket_assignment_data.json"

By default this reads DB connection info from config.ini (the same file
name_selector.py uses) next to this script. Override any of it on the
command line if you're migrating into a database config.ini doesn't point
at yet:

    python migrate_json_to_sql.py --json-file data.json ^
        --server SQLBOX\\PROD --database TicketAssignment

Safe to re-run: each location is imported with a full delete-and-reinsert
(see db.py's save_location), so re-running against the same JSON file just
re-applies the same state. It is NOT safe to run against a database that
already has live edits you want to keep for a location of the same name --
those will be overwritten. Use --dry-run first if you're not sure.
"""

import argparse
import configparser
import json
import os
import sys
from datetime import date

from db import Database, DatabaseError, STATUS_TYPES
from models import StatusDuration


def load_json_locations(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    locations = {}
    for location_name, loc in data.get("locations", {}).items():
        location_data = {
            "names": list(loc.get("names", [])),
            "name_counts": dict(loc.get("counts", {})),
            "daily_counts": dict(loc.get("daily_counts", {})),
            "schedules": dict(loc.get("schedules", {})),
        }
        for status_type in STATUS_TYPES:
            key = f"{status_type}_status"
            status_dict = {}
            for person_name, duration in loc.get(key, {}).items():
                start = date.fromisoformat(duration["start"])
                end = date.fromisoformat(duration["end"]) if duration.get("end") else None
                status_dict[person_name] = StatusDuration(start, end)
            location_data[key] = status_dict
        locations[location_name] = location_data
    return locations


def build_database_from_args(args):
    if args.server and args.database:
        return Database(
            driver=args.driver or "ODBC Driver 17 for SQL Server",
            server=args.server,
            database=args.database,
            trusted_connection=not (args.uid and args.pwd),
            uid=args.uid,
            pwd=args.pwd,
        )

    config_path = args.config or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    if not os.path.exists(config_path):
        print(f"No config.ini found at {config_path} and no --server/--database given.", file=sys.stderr)
        sys.exit(2)
    config = configparser.ConfigParser()
    config.read(config_path)
    try:
        return Database.from_config(config)
    except DatabaseError as e:
        print(f"Could not read database config from {config_path}: {e}", file=sys.stderr)
        sys.exit(2)


def summarize(locations):
    lines = []
    for name, loc in sorted(locations.items()):
        n_names = len(loc["names"])
        n_statuses = sum(len(loc[f"{t}_status"]) for t in STATUS_TYPES)
        lines.append(f"  {name}: {n_names} name(s), {n_statuses} active/upcoming status entrie(s)")
    return "\n".join(lines) if lines else "  (no locations found in JSON file)"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json-file", required=True, help="Path to the existing ticket_assignment_data.json")
    parser.add_argument("--config", help="Path to config.ini to read [Database] settings from (default: ./config.ini)")
    parser.add_argument("--driver", help="ODBC driver name override")
    parser.add_argument("--server", help="SQL Server host[\\instance] override")
    parser.add_argument("--database", help="Database name override")
    parser.add_argument("--uid", help="SQL login username (implies SQL auth, not Windows auth)")
    parser.add_argument("--pwd", help="SQL login password (implies SQL auth, not Windows auth)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and summarize only; don't write to the database")
    parser.add_argument("--yes", action="store_true", help="Don't prompt for confirmation before writing")
    args = parser.parse_args()

    if not os.path.exists(args.json_file):
        print(f"JSON file not found: {args.json_file}", file=sys.stderr)
        sys.exit(1)

    locations = load_json_locations(args.json_file)
    print(f"Parsed {len(locations)} location(s) from {args.json_file}:")
    print(summarize(locations))

    if args.dry_run:
        print("\n--dry-run: not writing anything.")
        return

    db = build_database_from_args(args)
    print(f"\nTarget: {db.server} / database {db.database!r}"
          f" ({'Windows Auth' if db.trusted_connection else 'SQL login ' + str(db.uid)})")

    if not args.yes:
        answer = input("Proceed with import? This overwrites any existing data for these location names. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return

    try:
        db.test_connection()
    except DatabaseError as e:
        print(f"Could not connect: {e}", file=sys.stderr)
        sys.exit(1)

    if not db.database_exists():
        print(f"Database {db.database!r} does not exist on {db.server} -- creating it.")
        db.create_database()

    print("Ensuring schema (creating tables if missing)...")
    db.ensure_schema()

    for location_name, location_data in locations.items():
        print(f"Importing {location_name!r} ({len(location_data['names'])} name(s))...")
        db.save_location(location_name, location_data)

    print("\nDone.")


if __name__ == "__main__":
    main()
