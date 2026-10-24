$ErrorActionPreference = 'Stop'
$pythonCommand = Get-Command python.exe -ErrorAction Stop
$pythonWindowless = Join-Path (Split-Path $pythonCommand.Source) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Python with pythonw.exe is required.' }
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'AI Office Local.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonWindowless
$shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'office-bridge.py') + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'Local Codex activity for AI Office wallpaper'
$shortcut.Save()
& (Join-Path $PSScriptRoot 'start-office.ps1')
Write-Output 'AI Office: local bridge started; sign-in startup enabled.'
