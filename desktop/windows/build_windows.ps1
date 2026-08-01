$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptRoot "..\..")
$Python = Get-Command py -ErrorAction SilentlyContinue

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "Windows candidate must be prepared on Windows x64."
}
if (-not $Python) {
    throw "Python Launcher for Windows was not found. Install the approved Python 3.12 x64 runtime first."
}

Push-Location $ProjectRoot
try {
    & py -3.12-64 "$ScriptRoot\build_plan.py" --candidate
    if ($LASTEXITCODE -ne 0) {
        throw "Windows candidate gate is closed. Do not generate or share an installer."
    }
    throw "Build skeleton validated, but the frozen launcher and installer recipe are not integrated yet."
}
finally {
    Pop-Location
}
