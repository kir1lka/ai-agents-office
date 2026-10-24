$ErrorActionPreference = 'Stop'
$bridge = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'office-bridge.py'))
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"' + $bridge + '"') } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
Write-Output 'AI Office: local bridge stopped.'
