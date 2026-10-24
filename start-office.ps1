$ErrorActionPreference = 'Stop'
$bridge = Join-Path $PSScriptRoot 'office-bridge.py'
$pythonCommand = Get-Command python.exe -ErrorAction Stop
$pythonWindowless = Join-Path (Split-Path $pythonCommand.Source) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) {
    throw 'Python with pythonw.exe is required.'
}
Start-Process -FilePath $pythonWindowless -ArgumentList ('"' + $bridge + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
