param(
    [string]$BuildRoot = "",
    [string]$Python = "python",
    [string[]]$Coins = @("BLC", "BBTC", "ELT", "LIT", "PHO", "UMO")
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Desktop = Join-Path $RepoRoot "unified\desktop"
if (-not $BuildRoot) {
    $BuildRoot = Join-Path $RepoRoot ".build-multi-windows"
}
$WsRoot = Join-Path $BuildRoot "wsroot"
$VenvDir = Join-Path $BuildRoot "venv"
$PythonBin = Join-Path $VenvDir "Scripts\python.exe"

function Run-Step([string]$Name, [scriptblock]$Body) {
    Write-Host "== $Name =="
    & $Body
}

function Ensure-PythonEnv {
    if (-not (Test-Path $PythonBin)) {
        & $Python -m venv $VenvDir
    }
    & $PythonBin -m pip install --upgrade pip setuptools wheel pyinstaller
    & $PythonBin -m pip install -r (Join-Path $RepoRoot "contrib\requirements\requirements.txt")
    & $PythonBin -m pip install -r (Join-Path $RepoRoot "contrib\requirements\requirements-hw.txt")
    & $PythonBin -m pip install "cryptography==48.0.1" "pycryptodomex==3.23.0" "argon2-cffi==25.1.0"
    & $PythonBin -m pip install (Join-Path $RepoRoot "blake256")
}

function Ensure-Blake256Dll {
    $Dll = Join-Path $RepoRoot "blake256.dll"
    if (Test-Path $Dll) {
        return $Dll
    }
    $Gcc = Get-Command gcc -ErrorAction SilentlyContinue
    if (-not $Gcc) {
        throw "blake256.dll is missing and gcc is not available. Build or copy blake256.dll before running this script."
    }
    & $Gcc.Source -O2 -shared `
        -I (Join-Path $RepoRoot "blake256") `
        -o $Dll `
        (Join-Path $RepoRoot "blake256\blake256_dll.c") `
        (Join-Path $RepoRoot "blake256\blake.c")
    if (-not (Test-Path $Dll)) {
        throw "failed to build blake256.dll"
    }
    return $Dll
}

function Ensure-LibsecpDll {
    $Candidates = @(
        (Join-Path $RepoRoot "libsecp256k1-6.dll"),
        (Join-Path $env:USERPROFILE "Desktop\Blakestream-Electrium-Windows-QA\installed-UMO\_internal\libsecp256k1-6.dll")
    )
    $Dll = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Dll) {
        throw "libsecp256k1-6.dll is missing. Copy it from a known-good Windows Electrum build into the repo root before running this script."
    }
    $PkgDir = & $PythonBin -c "import importlib.util; spec=importlib.util.find_spec('electrum_ecc'); print(spec.submodule_search_locations[0])"
    Copy-Item -Force $Dll (Join-Path $PkgDir "libsecp256k1-6.dll")
    return (Join-Path $PkgDir "libsecp256k1-6.dll")
}

function Prepare-Workspaces {
    New-Item -ItemType Directory -Force $WsRoot | Out-Null
    foreach ($Coin in $Coins) {
        Write-Host "== workspace: $Coin =="
        & $PythonBin (Join-Path $RepoRoot "scripts\prepare_wallet_variant.py") `
            --coin $Coin --workspace (Join-Path $WsRoot $Coin)
    }
}

function Build-Daemons([string]$NativeLib) {
    $Out = Join-Path $BuildRoot "daemons-out"
    Remove-Item -Recurse -Force $Out -ErrorAction SilentlyContinue
    foreach ($Coin in $Coins) {
        $Lc = $Coin.ToLowerInvariant()
        $Ws = Join-Path $WsRoot $Coin
        Write-Host "== electrum-$Lc (from $Ws) =="
        Push-Location $Ws
        try {
            $env:PYTHONPATH = $Ws
            & $PythonBin -m PyInstaller `
                --onedir --noconfirm --clean `
                --name "electrum-$Lc" `
                --distpath (Join-Path $Out "dist") `
                --workpath (Join-Path $Out "build\$Coin") `
                --specpath (Join-Path $Out "spec\$Coin") `
                --collect-all electrum --collect-all electrum_ecc `
                --exclude-module PyQt5 --exclude-module PyQt6 `
                --add-binary "$NativeLib;." `
                run_electrum
        } finally {
            Pop-Location
            Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
        }
        $Bin = Join-Path $Out "dist\electrum-$Lc\electrum-$Lc.exe"
        if (-not (Test-Path $Bin)) {
            throw "daemon build failed: $Coin"
        }
    }
}

function Build-Supervisor([string]$NativeLib) {
    $Out = Join-Path $BuildRoot "supervisor-out"
    Remove-Item -Recurse -Force $Out -ErrorAction SilentlyContinue
    Push-Location $BuildRoot
    try {
        $env:PYTHONPATH = $RepoRoot
        & $PythonBin -m PyInstaller `
            --onedir --noconfirm --clean `
            --name electrum-backend `
            --distpath (Join-Path $Out "dist") `
            --workpath (Join-Path $Out "build") `
            --specpath (Join-Path $Out "spec") `
            --collect-all electrum --collect-all electrum_ecc `
            --collect-submodules unified `
            --collect-all argon2 `
            --exclude-module PyQt5 --exclude-module PyQt6 `
            --add-data "$RepoRoot\coin-overlays;coin-overlays" `
            --add-binary "$NativeLib;." `
            --paths $RepoRoot `
            (Join-Path $RepoRoot "unified\launcher.py")
    } finally {
        Pop-Location
        Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
    }
    $Bin = Join-Path $Out "dist\electrum-backend\electrum-backend.exe"
    if (-not (Test-Path $Bin)) {
        throw "supervisor build failed"
    }
}

function Stage-Backend {
    $Backend = Join-Path $Desktop "backend"
    Remove-Item -Recurse -Force $Backend -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force (Join-Path $Backend "supervisor") | Out-Null
    New-Item -ItemType Directory -Force (Join-Path $Backend "daemons") | Out-Null
    Copy-Item -Recurse -Force (Join-Path $BuildRoot "supervisor-out\dist\electrum-backend") `
        (Join-Path $Backend "supervisor")
    foreach ($Coin in $Coins) {
        $Lc = $Coin.ToLowerInvariant()
        Copy-Item -Recurse -Force (Join-Path $BuildRoot "daemons-out\dist\electrum-$Lc") `
            (Join-Path $Backend "daemons")
    }
}

function Build-Desktop {
    Push-Location $Desktop
    try {
        if (Test-Path package-lock.json) {
            npm ci
        } else {
            npm install
        }
        npm run build
        npx electron-builder --win portable nsis --x64 --config.directories.output=release
    } finally {
        Pop-Location
    }
}

function Stage-Outputs {
    $OutDir = Join-Path $RepoRoot "outputs\multiwallet\windows"
    Remove-Item -Recurse -Force $OutDir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $OutDir | Out-Null
    $Release = Join-Path $Desktop "release"
    Get-ChildItem $Release -File |
        Where-Object { $_.Extension -in ".exe", ".blockmap", ".yml" } |
        ForEach-Object { Copy-Item -Force $_.FullName $OutDir }
    Copy-Item -Recurse -Force (Join-Path $Release "win-unpacked") $OutDir
    Get-ChildItem $OutDir | Sort-Object Name | ForEach-Object { Write-Host $_.FullName }
}

Run-Step "python env" { Ensure-PythonEnv }
$NativeLib = Ensure-Blake256Dll
$LibsecpDll = Ensure-LibsecpDll
Write-Host "== libsecp256k1: $LibsecpDll =="
Run-Step "workspaces" { Prepare-Workspaces }
Run-Step "daemons" { Build-Daemons $NativeLib }
Run-Step "supervisor" { Build-Supervisor $NativeLib }
Run-Step "stage backend" { Stage-Backend }
Run-Step "desktop" { Build-Desktop }
Run-Step "stage outputs" { Stage-Outputs }
Write-Host "== multiwallet build complete: windows =="
