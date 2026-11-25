<#
.SYNOPSIS
    Registers client files arriving on the inbound share, flags duplicates and late files, and archives them.

.DESCRIPTION
    PowerShell version of the intake step (src/retro_intake/intake.py does the same in Python).
    For every data file in -Inbox it:
      1. Works out the client, file type and period from the file name.
      2. Computes a SHA-256 hash, so a file sent twice is caught even if it was renamed.
      3. Compares the arrival time with the due date (day -DueDay of the following month).
      4. Copies the file, and its .ctl control file if there is one, to
         <Archive>\<client>\<period>\<yyyyMMddTHHmm>_<name>.
      5. Appends a row per file to the register CSV.
    Supports -WhatIf, so you can see what it would do without copying anything.

.EXAMPLE
    ./scripts/Invoke-FileIntake.ps1 -Inbox ./data/inbox -Archive ./data/archive_ps -Register ./output/file_register_ps.csv

.NOTES
    Synthetic data and fictional clients only. Runs on Windows PowerShell 5.1 and PowerShell 7.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)] [string] $Inbox,
    [Parameter(Mandatory = $true)] [string] $Archive,
    [Parameter(Mandatory = $true)] [string] $Register,
    [int] $DueDay = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# File name patterns per client (same as config/clients.yaml)
$Patterns = @(
    @{ Cedant = 'C01'; Regex = '^NBL_(?<type>inforce|billing|claims)_(?<yyyymm>\d{6})(?<suffix>_[a-z0-9]+)?\.csv$' },
    @{ Cedant = 'C02'; Regex = '^HRL_(?<type>inforce|billing|claims)_(?<yyyymm>\d{6})(?<suffix>_[a-z0-9]+)?\.txt$' },
    @{ Cedant = 'C03'; Regex = '^Kestrel_(?<type>inforce|billing|claims)_(?<yyyymm>\d{6})(?<suffix>_[a-z0-9]+)?\.xlsx$' },
    @{ Cedant = 'C04'; Regex = '^AMR_(?<type>inforce|billing|claims)_(?<yyyymm>\d{6})(?<suffix>_[a-z0-9]+)?\.dat$' }
)

function Get-FileIdentity {
    param([string] $Name)
    foreach ($p in $Patterns) {
        $m = [regex]::Match($Name, $p.Regex, 'IgnoreCase')
        if ($m.Success) {
            $yyyymm = $m.Groups['yyyymm'].Value
            return [pscustomobject]@{
                Cedant   = $p.Cedant
                FileType = $m.Groups['type'].Value.ToLower()
                Period   = '{0}-{1}' -f $yyyymm.Substring(0, 4), $yyyymm.Substring(4, 2)
            }
        }
    }
    return $null
}

function Get-DueDate {
    param([string] $Period, [int] $Day)
    $start = [datetime]::ParseExact("$Period-01", 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
    return $start.AddMonths(1).AddDays($Day - 1)
}

if (-not (Test-Path -LiteralPath $Inbox)) { throw "Inbox not found: $Inbox" }

# Hashes already in the register count as received, so a re-run never registers a file twice
$seen = @{}
if (Test-Path -LiteralPath $Register) {
    foreach ($row in Import-Csv -LiteralPath $Register) {
        if ($row.Status -eq 'RECEIVED' -and -not $seen.ContainsKey($row.Sha256)) { $seen[$row.Sha256] = $row.FileName }
    }
}

$files = Get-ChildItem -LiteralPath $Inbox -File |
    Where-Object { $_.Extension -ne '.ctl' } |
    Sort-Object LastWriteTime, Name

$results = foreach ($f in $files) {
    $id = Get-FileIdentity -Name $f.Name
    $hash = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLower()
    $status = 'RECEIVED'
    $duplicateOf = ''
    $due = $null
    $daysLate = 0

    if ($null -eq $id) {
        $status = 'UNRECOGNIZED'
    }
    elseif ($seen.ContainsKey($hash)) {
        $status = 'REJECTED_DUPLICATE'
        $duplicateOf = $seen[$hash]
    }
    else {
        $seen[$hash] = $f.Name
    }

    if ($null -ne $id) {
        $due = Get-DueDate -Period $id.Period -Day $DueDay
        $daysLate = [math]::Max(0, ($f.LastWriteTime.Date - $due.Date).Days)
        $folder = Join-Path (Join-Path $Archive $id.Cedant) $id.Period
    }
    else {
        $folder = Join-Path $Archive '_unrecognized'
    }

    $stamp = $f.LastWriteTime.ToString('yyyyMMddTHHmm')
    if ($PSCmdlet.ShouldProcess($f.Name, "Archive to $folder")) {
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
        Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $folder "${stamp}_$($f.Name)") -Force
        $ctl = [IO.Path]::ChangeExtension($f.FullName, '.ctl')
        if (Test-Path -LiteralPath $ctl) {
            Copy-Item -LiteralPath $ctl -Destination (Join-Path $folder "${stamp}_$([IO.Path]::GetFileName($ctl))") -Force
        }
    }

    [pscustomobject]@{
        FileName    = $f.Name
        Cedant      = if ($id) { $id.Cedant } else { '' }
        FileType    = if ($id) { $id.FileType } else { '' }
        Period      = if ($id) { $id.Period } else { '' }
        Bytes       = $f.Length
        ReceivedAt  = $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm')
        DueDate     = if ($due) { $due.ToString('yyyy-MM-dd') } else { '' }
        DaysLate    = $daysLate
        Sha256      = $hash
        Status      = $status
        DuplicateOf = $duplicateOf
        RegisteredAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    }
}

if ($PSCmdlet.ShouldProcess($Register, 'Append to file register')) {
    $dir = Split-Path -Parent $Register
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $results | Export-Csv -LiteralPath $Register -NoTypeInformation -Append
}

$received = @($results | Where-Object Status -eq 'RECEIVED').Count
$dupes = @($results | Where-Object Status -eq 'REJECTED_DUPLICATE').Count
$late = @($results | Where-Object { $_.Status -eq 'RECEIVED' -and $_.DaysLate -gt 0 }).Count
$unknown = @($results | Where-Object Status -eq 'UNRECOGNIZED').Count
Write-Host ("Intake: {0} files received, {1} duplicates rejected, {2} late, {3} unrecognized" -f $received, $dupes, $late, $unknown)

$results
