$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptRoot "..\..")
$KitRoot = Split-Path -Parent $ProjectRoot
$BuildKitRoot = Join-Path $env:LOCALAPPDATA "AutoResearchBuildKit\v1"
$ToolchainRoot = Join-Path $BuildKitRoot "Python312"
$Python = Join-Path $ToolchainRoot "python.exe"
$CacheRoot = Join-Path $BuildKitRoot "Downloads"
$PythonInstaller = Join-Path $CacheRoot "python-3.12.10-amd64.exe"
$BundledPythonInstaller = Join-Path $KitRoot "Windows-Tools\python-3.12.10-amd64.exe"
$PythonInstallerSha256 = "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
$WheelhouseRoot = Join-Path $KitRoot "Windows-Wheelhouse"
$WheelhouseManifest = Join-Path $ScriptRoot "wheelhouse-sha256-v1.txt"
$ProxyToolsArchive = Join-Path $WheelhouseRoot "proxy_tools-0.1.0.tar.gz"
$InnoRoot = Join-Path $BuildKitRoot "InnoSetup-6.7.3"
$InnoInstaller = Join-Path $CacheRoot "innosetup-6.7.3.exe"
$BundledInnoInstaller = Join-Path $KitRoot "Windows-Tools\innosetup-6.7.3.exe"
$InnoInstallerSha256 = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732"
$Iscc = Join-Path $InnoRoot "ISCC.exe"
$OutputRoot = Join-Path $KitRoot "Windows-Output"
$WorkRoot = Join-Path $BuildKitRoot "Work"
$Report = Join-Path $KitRoot "Windows-Build-Report.txt"
$JsonReport = Join-Path $KitRoot "Windows-Build-Report.json"
$PackageName = "auto-research-internal-evidence-1.0.0.aresearch"
$ResolvedDependenciesHash = ""
$PackageHash = ""
$SourceCommit = ""

function Assert-FileSha256([string]$Path, [string]$Expected, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing. Re-extract the complete Windows Build Kit."
    }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "$Label has an unexpected SHA-256. Nothing was installed from it."
    }
}

function Assert-OfflineWheelhouse() {
    if (-not (Test-Path -LiteralPath $WheelhouseManifest -PathType Leaf)) {
        throw "The offline wheelhouse manifest is missing."
    }
    if (-not (Test-Path -LiteralPath $WheelhouseRoot -PathType Container)) {
        throw "Windows-Wheelhouse is missing. Re-extract the complete Windows Build Kit."
    }
    $ExpectedNames = @()
    foreach ($Line in Get-Content -LiteralPath $WheelhouseManifest) {
        if ($Line -notmatch '^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]+)$') {
            throw "The offline wheelhouse manifest is invalid."
        }
        $ExpectedHash = $Matches[1]
        $FileName = $Matches[2]
        $ExpectedNames += $FileName
        Assert-FileSha256 (Join-Path $WheelhouseRoot $FileName) $ExpectedHash "Offline dependency $FileName"
    }
    $ActualNames = @(Get-ChildItem -LiteralPath $WheelhouseRoot -File | ForEach-Object { $_.Name } | Sort-Object)
    $ExpectedNames = @($ExpectedNames | Sort-Object)
    if (Compare-Object -ReferenceObject $ExpectedNames -DifferenceObject $ActualNames) {
        throw "Windows-Wheelhouse contains missing or unapproved files."
    }
}

function Write-Report([string]$Status, [string]$Detail, [string]$SetupPresent = "NO", [string]$SetupHash = "") {
    @(
        "Auto Research Windows v1 RC Build"
        "STATUS=$Status"
        "DETAIL=$Detail"
        "SETUP_PRESENT=$SetupPresent"
        "SETUP_SHA256=$SetupHash"
        "SOURCE_COMMIT=$SourceCommit"
        "OFFICIAL_PACKAGE_SHA256=$PackageHash"
        "RESOLVED_DEPENDENCIES_SHA256=$ResolvedDependenciesHash"
        "INSTALLER_READY=NO"
        "NOTE=Generated Setup is an unaccepted release candidate until the complete Windows 11 checklist passes."
        "TIME=$([DateTime]::UtcNow.ToString('o'))"
    ) | Set-Content -Path $Report -Encoding UTF8
    [ordered]@{
        schema = "auto-research-windows-build-report-v1"
        status = $Status
        detail = $Detail
        candidate_version = "1.0.0-windows.rc.1"
        setup_present = ($SetupPresent -eq "YES")
        setup_sha256 = $SetupHash
        source_commit = $SourceCommit
        official_package_sha256 = $PackageHash
        resolved_dependencies_sha256 = $ResolvedDependenciesHash
        installer_ready = $false
        windows_11_acceptance_complete = $false
        created_at_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json -Depth 4 | Set-Content -Path $JsonReport -Encoding UTF8
}

try {
    if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
        throw "This entry must run on Windows 11 x64."
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "Windows x64 is required."
    }

    $SourceIdentityPath = Join-Path $KitRoot "SOURCE_IDENTITY.txt"
    if (-not (Test-Path -LiteralPath $SourceIdentityPath -PathType Leaf)) {
        throw "SOURCE_IDENTITY.txt is missing. Use the complete Windows Build Kit."
    }
    $SourceIdentityLine = Get-Content -LiteralPath $SourceIdentityPath | Where-Object {
        $_ -match '^SOURCE_COMMIT=[0-9a-f]{40}$'
    } | Select-Object -First 1
    if (-not $SourceIdentityLine) {
        throw "SOURCE_IDENTITY.txt does not contain a valid source commit."
    }
    $SourceCommit = ($SourceIdentityLine -split '=', 2)[1]

    $PackageCandidates = @(
        (Join-Path $KitRoot $PackageName),
        (Join-Path $ProjectRoot $PackageName),
        (Join-Path (Split-Path -Parent $ProjectRoot) $PackageName)
    )
    $PackagePath = $PackageCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $PackagePath) {
        $PackagePath = Get-ChildItem -LiteralPath $KitRoot -Filter $PackageName -File -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $PackagePath) {
        throw "The exact v1 official .aresearch package is missing. Wait for the complete kit to download."
    }

    Assert-FileSha256 $BundledPythonInstaller $PythonInstallerSha256 "Bundled Python 3.12.10 installer"
    Assert-FileSha256 $BundledInnoInstaller $InnoInstallerSha256 "Bundled Inno Setup 6.7.3 installer"
    Assert-OfflineWheelhouse

    New-Item -ItemType Directory -Force -Path $CacheRoot, $OutputRoot, $WorkRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $ToolchainRoot | Out-Null

    if (-not (Test-Path $Python)) {
        Write-Host "[1/8] Installing the bundled, verified Python toolchain..."
        Copy-Item -LiteralPath $BundledPythonInstaller -Destination $PythonInstaller -Force
        $Signature = Get-AuthenticodeSignature -FilePath $PythonInstaller
        if ($Signature.Status -ne "Valid" -or $Signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
            throw "The bundled Python installer signature is not trusted. Nothing was installed."
        }
        $Process = Start-Process -FilePath $PythonInstaller -Wait -PassThru -ArgumentList @(
            "/quiet", "InstallAllUsers=0", "TargetDir=$ToolchainRoot", "Include_pip=1",
            "Include_launcher=0", "Include_test=0", "PrependPath=0", "Shortcuts=0"
        )
        if ($Process.ExitCode -ne 0 -or -not (Test-Path $Python)) {
            throw "The isolated Python toolchain could not be installed. Exit code: $($Process.ExitCode)"
        }
    }

    $PythonVersion = (& $Python -c "import platform; print(platform.python_version())").Trim()
    if ($PythonVersion -ne "3.12.10") { throw "The isolated Python version is not the locked 3.12.10 release." }

    Write-Host "[2/8] Installing the locked Windows build dependencies..."
    & $Python -m pip install --disable-pip-version-check --no-input --no-index --find-links $WheelhouseRoot --only-binary=:all: "setuptools==80.9.0" "wheel==0.45.1"
    if ($LASTEXITCODE -ne 0) { throw "The locked source-build helpers could not be installed." }
    & $Python -m pip install --disable-pip-version-check --no-input --no-index --no-deps --no-build-isolation $ProxyToolsArchive
    if ($LASTEXITCODE -ne 0) { throw "The verified proxy_tools dependency could not be installed." }
    & $Python -m pip install --disable-pip-version-check --no-input --no-index --find-links $WheelhouseRoot --only-binary=:all: --requirement (Join-Path $ScriptRoot "requirements-windows-x64.lock")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    & $Python -m pip check
    if ($LASTEXITCODE -ne 0) { throw "The resolved Python dependency graph is inconsistent." }
    $DependencyAudit = Join-Path $WorkRoot "resolved-dependencies.txt"
    & $Python -m pip freeze --all | Sort-Object | Set-Content -Path $DependencyAudit -Encoding ascii
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $DependencyAudit)) {
        throw "The exact resolved dependency audit could not be written."
    }
    $ResolvedDependenciesHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $DependencyAudit).Hash.ToLowerInvariant()

    Write-Host "[3/8] Installing the bundled, verified Inno Setup compiler..."
    Copy-Item -LiteralPath $BundledInnoInstaller -Destination $InnoInstaller -Force
    $InnoSignature = Get-AuthenticodeSignature -FilePath $InnoInstaller
    if (
        $InnoSignature.Status -ne "Valid" -or
        $InnoSignature.SignerCertificate.Subject -notmatch "Pyrsys B\.V\."
    ) { throw "The Inno Setup installer publisher identity is not trusted." }
    $InnoProcess = Start-Process -FilePath $InnoInstaller -Wait -PassThru -ArgumentList @(
        "/VERYSILENT", "/CURRENTUSER", "/NORESTART", "/SP-", "/DIR=$InnoRoot"
    )
    if ($InnoProcess.ExitCode -ne 0 -or -not (Test-Path $Iscc)) {
        throw "The isolated Inno Setup compiler could not be installed."
    }

    Write-Host "[4/8] Verifying source, shared Fusion assets, and the exact v1 package..."
    Push-Location $ProjectRoot
    try {
        $env:PYTHONPATH = "$(Join-Path $ProjectRoot 'src');$ScriptRoot"
        & $Python "$ScriptRoot\verify_build_inputs.py" --project-root $ProjectRoot --package $PackagePath
        if ($LASTEXITCODE -ne 0) { throw "Frozen input verification failed." }
        & $Python -m unittest desktop.windows.tests.test_build_plan desktop.windows.tests.test_verify_build_inputs desktop.windows.tests.test_v1_package_contract
        if ($LASTEXITCODE -ne 0) { throw "Windows release-contract tests failed." }
        & $Python "$ScriptRoot\build_plan.py"
        if ($LASTEXITCODE -ne 0) { throw "Windows build contract failed." }
        & $Python -m compileall -q "$ScriptRoot"
        if ($LASTEXITCODE -ne 0) { throw "Windows static compilation failed." }

        Write-Host "[5/8] Freezing the self-contained Windows application..."
        $CandidateVersion = (Get-Content "$ScriptRoot\version.json" -Raw | ConvertFrom-Json).desktop_version
        $env:AUTO_RESEARCH_WINDOWS_BUILD_ROOT = $ProjectRoot
        $DistRoot = Join-Path $WorkRoot "dist"
        $PyInstallerWork = Join-Path $WorkRoot "pyinstaller"
        Remove-Item -LiteralPath $DistRoot, $PyInstallerWork -Recurse -Force -ErrorAction SilentlyContinue
        & $Python -m PyInstaller --noconfirm --clean --distpath $DistRoot --workpath $PyInstallerWork "$ScriptRoot\AutoResearch.spec"
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
        $AppRoot = Join-Path $DistRoot "Auto Research"
        $AppExe = Join-Path $AppRoot "Auto Research.exe"
        if (-not (Test-Path $AppExe -PathType Leaf)) { throw "The frozen app executable is missing." }
        $AnalysisToc = Join-Path $PyInstallerWork "AutoResearch\Analysis-00.toc"
        & $Python "$ScriptRoot\finalize_runtime_candidate.py" `
            --candidate-root $AppRoot `
            --desktop-version $CandidateVersion `
            --python-version $PythonVersion `
            --dependency-audit $DependencyAudit `
            --analysis-toc $AnalysisToc
        if ($LASTEXITCODE -ne 0) { throw "Bundled runtime, forbidden-module, or Fusion resource audit failed." }

        Write-Host "[6/8] Creating the per-user Setup with Inno Setup..."
        & $Iscc "/DAppVersion=$CandidateVersion" "/DSourceDir=$AppRoot" "/DOutputDir=$OutputRoot" "$ScriptRoot\AutoResearch.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }
        $Setup = Join-Path $OutputRoot "Auto-Research-$CandidateVersion-Setup.exe"
        if (-not (Test-Path $Setup -PathType Leaf)) { throw "Setup was not generated." }

        Write-Host "[7/8] Writing SHA-256 and build evidence..."
        $SetupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Setup).Hash.ToLowerInvariant()
        $PackageHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackagePath).Hash.ToLowerInvariant()
        @(
            "$SetupHash  $([IO.Path]::GetFileName($Setup))",
            "$PackageHash  $PackageName"
        ) | Set-Content -Path (Join-Path $OutputRoot "SHA256SUMS.txt") -Encoding ascii
        Copy-Item -LiteralPath $PackagePath -Destination (Join-Path $OutputRoot $PackageName) -Force
        Copy-Item -LiteralPath $DependencyAudit -Destination (Join-Path $OutputRoot "resolved-dependencies.txt") -Force
    }
    finally {
        Pop-Location
    }

    Write-Host "[8/8] Writing the audit report..."
    Write-Report "RC_SETUP_BUILT_ACCEPTANCE_PENDING" "The exact v1 package and locked build inputs passed. Complete the Win11 checklist before sharing." "YES" $SetupHash
    Write-Host "Done. Open Windows-Output and then follow the Chinese guide."
}
catch {
    Write-Report "RC_BUILD_FAILED" $_.Exception.Message
    Write-Host "RC build failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Send Windows-Build-Report.txt back to the Windows development task."
    exit 1
}
