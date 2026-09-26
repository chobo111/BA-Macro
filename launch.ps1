$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$studioPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $studioPython)) {
    $studioLauncher = Get-Command py -ErrorAction SilentlyContinue
    $studioInterpreter = Get-Command python -ErrorAction SilentlyContinue
    $bundledInterpreter = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if ($studioLauncher) {
        & $studioLauncher.Source -3 -m venv (Join-Path $PSScriptRoot '.venv')
    } elseif ($studioInterpreter) {
        & $studioInterpreter.Source -m venv (Join-Path $PSScriptRoot '.venv')
    } elseif (Test-Path -LiteralPath $bundledInterpreter) {
        & $bundledInterpreter -m venv (Join-Path $PSScriptRoot '.venv')
    } else {
        throw 'Python 3.10 or later is required. Install Python, then run launch.cmd again.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
& $studioPython -c "import PySide6, keyboard"
if ($LASTEXITCODE -ne 0) {
    & $studioPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& (Join-Path $PSScriptRoot 'create-shortcut.ps1')
$studioGuiPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
Start-Process -FilePath $studioGuiPython -ArgumentList ('"' + (Join-Path $PSScriptRoot 'app.py') + '"') -WorkingDirectory $PSScriptRoot
exit 0
