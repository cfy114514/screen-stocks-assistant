param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name ScreenStocksAssistant --add-data 'index.html;.' --add-data 'charts.js;.' --add-data 'events_details.js;.' server.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
    & $Python scripts/package_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed' }
} finally { Pop-Location }
