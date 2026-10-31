# Zeno one-click installer for Windows 10/11 (called by install.bat).
# Safe to run again: it skips anything already installed. No admin rights needed.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Host "=============================================="
Write-Host " Zeno installer (Windows)"
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

# 5. Alignment model (Meta wav2vec2, about 360 MB, into models\torch) + real checks
Write-Host "[5/5] Downloading the alignment model (first time only) and checking everything..."
$ErrorActionPreference = "Continue"
& .venv\Scripts\python.exe scripts\setup.py --prewarm | Out-Null
& .venv\Scripts\python.exe scripts\setup.py --smoke

Write-Host ""
Write-Host "=============================================="
if (Select-String -Path ".env" -Pattern "^SCRIPTCUT_CLOUD_TOKEN=.+" -Quiet) {
    Write-Host " Done. Zeno is ready."
} else {
    Write-Host " Almost done: generate your key at"
    Write-Host "   https://zeno-production-bef7.up.railway.app"
    Write-Host " and paste it after SCRIPTCUT_CLOUD_TOKEN= in the .env file."
}
Write-Host "=============================================="
