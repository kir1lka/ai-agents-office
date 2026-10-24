$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'stop-office.ps1')
$shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'AI Office Local.lnk'
if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut }
Write-Output 'AI Office: sign-in startup removed. Wallpaper files are kept.'
