/* =====================================================================================
   dq.usp_RegisterFile: called by the ADF template (adf/pl_client_intake.json) for each file.
   Registers the file, rejects a duplicate by hash, and stamps the due date. Not executed by CI.
   ===================================================================================== */
CREATE OR ALTER PROCEDURE dq.usp_RegisterFile
    @FileName    nvarchar(260),
    @ContentHash varchar(64),
    @Bytes       bigint,
    @ReceivedAt  datetime2(0),
    @DueDay      tinyint = 10
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @FileId varchar(10) = CONCAT('F', FORMAT(NEXT VALUE FOR dq.FileIdSeq, '000000'));
    DECLARE @Status varchar(20) = 'RECEIVED', @DuplicateOf varchar(10);

    SELECT TOP (1) @DuplicateOf = FileId FROM dq.FileRegister
    WHERE Sha256 = @ContentHash AND Status = 'RECEIVED';
    IF @DuplicateOf IS NOT NULL SET @Status = 'REJECTED_DUPLICATE';

    -- client, type and period come from the agreed file-name pattern, e.g. NBL_inforce_202609.csv
    DECLARE @Period char(7) = STUFF(SUBSTRING(@FileName, PATINDEX('%[_][0-9][0-9][0-9][0-9][0-9][0-9]%', @FileName) + 1, 6), 5, 0, '-');
    DECLARE @DueDate date = DATEADD(day, @DueDay - 1, DATEADD(month, 1, CAST(@Period + '-01' AS date)));

    INSERT dq.FileRegister (FileId, FileName, Sha256, ReceivedAt, DueDate, Period, Status)
    VALUES (@FileId, @FileName, @ContentHash, @ReceivedAt, @DueDate, @Period, @Status);

    SELECT @FileId AS fileId, @Status AS status, @DuplicateOf AS duplicateOf,
           CASE WHEN CAST(@ReceivedAt AS date) > @DueDate THEN DATEDIFF(day, @DueDate, @ReceivedAt) ELSE 0 END AS daysLate;
END;
GO
