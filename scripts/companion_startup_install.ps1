# companion_startup_install.ps1 — 用「啟動資料夾捷徑」讓 Patent Companion 開機自動背景啟動
#
# 為什麼不用工作排程器（原 companion_install.ps1 的做法）：
#   排程器以 LogonType Interactive 啟動行程時實測 LastTaskResult=1（啟動即失敗），
#   改 S4U 需要系統管理員權限（Set-ScheduledTask 回 Access is denied）。
#   啟動資料夾是純使用者層級機制，不需任何提權，且同樣以登入使用者身分執行——
#   這正是 Companion 需要的前提（要拿得到使用者自己的 Claude CLI 登入 token）。
#
# 代價（相對排程器）：崩潰後不會自動重啟。開發階段可接受；
#   需要自動重啟時再回頭用排程器（需管理員權限設 S4U）。
#
# 冪等：重跑會覆蓋既有捷徑與 wrapper，不會產生重複項目。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\companion_startup_install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\companion_startup_install.ps1 -StartNow:$false
#
# 移除：直接刪掉啟動資料夾裡的 PatentCompanion.lnk（腳本結尾會印出完整路徑）。

[CmdletBinding()]
param(
    # 專案根目錄；預設由本腳本位置推導，不寫死磁碟路徑。
    [string]$ProjectRoot,
    # 狀態與日誌目錄；預設 <專案>\var。
    [string]$StateDir,
    # 啟動資料夾；預設目前使用者 Startup，測試或特殊部署可導到其他資料夾。
    [string]$StartupDir,
    # 執行 Companion 的 uv 路徑；預設由 PATH 偵測。
    [string]$UvPath,
    [double]$PollSeconds = 3.0,
    [int]$StaleAfterSeconds = 1800,
    [string]$LogLevel = "INFO",
    [int]$LogMaxBytes = 5242880,
    [int]$LogBackups = 5,
    # 安裝後是否立刻啟動一次（不必等下次登入）。
    [switch]$StartNow = $true
)

$ErrorActionPreference = "Stop"

# ---------- 1. 解析路徑 ----------
if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "backend\app\worker\ai_bridge.py"))) {
    throw "ProjectRoot 不像專案根目錄（找不到 backend\app\worker\ai_bridge.py）：$ProjectRoot"
}

if (-not $StateDir) { $StateDir = Join-Path $ProjectRoot "var" }
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
$StateDir = (Resolve-Path $StateDir).Path

$logFile = Join-Path $StateDir "ai_bridge.log"
$bootstrapLog = Join-Path $StateDir "companion_bootstrap.log"
$heartbeatFile = Join-Path $StateDir "ai_bridge_heartbeat.json"
# 停止旗標檔：建立此檔＝要求 serve 做完手上的 job 後退出（Windows 收不到 SIGTERM，
# 只能靠旗標檔做 graceful shutdown，沿 ai_bridge 既有設計）。
$stopFile = Join-Path $StateDir "ai_bridge_stop"

if (-not $UvPath) {
    $uvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
    if (-not $uvCommand) { throw "PATH 找不到 uv；請用 -UvPath 指定 uv.exe 完整路徑。" }
    $UvPath = $uvCommand.Source
}
if (-not (Test-Path -LiteralPath $UvPath)) { throw "uv 不存在：$UvPath" }

$envFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Warning "找不到 $envFile；Companion 將只吃系統環境變數（DATABASE_URL／PG*）。"
}

Write-Host "[startup_install] 專案根目錄：$ProjectRoot"
Write-Host "[startup_install] 狀態／日誌目錄：$StateDir"
Write-Host "[startup_install] uv：$UvPath"

# ---------- 2. 產生啟動 wrapper（.cmd，固定 cwd 與環境變數） ----------
# 捷徑直接指向 .cmd；由 .cmd 統一設定工作目錄與環境變數，
# 讓「開機自動啟動」與「手動雙擊」跑的是同一段邏輯。
$launcher = Join-Path $StateDir "companion_serve.cmd"
$launcherLines = @(
    '@echo off',
    'rem 由 scripts\companion_startup_install.ps1 產生；請勿手改，重跑安裝腳本即可更新。',
    "cd /d `"$ProjectRoot`"",
    'set UV_CACHE_DIR=.uv-cache',
    "set AI_BRIDGE_STATE_DIR=$StateDir",
    ("`"$UvPath`" run python -m backend.app.worker.ai_bridge serve " +
     "--poll-seconds $PollSeconds --stale-after-seconds $StaleAfterSeconds " +
     "--log-level $LogLevel --log-file `"$logFile`" " +
     "--log-max-bytes $LogMaxBytes --log-backups $LogBackups " +
     "--heartbeat-file `"$heartbeatFile`" --stop-file `"$stopFile`" " +
     ">> `"$bootstrapLog`" 2>&1")
)
# ⚠ 必須用系統 ANSI（本機為 Big5）寫 .cmd，不可用 UTF-8：
# cmd.exe 以系統 codepage 解讀批次檔，專案路徑含中文（D:\力山\...）。
# 寫成 UTF-8 無 BOM 會讓 cmd 用 Big5 誤讀 UTF-8 位元組，路徑變成亂碼，
# `cd /d` 失敗 → 工作目錄留在別處 → ModuleNotFoundError: No module named 'backend'
# （2026-07-26 實測；原 companion_install.ps1 的 UTF8 無 BOM 寫法在中文路徑下不可用）。
[System.IO.File]::WriteAllLines($launcher, $launcherLines, [System.Text.Encoding]::Default)
Write-Host "[startup_install] 啟動包裝：$launcher"

# ---------- 2b. 產生隱藏啟動器（.vbs，完全無視窗背景啟動 .cmd） ----------
# 為什麼要 vbs：cmd.exe 本身無法真正隱藏——捷徑 WindowStyle 7（最小化）仍會閃視窗、
# 佔工作列；而 -WindowStyle Hidden 搭 cmd.exe 實測會讓行程立刻結束、heartbeat 不更新
# （見下方第 4 節原註）。wscript 跑 vbs、由 vbs 以 Run(cmd, 0, False) 啟動，
# 視窗完全不出現且 cmd 正常存活——這是 Windows 上讓 console 程式真背景化的乾淨解法。
# 路徑一律取自推導的 $launcher，不寫死磁碟路徑，移植重跑安裝即產出該機正確路徑。
$vbsLauncher = Join-Path $StateDir "companion_serve_hidden.vbs"
# VBS 字串以雙引號包路徑，VBS 內的雙引號用兩個雙引號跳脫。
# ⚠ 必須先設 sh.CurrentDirectory＝專案根：wscript 從 system32 啟動 cmd 時，cmd 初始 cwd
#   會是 system32；雖然 .cmd 內有 cd /d，但實測此鏈路下 ai_bridge 未起（No module 'backend'）。
#   由 vbs 先把 CurrentDirectory 設成專案根，cmd 一啟動就在對的目錄，uv 才找得到 .venv(3.12)。
$q = [char]34
$vbsCwdLine = 'sh.CurrentDirectory = ' + $q + $ProjectRoot + $q
$vbsRunLine = 'sh.Run ' + $q + 'cmd.exe /c ' + $q + $q + $launcher + $q + $q + $q + ', 0, False'
$vbsLines = @(
    "' 由 scripts\companion_startup_install.ps1 產生；請勿手改，重跑安裝腳本即可更新。",
    "' 以完全隱藏視窗（第 2 參數 0）的方式啟動 companion_serve.cmd，第 3 參數 False＝不等待。",
    "Dim sh",
    'Set sh = CreateObject("WScript.Shell")',
    $vbsCwdLine,
    $vbsRunLine
)
# vbs 走 wscript，UTF-8 with BOM 可正確處理中文路徑（與 cmd 的 Big5 限制不同）。
[System.IO.File]::WriteAllLines($vbsLauncher, $vbsLines, (New-Object System.Text.UTF8Encoding($true)))
Write-Host "[startup_install] 隱藏啟動器：$vbsLauncher"

# ---------- 3. 在啟動資料夾建立捷徑 ----------
$startupDir = if ($StartupDir) {
    New-Item -ItemType Directory -Path $StartupDir -Force | Out-Null
    (Resolve-Path $StartupDir).Path
}
else {
    [System.Environment]::GetFolderPath('Startup')
}
$shortcut = Join-Path $startupDir "PatentCompanion.lnk"

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
# 透過 wscript.exe 執行 vbs 隱藏啟動器：登入時完全不跳視窗、不佔工作列（像常駐背景服務）。
$link.TargetPath = "$env:SystemRoot\System32\wscript.exe"
$link.Arguments = "`"$vbsLauncher`""
$link.WorkingDirectory = $ProjectRoot
$link.Description = "Patent Companion (AI bridge serve) — 領取本機 AI 任務並驅動 Claude CLI"
$link.Save()

Write-Host "[startup_install] 已建立開機捷徑（隱藏背景）：$shortcut"

# ---------- 4. 可選：立刻啟動一次 ----------
if ($StartNow) {
    # 走與開機路徑相同的 vbs 隱藏啟動器：完全無視窗，且驗證的正是實際開機行為。
    # （原註記錄：-WindowStyle Hidden 搭 cmd.exe 會讓行程立刻結束、heartbeat 不更新；
    #   vbs 的 Run(cmd, 0, False) 無此問題，故此處統一改用 vbs。）
    Start-Process -FilePath "wscript.exe" -ArgumentList "`"$vbsLauncher`"" `
        -WorkingDirectory $ProjectRoot
    Write-Host "[startup_install] 已在背景（隱藏）啟動；約數秒後可用 doctor 檢查 heartbeat。"
}

Write-Host ""
Write-Host "檢查指令："
Write-Host "  cd `"$ProjectRoot`"; `$env:UV_CACHE_DIR='.uv-cache'; uv run python -m backend.app.worker.ai_bridge doctor"
Write-Host "  日誌：$logFile"
Write-Host "  啟動錯誤日誌：$bootstrapLog"
Write-Host "  heartbeat：$heartbeatFile"
Write-Host ""
Write-Host "優雅停止（做完手上的 job 再退出）："
Write-Host "  New-Item -ItemType File -Path '$stopFile' -Force | Out-Null"
Write-Host ""
Write-Host "取消開機自動啟動："
Write-Host "  Remove-Item '$shortcut'"
