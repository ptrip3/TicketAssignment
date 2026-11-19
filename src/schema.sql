-- Ticket Assignment - SQL Server schema
--
-- Targets SQL Server 2017+ (tested against 14.0.1000.169). Run this against
-- an empty database (create the database first, e.g. `CREATE DATABASE
-- TicketAssignment;`), or point it at an existing one -- every statement
-- below is idempotent, so re-running this script is safe.
--
-- Design notes:
--   * One row per (location, person) in dbo.Names; QueuePosition encodes
--     the round-robin order that used to be the JSON file's "names" deque
--     order. Position 0 is "next up" / equivalent to the old "current_name".
--   * DailyCounts and Schedules are keyed by NameId + DayOfWeek rather than
--     nested JSON, so a person's schedule/day-count entries cascade-delete
--     automatically when the person is removed.
--   * StatusEntries covers all five status types (out_of_office, vacation,
--     training, sick_leave, half_day) in one table with a StatusType
--     column. The unique constraint enforces one entry per type per
--     person. HalfDay is a flag on that
--     same row (only meaningful for vacation/sick_leave rows) rather than
--     its own status type -- the standalone "half_day" StatusType value is
--     only still written by old data and is no longer created by the app.

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Locations' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.Locations (
        LocationId    INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        LocationName  NVARCHAR(100)     NOT NULL,
        CONSTRAINT UQ_Locations_Name UNIQUE (LocationName)
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Names' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.Names (
        NameId         INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        LocationId     INT                NOT NULL,
        PersonName     NVARCHAR(200)      NOT NULL,
        QueuePosition  INT                NOT NULL,
        TotalCount     INT                NOT NULL DEFAULT 0,
        CONSTRAINT FK_Names_Locations FOREIGN KEY (LocationId)
            REFERENCES dbo.Locations(LocationId) ON DELETE CASCADE,
        CONSTRAINT UQ_Names_Location_Person UNIQUE (LocationId, PersonName)
    );
    CREATE INDEX IX_Names_Location_Position ON dbo.Names(LocationId, QueuePosition);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'DailyCounts' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.DailyCounts (
        NameId     INT           NOT NULL,
        DayOfWeek  NVARCHAR(10)  NOT NULL,
        DayCount   INT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_DailyCounts PRIMARY KEY (NameId, DayOfWeek),
        CONSTRAINT FK_DailyCounts_Names FOREIGN KEY (NameId)
            REFERENCES dbo.Names(NameId) ON DELETE CASCADE,
        CONSTRAINT CK_DailyCounts_Day CHECK (DayOfWeek IN
            ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'))
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Schedules' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.Schedules (
        ScheduleId  INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        NameId      INT               NOT NULL,
        DayOfWeek   NVARCHAR(10)      NOT NULL,
        TimeRange   NVARCHAR(20)      NOT NULL,  -- "HH:MM-HH:MM"
        CONSTRAINT FK_Schedules_Names FOREIGN KEY (NameId)
            REFERENCES dbo.Names(NameId) ON DELETE CASCADE,
        CONSTRAINT CK_Schedules_Day CHECK (DayOfWeek IN
            ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'))
    );
    CREATE INDEX IX_Schedules_Name_Day ON dbo.Schedules(NameId, DayOfWeek);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'StatusEntries' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.StatusEntries (
        StatusId    INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        NameId      INT               NOT NULL,
        StatusType  NVARCHAR(20)      NOT NULL,
        StartDate   DATE              NOT NULL,
        EndDate     DATE              NULL,
        HalfDay       BIT             NOT NULL DEFAULT 0,
        HalfDayPeriod NVARCHAR(10)    NULL,  -- 'morning' or 'afternoon'; only meaningful when HalfDay = 1
        CONSTRAINT FK_StatusEntries_Names FOREIGN KEY (NameId)
            REFERENCES dbo.Names(NameId) ON DELETE CASCADE,
        CONSTRAINT CK_StatusEntries_Type CHECK (StatusType IN
            ('out_of_office','vacation','training','sick_leave','half_day')),
        CONSTRAINT UQ_StatusEntries_Name_Type UNIQUE (NameId, StatusType)
    );
END
GO

-- HalfDay was added after some databases were already created from the
-- CREATE TABLE above; add it on existing installs too (idempotent, like
-- everything else in this file). Only vacation/sick_leave rows ever set
-- this from the UI, but it's a plain column on every status type.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.StatusEntries') AND name = 'HalfDay'
)
BEGIN
    ALTER TABLE dbo.StatusEntries ADD HalfDay BIT NOT NULL DEFAULT 0;
END
GO

-- Same for HalfDayPeriod (added after HalfDay itself, for the
-- morning/afternoon choice).
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.StatusEntries') AND name = 'HalfDayPeriod'
)
BEGIN
    ALTER TABLE dbo.StatusEntries ADD HalfDayPeriod NVARCHAR(10) NULL;
END
GO
GO

-- Append-only log of individual ticket assignments, for answering "who
-- assigned what to whom". Added after the tables above, so it is created
-- on existing installs too.
--
-- Deliberately has NO foreign keys, unlike every other table here.
-- save_location() deletes and reinserts a location's Names on every save,
-- so NameId is regenerated constantly -- a reference to it would be
-- cascade-deleted almost immediately. Names and locations are stored as
-- text so the history also outlives a renamed, merged or deleted
-- location, which is the whole point of an audit log.
--
-- AssignedBy and HostName default to the server's own view of the
-- connection rather than anything the client passes in. With Windows
-- Integrated Auth (Windows) or NTLM (macOS) that is the caller's domain
-- account, so it cannot be spoofed by the app and needs no extra config.
-- Note this records whichever account the app is RUNNING AS, which is the
-- elevated account if it was launched with one.
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Assignments' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.Assignments (
        AssignmentId  BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        AssignedAtUtc DATETIME2(0)  NOT NULL CONSTRAINT DF_Assignments_AtUtc DEFAULT SYSUTCDATETIME(),
        LocationName  NVARCHAR(100) NOT NULL,
        PersonName    NVARCHAR(200) NOT NULL,
        AssignedBy    NVARCHAR(256) NOT NULL CONSTRAINT DF_Assignments_By   DEFAULT SUSER_SNAME(),
        HostName      NVARCHAR(128) NULL     CONSTRAINT DF_Assignments_Host DEFAULT HOST_NAME()
    );

    -- "what did this person get, and from whom" is the main query;
    -- the second index covers plain date-range reporting.
    CREATE INDEX IX_Assignments_Person ON dbo.Assignments (PersonName, AssignedAtUtc);
    CREATE INDEX IX_Assignments_AtUtc  ON dbo.Assignments (AssignedAtUtc);
END
