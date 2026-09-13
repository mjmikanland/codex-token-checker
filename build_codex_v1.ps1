$ErrorActionPreference = "Stop"

Write-Host "=== Codex Token Checker v1.0 build ===" -ForegroundColor Cyan

python -m pip install --upgrade pyinstaller pystray pillow
python .\make_codex_icon.py

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name CodexTokenChecker `
  --icon .\CodexTokenChecker.ico `
  --collect-all pystray `
  --collect-all PIL `
  .\codex_gui_v6.py

Write-Host "" 
Write-Host "Build complete:" -ForegroundColor Green
Write-Host "$PWD\dist\CodexTokenChecker.exe"
