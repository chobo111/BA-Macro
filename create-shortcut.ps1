$ErrorActionPreference = 'Stop'
$studioGuiPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$studioApp = Join-Path $PSScriptRoot 'app.py'
if (-not (Test-Path -LiteralPath $studioGuiPython)) {
    throw 'Run launch.cmd once to install the Python environment.'
}
if (-not (Test-Path -LiteralPath $studioApp)) {
    throw 'app.py was not found beside this script.'
}
$studioShell = New-Object -ComObject WScript.Shell
$studioShortcut = $studioShell.CreateShortcut((Join-Path $PSScriptRoot 'BA Macro.lnk'))
$studioShortcut.TargetPath = $studioGuiPython
$studioShortcut.Arguments = '"' + $studioApp + '"'
$studioShortcut.WorkingDirectory = $PSScriptRoot
$studioShortcut.Description = 'Launch BA Macro without a console window'
$studioShortcut.IconLocation = $studioGuiPython + ',0'
$studioShortcut.Save()
