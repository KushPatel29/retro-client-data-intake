/* =====================================================================================
   dq.usp_ValidateInforce: set-based data quality checks for one staged inforce file.
   Same rules as config/rules.yaml (IF001-IF011, RF001-RF003). Not executed by CI.
   ===================================================================================== */
CREATE OR ALTER FUNCTION dq.fn_AgeLastBirthday (@Dob date, @OnDate date)
RETURNS int
WITH SCHEMABINDING
AS
BEGIN
    RETURN DATEDIFF(year, @Dob, @OnDate)
         - CASE WHEN MONTH(@OnDate) * 100 + DAY(@OnDate) < MONTH(@Dob) * 100 + DAY(@Dob) THEN 1 ELSE 0 END;
END;
GO

CREATE OR ALTER PROCEDURE dq.usp_ValidateInforce
    @FileId varchar(10)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @CedantId varchar(10), @Period char(7), @PeriodStart date, @PrevFileId varchar(10);

    SELECT TOP (1) @CedantId = CedantId, @Period = Period, @PeriodStart = PeriodStart
    FROM stg.Inforce WHERE FileId = @FileId;

    IF @CedantId IS NULL
        THROW 50001, 'No staged rows for this file.', 1;

    SELECT @PrevFileId = MAX(f.FileId)
    FROM dq.FileRegister AS f
    WHERE f.CedantId = @CedantId AND f.FileType = 'inforce' AND f.Status = 'RECEIVED'
      AND f.Period = FORMAT(DATEADD(day, -1, @PeriodStart), 'yyyy-MM');

    BEGIN TRY
        BEGIN TRANSACTION;

        -- re-runnable: clear this file's earlier results
        DELETE dq.Exception WHERE FileId = @FileId AND (RuleId LIKE 'IF%' OR RuleId LIKE 'RF%');

        INSERT dq.Exception (FileId, CedantId, Period, FileType, RuleId, Severity, RowNum, RecordKey, Detail)
        SELECT i.FileId, i.CedantId, i.Period, 'inforce', r.RuleId, r.Severity, i.RowNum,
               CONCAT(i.PolicyId, '|', i.LifeId), r.Detail
        FROM stg.Inforce AS i
        JOIN dq.Cedant AS c ON c.CedantId = i.CedantId
        CROSS APPLY (
            -- IF001 required field missing
            SELECT 'IF001', 'CRITICAL', 'Missing required field'
            WHERE i.PolicyId IS NULL OR i.LifeId IS NULL OR ISNULL(i.InsuredDobRaw, '') = ''
               OR ISNULL(i.IssueDateRaw, '') = '' OR i.FaceAmount IS NULL
            UNION ALL
            -- IF003 date cannot be read
            SELECT 'IF003', 'CRITICAL', CONCAT('Unreadable date: ', COALESCE(i.IssueDateRaw, i.InsuredDobRaw))
            WHERE (i.InsuredDob IS NULL AND ISNULL(i.InsuredDobRaw, '') <> '')
               OR (i.IssueDate IS NULL AND ISNULL(i.IssueDateRaw, '') <> '')
            UNION ALL
            -- IF004 DOB after issue date
            SELECT 'IF004', 'CRITICAL', CONCAT('DOB ', i.InsuredDob, ' after issue date ', i.IssueDate)
            WHERE i.InsuredDob > i.IssueDate OR i.InsuredDob > i.PeriodEnd
            UNION ALL
            -- IF005 issue age does not match DOB
            SELECT 'IF005', 'WARNING', CONCAT('Issue age ', i.IssueAge, ' but DOB gives ',
                                              dq.fn_AgeLastBirthday(i.InsuredDob, i.IssueDate))
            WHERE i.InsuredDob < i.IssueDate
              AND ABS(i.IssueAge - dq.fn_AgeLastBirthday(i.InsuredDob, i.IssueDate)) > 1
            UNION ALL
            -- IF006 face amount zero or negative
            SELECT 'IF006', 'CRITICAL', CONCAT('Face amount ', i.FaceAmount) WHERE i.FaceAmount <= 0
            UNION ALL
            -- IF007 above jumbo limit
            SELECT 'IF007', 'WARNING', CONCAT('Face ', FORMAT(i.FaceAmount, 'N0'), ' above jumbo limit')
            WHERE i.FaceAmount > c.JumboLimit
            UNION ALL
            -- IF008 retro share outside 0-100%
            SELECT 'IF008', 'CRITICAL', CONCAT('Retro share ', ISNULL(CONVERT(varchar(20), i.RetroShare), 'blank'))
            WHERE i.RetroShare IS NULL OR i.RetroShare <= 0 OR i.RetroShare > 1
            UNION ALL
            -- IF009 code not recognised (mapping left it NULL)
            SELECT 'IF009', 'CRITICAL', CONCAT('Unknown code: sex "', i.SexRaw, '"')
            WHERE i.Sex IS NULL OR i.Smoker IS NULL OR i.Status IS NULL
            UNION ALL
            -- IF010 issue age outside treaty limits
            SELECT 'IF010', 'WARNING', CONCAT('Issue age ', i.IssueAge, ', treaty allows ', c.MinIssueAge, '-', c.MaxIssueAge)
            WHERE i.IssueAge < c.MinIssueAge OR i.IssueAge > c.MaxIssueAge
            UNION ALL
            -- IF011 currency does not match treaty
            SELECT 'IF011', 'CRITICAL', CONCAT('Currency ', ISNULL(i.Currency, 'blank'), ', treaty is ', c.Currency)
            WHERE ISNULL(i.Currency, '') <> c.Currency
        ) AS r (RuleId, Severity, Detail)
        WHERE i.FileId = @FileId;

        -- IF002 duplicate policy and life in the file
        WITH d AS (
            SELECT FileId, CedantId, Period, RowNum, PolicyId, LifeId,
                   COUNT(*) OVER (PARTITION BY PolicyId, LifeId) AS n
            FROM stg.Inforce WHERE FileId = @FileId AND PolicyId IS NOT NULL)
        INSERT dq.Exception (FileId, CedantId, Period, FileType, RuleId, Severity, RowNum, RecordKey, Detail)
        SELECT FileId, CedantId, Period, 'inforce', 'IF002', 'CRITICAL', RowNum,
               CONCAT(PolicyId, '|', LifeId), CONCAT('Appears ', n, ' times in this file')
        FROM d WHERE n > 1;

        IF @PrevFileId IS NOT NULL
        BEGIN
            -- RF001 active last month, missing this month with no termination
            INSERT dq.Exception (FileId, CedantId, Period, FileType, RuleId, Severity, RowNum, RecordKey, Detail)
            SELECT DISTINCT @FileId, @CedantId, @Period, 'inforce', 'RF001', 'WARNING', NULL,
                   CONCAT(p.PolicyId, '|', p.LifeId), 'ACTIVE last month, missing now with no termination'
            FROM stg.Inforce AS p
            WHERE p.FileId = @PrevFileId AND p.Status = 'ACTIVE'
              AND NOT EXISTS (SELECT 1 FROM stg.Inforce AS c
                              WHERE c.FileId = @FileId AND c.PolicyId = p.PolicyId AND c.LifeId = p.LifeId);

            -- RF002 face amount changed by more than 50%
            INSERT dq.Exception (FileId, CedantId, Period, FileType, RuleId, Severity, RowNum, RecordKey, Detail)
            SELECT DISTINCT c.FileId, c.CedantId, c.Period, 'inforce', 'RF002', 'WARNING', c.RowNum,
                   CONCAT(c.PolicyId, '|', c.LifeId),
                   CONCAT('Face ', FORMAT(p.FaceAmount, 'N0'), ' last month, ', FORMAT(c.FaceAmount, 'N0'), ' now')
            FROM stg.Inforce AS c
            JOIN stg.Inforce AS p ON p.FileId = @PrevFileId AND p.PolicyId = c.PolicyId AND p.LifeId = c.LifeId
            WHERE c.FileId = @FileId AND c.FaceAmount > 0 AND p.FaceAmount > 0
              AND ABS(c.FaceAmount / p.FaceAmount - 1) > 0.5;

            -- RF003 new this month but issued before this month
            INSERT dq.Exception (FileId, CedantId, Period, FileType, RuleId, Severity, RowNum, RecordKey, Detail)
            SELECT c.FileId, c.CedantId, c.Period, 'inforce', 'RF003', 'WARNING', c.RowNum,
                   CONCAT(c.PolicyId, '|', c.LifeId), CONCAT('Not in last month''s file, issued ', c.IssueDate)
            FROM stg.Inforce AS c
            WHERE c.FileId = @FileId AND c.IssueDate < @PeriodStart
              AND NOT EXISTS (SELECT 1 FROM stg.Inforce AS p
                              WHERE p.FileId = @PrevFileId AND p.PolicyId = c.PolicyId AND p.LifeId = c.LifeId);
        END;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO
