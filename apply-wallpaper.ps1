$ErrorActionPreference = 'Stop'
$engine = Get-Process wallpaper64,wallpaper32 -ErrorAction SilentlyContinue | Where-Object Path | Select-Object -First 1
if (-not $engine) { throw 'Start Wallpaper Engine first.' }
$engineDir = Split-Path $engine.Path
$destination = Join-Path $engineDir 'projects\myprojects\ai-office-ru-live'
$marker = Join-Path $destination '.codex-local-office'
if ((Test-Path -LiteralPath $destination) -and -not (Test-Path -LiteralPath $marker)) {
    throw ('Destination already exists and is not this local office: ' + $destination)
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $destination 'fonts') -Force | Out-Null
Set-Content -LiteralPath $marker -Value 'AI Office RU local wallpaper' -Encoding utf8
foreach ($name in @('index.html', 'office-panel.js', 'project.json', 'preview-ru.png')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $destination $name) -Force
}
Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'fonts') -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $destination 'fonts') -Force
}
& $engine.Path -control openWallpaper -file (Join-Path $destination 'project.json') -monitor 0
Write-Output ('Applied local wallpaper: ' + $destination)
