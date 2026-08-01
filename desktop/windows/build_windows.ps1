$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptRoot "..\..")
$KitRoot = Split-Path -Parent $ProjectRoot
$ToolchainRoot = Join-Path $env:LOCALAPPDATA "AutoResearchBuildKit\Python312"
$Python = Join-Path $ToolchainRoot "python.exe"
$CacheRoot = Join-Path $env:LOCALAPPDATA "AutoResearchBuildKit\Downloads"
$PythonInstaller = Join-Path $CacheRoot "python-3.12.10-amd64.exe"
$PythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
$Report = Join-Path $KitRoot "Windows-Build-Report.txt"

function Write-Report([string]$Status, [string]$Detail) {
    @(
        "Auto Research Windows Internal Test"
        "STATUS=$Status"
        "DETAIL=$Detail"
        "SETUP_PRESENT=NO"
        "NOTE=This kit validates the isolated Windows toolchain and frozen Windows tests. It does not claim that a Setup exists."
        "TIME=$([DateTime]::UtcNow.ToString('o'))"
    ) | Set-Content -Path $Report -Encoding UTF8
}

try {
    if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
        throw "This entry must run on Windows 11 x64."
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "Windows x64 is required."
    }

    New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $ToolchainRoot | Out-Null

    if (-not (Test-Path $Python)) {
        Write-Host "[1/4] Downloading the official isolated Python toolchain..."
        Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $PythonInstaller
        $Signature = Get-AuthenticodeSignature -FilePath $PythonInstaller
        if ($Signature.Status -ne "Valid" -or $Signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
            throw "The downloaded Python installer signature is not trusted. Nothing was installed."
        }
        $Process = Start-Process -FilePath $PythonInstaller -Wait -PassThru -ArgumentList @(
            "/quiet", "InstallAllUsers=0", "TargetDir=$ToolchainRoot", "Include_pip=1",
            "Include_launcher=0", "Include_test=0", "PrependPath=0", "Shortcuts=0"
        )
        if ($Process.ExitCode -ne 0 -or -not (Test-Path $Python)) {
            throw "The isolated Python toolchain could not be installed. Exit code: $($Process.ExitCode)"
        }
    }

    Write-Host "[2/4] Installing the one locked test dependency into the isolated toolchain..."
    & $Python -m pip install --disable-pip-version-check --no-input "cryptography==44.0.3"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

    Write-Host "[3/4] Running the existing Windows-only tests and static build contract..."
    Push-Location $ProjectRoot
    try {
        $env:PYTHONPATH = "$(Join-Path $ProjectRoot 'src');$ScriptRoot"
        & $Python -m unittest discover -s "desktop\windows\tests" -p "test_*.py"
        if ($LASTEXITCODE -ne 0) { throw "Windows tests failed." }
        & $Python "$ScriptRoot\build_plan.py"
        if ($LASTEXITCODE -ne 0) { throw "Windows build contract failed." }
        & $Python -m compileall -q "$ScriptRoot"
        if ($LASTEXITCODE -ne 0) { throw "Windows static compilation failed." }
    }
    finally {
        Pop-Location
    }

    Write-Host "[4/4] Writing the audit report..."
    Write-Report "BUILD_KIT_READY" "Windows-only tests and the frozen build contract passed. Setup remains blocked until the production shared bridge is integrated and Win11 smoke-tested."
    Write-Host "Done. Open Windows-Build-Report.txt in this folder."
}
catch {
    Write-Report "BUILD_KIT_FAILED" $_.Exception.Message
    Write-Host "Build kit check failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Send Windows-Build-Report.txt back to the Windows development task."
    exit 1
}
