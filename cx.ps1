$ErrorActionPreference = 'Stop'
$SourceDirectory = Split-Path -Parent $PSCommandPath

if ($args.Count -gt 0 -and $args[0] -eq 'install') {
    $BinDirectory = if ($env:CX_BIN_DIR) { $env:CX_BIN_DIR } else {
        Join-Path (Join-Path $env:LOCALAPPDATA 'codex-accounts') 'bin'
    }
    New-Item -ItemType Directory -Force -Path $BinDirectory | Out-Null
    $Destination = Join-Path $BinDirectory 'cx.cmd'
    if (Test-Path -LiteralPath $Destination) {
        $Existing = Get-Content -LiteralPath $Destination -Raw
        if (-not $Existing.Contains($PSCommandPath)) {
            throw "Refusing to replace an unrelated launcher: $Destination"
        }
    }
    $Launcher = "@echo off`r`nchcp 65001 >nul`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" %*`r`n"
    [System.IO.File]::WriteAllText($Destination, $Launcher, (New-Object System.Text.UTF8Encoding $false))
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($UserPath -split ';') -notcontains $BinDirectory) {
        [Environment]::SetEnvironmentVariable('Path', "$BinDirectory;$UserPath", 'User')
    }
    Write-Output "Installed $Destination. Open a new terminal to use cx."
    exit 0
}

$Helper = Join-Path $SourceDirectory 'codex_accounts.py'
if (-not (Test-Path -LiteralPath $Helper)) { throw 'Missing codex_accounts.py; use the complete checkout.' }
foreach ($Candidate in @('python3', 'python', 'py')) {
    $Command = Get-Command $Candidate -ErrorAction SilentlyContinue
    if (-not $Command) { continue }
    $Prefix = @()
    if ($Candidate -eq 'py') { $Prefix = @('-3') }
    & $Command.Source @Prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) {
        & $Command.Source @Prefix $Helper @args
        exit $LASTEXITCODE
    }
}
throw 'cx requires Python 3.11+.'
