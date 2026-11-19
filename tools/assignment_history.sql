-- Reporting queries for the assignment audit log (dbo.Assignments).
--
-- The log records one row per ticket assignment: when, which location,
-- who it was assigned TO, and which account assigned it. AssignedBy is
-- filled in by SQL Server from the connection, so it is whoever the app
-- was running as -- the elevated account, if it was launched with one.
--
-- Times are stored UTC. The queries below convert to local time for
-- display; drop AT TIME ZONE if you would rather read raw UTC.
--
-- Nothing here modifies data. Run the section you need.

-- ---------------------------------------------------------------------
-- 1. Who assigned tickets to a specific person, most recent first.
--    Set @Person before running.
-- ---------------------------------------------------------------------
DECLARE @Person NVARCHAR(200) = N'REPLACE WITH PERSON NAME';

SELECT
    AssignedAtUtc AT TIME ZONE 'UTC' AT TIME ZONE 'Eastern Standard Time' AS AssignedAtLocal,
    AssignedBy,
    LocationName,
    HostName
FROM dbo.Assignments
WHERE PersonName = @Person
ORDER BY AssignedAtUtc DESC;


-- ---------------------------------------------------------------------
-- 2. Same question, summarised: how many each assigner gave that person.
-- ---------------------------------------------------------------------
SELECT
    AssignedBy,
    COUNT(*)          AS Assignments,
    MIN(AssignedAtUtc) AS FirstUtc,
    MAX(AssignedAtUtc) AS LastUtc
FROM dbo.Assignments
WHERE PersonName = @Person
GROUP BY AssignedBy
ORDER BY Assignments DESC;


-- ---------------------------------------------------------------------
-- 3. Full who-assigned-to-whom matrix for a date range.
-- ---------------------------------------------------------------------
SELECT
    AssignedBy,
    PersonName,
    COUNT(*) AS Assignments
FROM dbo.Assignments
WHERE AssignedAtUtc >= DATEADD(DAY, -30, SYSUTCDATETIME())
GROUP BY AssignedBy, PersonName
ORDER BY AssignedBy, Assignments DESC;


-- ---------------------------------------------------------------------
-- 4. Everything one account did, newest first -- the "what did this tech
--    do today" view.
-- ---------------------------------------------------------------------
DECLARE @Account NVARCHAR(256) = N'DOMAIN\REPLACE-WITH-ACCOUNT';

SELECT TOP (200)
    AssignedAtUtc AT TIME ZONE 'UTC' AT TIME ZONE 'Eastern Standard Time' AS AssignedAtLocal,
    PersonName,
    LocationName
FROM dbo.Assignments
WHERE AssignedBy = @Account
ORDER BY AssignedAtUtc DESC;


-- ---------------------------------------------------------------------
-- 5. Retention. The log grows without bound, unlike every other table in
--    this database. Decide a cutoff and run this on a schedule, or skip
--    it if the volume stays small (one row per assignment).
-- ---------------------------------------------------------------------
-- DELETE FROM dbo.Assignments
-- WHERE AssignedAtUtc < DATEADD(YEAR, -1, SYSUTCDATETIME());
