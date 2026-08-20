-- One-time helper: merge two locations into one.
--
-- Not a feature of the app itself -- this is meant to be run directly
-- against the database (SSMS, Azure Data Studio, or `sqlcmd`) for a
-- one-off merge, e.g. when two offices/teams consolidate. Deliberately a
-- script rather than a UI feature, since it's a rare administrative
-- operation rather than day-to-day use.
--
-- BACK UP THE DATABASE FIRST. This changes real data; the whole thing is
-- wrapped in a transaction so it either fully commits or fully rolls
-- back, but that's not a substitute for a real backup:
--
--     BACKUP DATABASE [TicketAssignment] TO DISK = 'C:\Backups\TicketAssignment_pre_merge.bak';
--
-- Usage: fill in @KeepLocation / @MergeLocation below, then run the whole
-- script. @KeepLocation is the name that survives; every person, count,
-- schedule, and status from @MergeLocation moves onto it, and
-- @MergeLocation itself is deleted once it's empty.
--
-- Assumes no person name exists in both locations already (confirmed
-- before writing this) -- the pre-flight check below verifies that for
-- real before anything is changed. If it finds an overlap, the script
-- stops there; resolve that person's duplicate entry by hand (decide
-- which location's count/schedule/status should win, or sum the counts)
-- before re-running.

DECLARE @KeepLocation  NVARCHAR(100) = 'REPLACE_ME_LOCATION_TO_KEEP';
DECLARE @MergeLocation NVARCHAR(100) = 'REPLACE_ME_LOCATION_TO_MERGE_IN';

DECLARE @KeepId  INT = (SELECT LocationId FROM dbo.Locations WHERE LocationName = @KeepLocation);
DECLARE @MergeId INT = (SELECT LocationId FROM dbo.Locations WHERE LocationName = @MergeLocation);

IF @KeepId IS NULL OR @MergeId IS NULL
BEGIN
    RAISERROR('One or both location names not found -- check spelling/case against dbo.Locations.', 16, 1);
    RETURN;
END

-- Pre-flight: refuse to run if the same person exists in both locations.
-- The unique constraint on (LocationId, PersonName) would make the merge
-- below fail anyway, but this gives a clear list of who to resolve
-- instead of a raw constraint-violation error mid-transaction.
IF EXISTS (
    SELECT 1
    FROM dbo.Names A
    JOIN dbo.Names B ON A.PersonName = B.PersonName
    WHERE A.LocationId = @KeepId AND B.LocationId = @MergeId
)
BEGIN
    SELECT A.PersonName AS DuplicateName
    FROM dbo.Names A
    JOIN dbo.Names B ON A.PersonName = B.PersonName
    WHERE A.LocationId = @KeepId AND B.LocationId = @MergeId;

    RAISERROR('Found people in both locations (listed above) -- resolve these manually before merging.', 16, 1);
    RETURN;
END

BEGIN TRANSACTION;

BEGIN TRY
    -- Move everyone from the merged-away location onto the kept one.
    -- DailyCounts/Schedules/StatusEntries all key off NameId, not
    -- LocationId directly, so they come along automatically -- nothing
    -- else to touch for those.
    UPDATE dbo.Names
    SET LocationId = @KeepId
    WHERE LocationId = @MergeId;

    -- QueuePosition values from both locations started at 0, so after the
    -- move there are duplicates/gaps. Renumber the merged roster
    -- alphabetically (0, 1, 2, ...) -- matches how the app itself now
    -- keeps the rotation queue sorted (see _normalize_rotation_order in
    -- name_selector.py), so whoever the app already treats as "current"
    -- among the kept location's own names stays at position 0 and
    -- everyone else, including the newly merged-in names, falls in behind
    -- them in alphabetical order.
    ;WITH Renumbered AS (
        SELECT
            NameId,
            ROW_NUMBER() OVER (ORDER BY PersonName) - 1 AS NewPosition
        FROM dbo.Names
        WHERE LocationId = @KeepId
    )
    UPDATE N
    SET N.QueuePosition = R.NewPosition
    FROM dbo.Names N
    JOIN Renumbered R ON N.NameId = R.NameId;

    -- The merged-away location is now empty; remove it.
    DELETE FROM dbo.Locations WHERE LocationId = @MergeId;

    COMMIT TRANSACTION;
    PRINT 'Merge complete.';
END TRY
BEGIN CATCH
    ROLLBACK TRANSACTION;
    THROW;
END CATCH

-- Verify: everyone who used to be in @MergeLocation should now show up
-- under @KeepLocation, in alphabetical QueuePosition order.
SELECT PersonName, QueuePosition, TotalCount
FROM dbo.Names
WHERE LocationId = @KeepId
ORDER BY QueuePosition;
