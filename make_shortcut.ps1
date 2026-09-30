# Ярлык «Forge 3.0» на рабочем столе: пульт без консоли (pythonw -m forge.desktop).
# COM Save() маршалит имя через ANSI, поэтому создаём с латинским временным именем и
# переименовываем через .NET (Unicode).
$desktop = [Environment]::GetFolderPath('Desktop')
$dir = $PSScriptRoot  # каталог проекта = каталог этого скрипта
$pyw = Join-Path $dir '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pyw)) { Write-Error "Нет $pyw — сначала создай .venv (см. README)"; exit 1 }

$ws = New-Object -ComObject WScript.Shell
$tmp = Join-Path $desktop 'forge3_tmp.lnk'
$final = Join-Path $desktop 'Forge 3.0.lnk'
$s = $ws.CreateShortcut($tmp)
$s.TargetPath = $pyw
$s.Arguments = '-m forge.desktop'
$s.WorkingDirectory = $dir
$s.IconLocation = Join-Path $dir 'forge\desktop\assets\forge.ico'
# Описание — латиницей: WScript.Shell пишет его через ANSI (cp1252), кириллица станет «???».
$s.Description = 'Forge 3.0 - EVE Online industry panel'
$s.Save()
if (Test-Path $final) { Remove-Item -LiteralPath $final -Force }
[System.IO.File]::Move($tmp, $final)
Write-Output "Ярлык создан: $final"
