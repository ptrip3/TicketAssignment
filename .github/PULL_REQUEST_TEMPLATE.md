# What this changes

<!-- What behaviour is different after this PR? -->

# Why

<!--
The reasoning, not just the change. If this fixes an issue, link it.
If a naive reader might "simplify" your change back out later, say so here
and consider a code comment too -- this codebase leans on those.
-->

Fixes #

# How it was tested

<!--
There is no automated suite, so say what you actually ran. Be specific about
platform and backend: a fair share of bugs live in only one of the two
database code paths.
-->

Tested on:

- [ ] Windows (`pyodbc`)
- [ ] macOS (`python-tds`)
- [ ] Ran from source
- [ ] Ran as a built PyInstaller bundle

Exercised:

- [ ] Connect and load an existing location
- [ ] Assign a ticket — rotation advances, counts increment
- [ ] Statuses with date ranges, including a half day
- [ ] A second client picked up the change (0.5s background poll)
- [ ] Settings and data survived a restart

# Checklist

- [ ] Queries use `?` placeholders with tuple params, so both backends work
- [ ] No new runtime dependency (or it is justified above, and survives being
      frozen by PyInstaller on both platforms)
- [ ] Schema changes are idempotent, and `_schema_is_current()` was updated
- [ ] Existing databases upgrade cleanly — there is no migration framework, and
      people run this against live data
- [ ] `packaging/` changes were verified by building and running the artifact
