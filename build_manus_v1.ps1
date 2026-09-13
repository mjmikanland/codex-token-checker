$ErrorActionPreference = "Stop"

Write-Host "=== Manus Usage Checker v1.0 build ===" -ForegroundColor Cyan
python -m pip install --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ManusUsageChecker .\manus_usage_checker.py

Write-Host ""
Write-Host "Build complete:" -ForegroundColor Green
Write-Host "$PWD\dist\ManusUsageChecker.exe"
