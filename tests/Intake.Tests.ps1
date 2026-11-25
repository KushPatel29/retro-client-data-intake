# Pester 5 tests for scripts/Invoke-FileIntake.ps1. Run after `python -m retro_intake.generate`.
#   Invoke-Pester ./tests/Intake.Tests.ps1 -Output Detailed

BeforeAll {
    $root = Split-Path -Parent $PSScriptRoot
    $script:inbox = Join-Path $root 'data/inbox'
    $script:work = Join-Path ([IO.Path]::GetTempPath()) ("intake_" + [guid]::NewGuid().ToString('N'))
    $script:register = Join-Path $work 'register.csv'
    $script:archive = Join-Path $work 'archive'
    $script:result = & (Join-Path $root 'scripts/Invoke-FileIntake.ps1') -Inbox $inbox -Archive $archive -Register $register
}

AfterAll {
    Remove-Item -LiteralPath $script:work -Recurse -Force -ErrorAction SilentlyContinue
}

Describe 'Invoke-FileIntake' {
    It 'registers every data file and skips control files' {
        @($result).Count | Should -Be 36
        @($result | Where-Object { $_.FileName -like '*.ctl' }).Count | Should -Be 0
    }

    It 'recognises every file name' {
        @($result | Where-Object Status -eq 'UNRECOGNIZED').Count | Should -Be 0
    }

    It 'rejects the resent duplicate by hash' {
        $dup = @($result | Where-Object Status -eq 'REJECTED_DUPLICATE')
        $dup.Count | Should -Be 1
        $dup[0].FileName | Should -Be 'NBL_billing_202608_resend.csv'
        $dup[0].DuplicateOf | Should -Be 'NBL_billing_202608.csv'
    }

    It 'flags the three late Atlantic Mutual files for 2026-08' {
        $late = @($result | Where-Object { $_.DaysLate -gt 0 })
        $late.Count | Should -Be 3
        ($late | ForEach-Object Cedant | Sort-Object -Unique) | Should -Be 'C04'
        ($late | ForEach-Object Period | Sort-Object -Unique) | Should -Be '2026-08'
    }

    It 'archives files and control files by client and period' {
        (Get-ChildItem -LiteralPath (Join-Path $archive 'C02/2026-09') -File).Count | Should -Be 6
    }

    It 'does not register the same files twice on a re-run' {
        $again = & (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/Invoke-FileIntake.ps1') `
            -Inbox $inbox -Archive $archive -Register $register
        @($again | Where-Object Status -eq 'RECEIVED').Count | Should -Be 0
    }
}
