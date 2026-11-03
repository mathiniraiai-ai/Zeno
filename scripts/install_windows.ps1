# Zeno one-click installer for Windows 10/11 (called by install.bat).
# Safe to run again: it skips anything already installed. No admin rights needed.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-Location (Split-Path -Parent $PSScriptRoot)

# 0. Team rule: Zeno lives in Documents\Zeno and finished videos go to Downloads\Zeno Outputs.
#    If this folder is somewhere else, copy it there and continue from the new copy.
#    (Windows can't move a folder a script is running from. The owner's dev copy has a .zeno-dev file and stays put.)
$downloads = Join-Path $env:USERPROFILE "Downloads"
$documents = Join-Path $env:USERPROFILE "Documents"
$target = Join-Path $documents "Zeno"
$here = (Get-Location).Path
if (-not (Test-Path ".zeno-dev") -and ($here.TrimEnd('\') -ne $target.TrimEnd('\'))) {
    if (Test-Path $target) {
        Write-Host "Zeno is already installed at: $target"
        Write-Host "Open that folder and double-click install.bat there (or delete it first to install this copy)."
        exit 1
    }
    Write-Host "Copying Zeno to $target ..."
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    robocopy $here $target /E /XD ".venv" /NFL /NDL /NJH /NJS /NP | Out-Null
    Write-Host "Zeno is now in $target. Continuing the install there; you can delete this old folder afterwards."
    Set-Location $target
}
New-Item -ItemType Directory -Force -Path (Join-Path $downloads "Zeno Outputs") | Out-Null

Write-Host "=============================================="
Write-Host " Zeno installer (Windows)"
Write-Host " Folder : $(Get-Location)"
Write-Host " Videos : $(Join-Path $downloads 'Zeno Outputs')"
Write-Host "=============================================="

# 1. uv: installs Python 3.12 and the packages (user folder only)
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "[1/5] uv already installed"
} else {
    Write-Host "[1/5] Installing uv (Python manager)..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# 2. FFmpeg: use one already on the machine, else download a static build into tools\ffmpeg\
$ff = "tools\ffmpeg\ffmpeg.exe"; $fp = "tools\ffmpeg\ffprobe.exe"
if ((Test-Path $ff) -and (Test-Path $fp)) {
    Write-Host "[2/5] FFmpeg already in tools\ffmpeg"
} elseif ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Host "[2/5] Using system FFmpeg"
} else {
    Write-Host "[2/5] Downloading FFmpeg (about 100 MB)..."
    New-Item -ItemType Directory -Force -Path "tools\ffmpeg" | Out-Null
    $tmp = Join-Path $env:TEMP ("zeno_ffmpeg_" + [guid]::NewGuid())
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile "$tmp\ff.zip"
    Expand-Archive "$tmp\ff.zip" -DestinationPath $tmp -Force
    $bin = Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    Copy-Item $bin.FullName $ff -Force
    Copy-Item (Join-Path $bin.DirectoryName "ffprobe.exe") $fp -Force
    Remove-Item $tmp -Recurse -Force
    Write-Host "      FFmpeg installed in tools\ffmpeg"
}

# 3. Python 3.12 virtual environment + packages (CPU build of torch; works on any PC)
if (Test-Path ".venv\Scripts\python.exe") {
    Write-Host "[3/5] .venv exists; updating packages"
} else {
    Write-Host "[3/5] Creating .venv with Python 3.12..."
    uv venv --python 3.12 .venv
}
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "      NVIDIA GPU found. Zeno runs fine on CPU; for GPU speed see the README (optional)."
}

# 4. .env from the template (never overwrites an existing one)
if (Test-Path ".env") {
    Write-Host "[4/5] .env already exists (kept as is)"
} else {
    Copy-Item ".env.example" ".env"
    Write-Host "[4/5] Created .env from .env.example"
}

# 5. Alignment model (Meta wav2vec2, about 360 MB, into models\torch) + automatic Google login + real checks
Write-Host "[5/5] Downloading the alignment model (first time only) and checking everything..."
$ErrorActionPreference = "Continue"
& .venv\Scripts\python.exe scripts\setup.py --prewarm | Out-Null
& .venv\Scripts\python.exe scripts\setup.py --whoami | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[Auth] Opening your browser to sign in with Google (@gosupernova.live)..."
    & .venv\Scripts\python.exe scripts\setup.py --login
}
& .venv\Scripts\python.exe scripts\setup.py --smoke

Write-Host ""
Write-Host "=============================================="
& .venv\Scripts\python.exe scripts\setup.py --whoami | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host " Done. Zeno is signed in and ready!"
} else {
    Write-Host " Almost done: sign in anytime by running:"
    Write-Host "   .venv\Scripts\python.exe scripts\setup.py --login"
}
Write-Host "=============================================="
