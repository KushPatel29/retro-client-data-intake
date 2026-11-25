/* =====================================================================================
   SQL Server version of the staging, exception and production tables.
   Written for SQL Server 2019+ / Azure SQL. The repo's automated tests run the same logic
   in DuckDB (config/rules.yaml); these scripts are the T-SQL equivalent for a SQL Server shop
   and are not executed by CI.
   ===================================================================================== */
IF SCHEMA_ID('stg') IS NULL EXEC ('CREATE SCHEMA stg');
IF SCHEMA_ID('dq') IS NULL EXEC ('CREATE SCHEMA dq');
IF SCHEMA_ID('prod') IS NULL EXEC ('CREATE SCHEMA prod');
GO
CREATE SEQUENCE dq.FileIdSeq AS int START WITH 1;
GO

CREATE TABLE dq.Cedant (
    CedantId        varchar(10)   NOT NULL PRIMARY KEY,
    CedantName      nvarchar(100) NOT NULL,
    TreatyId        varchar(30)   NOT NULL,
    Currency        char(3)       NOT NULL,
    MinIssueAge     tinyint       NOT NULL,
    MaxIssueAge     tinyint       NOT NULL,
    JumboLimit      decimal(18,2) NOT NULL
);

CREATE TABLE dq.FileRegister (
    FileId          varchar(10)   NOT NULL PRIMARY KEY,
    FileName        nvarchar(260) NOT NULL,
    CedantId        varchar(10)   NULL REFERENCES dq.Cedant (CedantId),
    FileType        varchar(10)   NULL,
    Period          char(7)       NULL,          -- 'YYYY-MM'
    Sha256          char(64)      NOT NULL,
    ReceivedAt      datetime2(0)  NOT NULL,
    DueDate         date          NULL,
    RowCountActual  int           NULL,
    ControlCount    int           NULL,
    AmountTotal     decimal(18,2) NULL,
    ControlTotal    decimal(18,2) NULL,
    Status          varchar(20)   NOT NULL,      -- RECEIVED / REJECTED_DUPLICATE / UNRECOGNIZED
    Decision        varchar(10)   NULL,          -- GO / HOLD / REJECTED
    DecisionReason  nvarchar(400) NULL,
    LoadBatch       varchar(30)   NULL
);
CREATE UNIQUE INDEX UX_FileRegister_Received ON dq.FileRegister (Sha256) WHERE Status = 'RECEIVED';

CREATE TABLE stg.Inforce (
    FileId          varchar(10)   NOT NULL,
    RowNum          int           NOT NULL,
    CedantId        varchar(10)   NOT NULL,
    Period          char(7)       NOT NULL,
    PeriodStart     date          NOT NULL,
    PeriodEnd       date          NOT NULL,
    PolicyId        varchar(20)   NULL,
    LifeId          tinyint       NULL,
    InsuredDob      date          NULL,
    InsuredDobRaw   varchar(20)   NULL,
    Sex             char(1)       NULL,
    SexRaw          varchar(20)   NULL,
    Smoker          char(1)       NULL,
    IssueDate       date          NULL,
    IssueDateRaw    varchar(20)   NULL,
    IssueAge        smallint      NULL,
    FaceAmount      decimal(18,2) NULL,
    RetroShare      decimal(9,6)  NULL,
    Status          varchar(10)   NULL,
    Currency        char(3)       NULL,
    CONSTRAINT PK_stg_Inforce PRIMARY KEY (FileId, RowNum)
);
CREATE INDEX IX_stg_Inforce_Policy ON stg.Inforce (CedantId, Period, PolicyId, LifeId) INCLUDE (Status, FaceAmount);

CREATE TABLE dq.Exception (
    ExceptionId     bigint IDENTITY PRIMARY KEY,
    FileId          varchar(10)   NULL,
    CedantId        varchar(10)   NOT NULL,
    Period          char(7)       NOT NULL,
    FileType        varchar(10)   NOT NULL,
    RuleId          varchar(10)   NOT NULL,
    Severity        varchar(10)   NOT NULL,      -- CRITICAL / WARNING
    RowNum          int           NULL,
    RecordKey       varchar(40)   NULL,
    Detail          nvarchar(400) NULL,
    LoggedAt        datetime2(0)  NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX IX_Exception_FileRow ON dq.Exception (FileId, RowNum) INCLUDE (Severity);

CREATE TABLE prod.Inforce (
    CedantId        varchar(10)   NOT NULL,
    Period          char(7)       NOT NULL,
    PolicyId        varchar(20)   NOT NULL,
    LifeId          tinyint       NOT NULL,
    InsuredDob      date          NOT NULL,
    Sex             char(1)       NOT NULL,
    Smoker          char(1)       NOT NULL,
    IssueDate       date          NOT NULL,
    IssueAge        smallint      NULL,
    FaceAmount      decimal(18,2) NOT NULL,
    RetroShare      decimal(9,6)  NOT NULL,
    Status          varchar(10)   NOT NULL,
    Currency        char(3)       NOT NULL,
    SourceFileId    varchar(10)   NOT NULL,
    LoadBatch       varchar(30)   NOT NULL,
    CONSTRAINT PK_prod_Inforce PRIMARY KEY (CedantId, Period, PolicyId, LifeId)
);
GO
