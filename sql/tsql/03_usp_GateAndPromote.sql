/* =====================================================================================
   dq.usp_GateAndPromote: decide GO / HOLD for a validated file and load the clean rows.
   Same logic as src/retro_intake/validate.py (decide + promote). Not executed by CI.
   ===================================================================================== */
CREATE OR ALTER PROCEDURE dq.usp_GateAndPromote
    @FileId        varchar(10),
    @LoadBatch     varchar(30),
    @HoldRate      decimal(5,4) = 0.02,   -- hold the file if more than 2% of rows fail critical rules
    @HoldMinRows   int = 100              -- ...and the file has at least this many rows
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @Rows int, @CriticalRows int, @FileChecks int, @Decision varchar(10), @Reason nvarchar(400);

    SELECT @Rows = RowCountActual FROM dq.FileRegister WHERE FileId = @FileId;

    SELECT @CriticalRows = COUNT(DISTINCT RowNum)
    FROM dq.Exception WHERE FileId = @FileId AND Severity = 'CRITICAL' AND RowNum IS NOT NULL;

    SELECT @FileChecks = COUNT(*)
    FROM dq.Exception WHERE FileId = @FileId AND Severity = 'CRITICAL' AND RowNum IS NULL;

    SELECT @Decision = CASE
               WHEN @FileChecks > 0 THEN 'HOLD'
               WHEN @Rows >= @HoldMinRows AND @CriticalRows * 1.0 / NULLIF(@Rows, 0) > @HoldRate THEN 'HOLD'
               ELSE 'GO' END,
           @Reason = CASE
               WHEN @FileChecks > 0 THEN N'Failed file-level check (control totals or layout)'
               WHEN @Rows >= @HoldMinRows AND @CriticalRows * 1.0 / NULLIF(@Rows, 0) > @HoldRate
                    THEN CONCAT(FORMAT(100.0 * @CriticalRows / @Rows, 'N1'), N'% of rows failed critical rules')
               WHEN @CriticalRows > 0 THEN CONCAT(@CriticalRows, N' rows held back, rest loaded')
               ELSE N'Clean' END;

    BEGIN TRY
        BEGIN TRANSACTION;

        UPDATE dq.FileRegister
        SET Decision = @Decision, DecisionReason = @Reason,
            LoadBatch = CASE WHEN @Decision = 'GO' THEN @LoadBatch END
        WHERE FileId = @FileId;

        IF @Decision = 'GO'
        BEGIN
            -- the same client and month can be re-sent: replace, never double-load
            DELETE p
            FROM prod.Inforce AS p
            JOIN (SELECT DISTINCT CedantId, Period FROM stg.Inforce WHERE FileId = @FileId) AS s
              ON s.CedantId = p.CedantId AND s.Period = p.Period;

            INSERT prod.Inforce (CedantId, Period, PolicyId, LifeId, InsuredDob, Sex, Smoker, IssueDate,
                                 IssueAge, FaceAmount, RetroShare, Status, Currency, SourceFileId, LoadBatch)
            SELECT s.CedantId, s.Period, s.PolicyId, s.LifeId, s.InsuredDob, s.Sex, s.Smoker, s.IssueDate,
                   s.IssueAge, s.FaceAmount, s.RetroShare, s.Status, s.Currency, s.FileId, @LoadBatch
            FROM stg.Inforce AS s
            WHERE s.FileId = @FileId
              AND NOT EXISTS (SELECT 1 FROM dq.Exception AS e
                              WHERE e.FileId = s.FileId AND e.RowNum = s.RowNum AND e.Severity = 'CRITICAL');
        END;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;

    SELECT @FileId AS FileId, @Decision AS Decision, @Reason AS Reason, @Rows AS RowsReceived,
           @CriticalRows AS RowsHeldBack;
END;
GO
