# patent_shortcut_install.ps1 — 建立／移除「專利分析平台」桌面捷徑
#
# 捷徑點一下就跑 scripts\patent_launcher.ps1：確保 Companion 在跑（冪等）＋ 開前端。
#
# 視窗怎麼隱藏（兩層都要，缺一會閃黑框）：
#   1. 命令列帶 -WindowStyle Hidden -NonInteractive（PowerShell 自身不顯示視窗）。
#   2. 捷徑物件的 WindowStyle = 7（最小化）—— .lnk 層級的保險，
#      避免某些 Windows 版本在 PowerShell 接手前先閃一次 console。
#   （若使用者機器有安裝 pwsh，會優先用 pwsh.exe，啟動更快也更不易閃窗。）
#
# 不寫死：目標腳本由 $PSScriptRoot 推導；捷徑放置目錄、前端網址、StateDir 皆為參數，
# 未來改成 Railway 網址或裝到別人電腦，只需帶不同參數，不必改腳本內容。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\patent_shortcut_install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\patent_shortcut_install.ps1 -FrontendUrl https://patent.up.railway.app
#   powershell -ExecutionPolicy Bypass -File scripts\patent_shortcut_install.ps1 -ShortcutDir "D:\tmp"   # 測試用，不動桌面
#   powershell -ExecutionPolicy Bypass -File scripts\patent_shortcut_install.ps1 -Remove    # 移除捷徑

[CmdletBinding()]
param(
    # 捷徑檔名（不含 .lnk）。
    [string]$ShortcutName = "專利分析平台",
    # 捷徑放置目錄；預設使用者桌面。測試時指到暫存目錄即可不碰真桌面。
    [string]$ShortcutDir,
    # 傳給 launcher 的前端網址；不給就由 launcher 自行決定（環境變數或預設本機）。
    [string]$FrontendUrl,
    # 傳給 launcher 的 StateDir；不給就由 launcher 推導（<專案>\var）。
    [string]$StateDir,
    # 傳給 launcher 的「是否順便起本機 backend」模式。
    [ValidateSet("IfLocal", "Never", "Always")]
    [string]$StartBackend,
    # 捷徑圖示（可指到自訂 .ico）；預設用 PowerShell 內建圖示。
    [string]$IconPath,
    # 移除捷徑而非建立。
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$launcher = Join-Path $PSScriptRoot "patent_launcher.ps1"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "找不到 launcher：$launcher"
}
$launcher = (Resolve-Path $launcher).Path

if (-not $ShortcutDir) {
    $ShortcutDir = [Environment]::GetFolderPath("Desktop")
}
New-Item -ItemType Directory -Path $ShortcutDir -Force | Out-Null
$ShortcutDir = (Resolve-Path $ShortcutDir).Path
$shortcutPath = Join-Path $ShortcutDir "$ShortcutName.lnk"

# ---------- 移除 ----------
if ($Remove) {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force
        Write-Host "[shortcut] 已移除捷徑：$shortcutPath"
    }
    else {
        Write-Host "[shortcut] 捷徑不存在，略過：$shortcutPath"
    }
    Write-Host "[shortcut] 註：Companion 常駐工作請用 scripts\companion_uninstall.ps1 移除。"
    exit 0
}

# ---------- 建立 ----------
# 優先 pwsh（PowerShell 7，啟動較快、隱藏視窗較徹底），沒有才退回內建 powershell.exe。
$shellExe = (Get-Command "pwsh.exe" -ErrorAction SilentlyContinue).Source
if (-not $shellExe) {
    $shellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
}
if (-not (Test-Path -LiteralPath $shellExe)) {
    throw "找不到可用的 PowerShell 執行檔：$shellExe"
}

# -WindowStyle Hidden：Companion 與 backend 都在背景跑，可見性交給前端狀態燈。
$argList = @(
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden", "-File", "`"$launcher`""
)
if ($FrontendUrl) { $argList += @("-FrontendUrl", "`"$FrontendUrl`"") }
if ($StateDir) { $argList += @("-StateDir", "`"$StateDir`"") }
if ($StartBackend) { $argList += @("-StartBackend", $StartBackend) }
$arguments = $argList -join " "

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $shellExe
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = "開啟專利分析平台（自動確保 Companion 在背景執行）"
# 7 = 最小化。.lnk 層再壓一次視窗，避免部分環境在 PowerShell 接手前閃黑框。
$shortcut.WindowStyle = 7
if ($IconPath) { $shortcut.IconLocation = $IconPath }
else { $shortcut.IconLocation = "$shellExe,0" }
$shortcut.Save()

Write-Host "[shortcut] 已建立捷徑：$shortcutPath"
Write-Host "[shortcut] 目標：$shellExe $arguments"
Write-Host "[shortcut] 移除：powershell -ExecutionPolicy Bypass -File scripts\patent_shortcut_install.ps1 -Remove"
