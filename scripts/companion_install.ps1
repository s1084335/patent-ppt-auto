# companion_install.ps1 — 註冊 Patent Companion（AI bridge）為登入自動啟動的常駐工作
#
# 為什麼用工作排程器（Task Scheduler）而非 Windows 服務：
#   Companion 驅動的是「使用者自己的 Claude CLI 與登入 token」，必須以該使用者身分執行。
#   Windows 服務預設跑 LocalSystem／需要另存服務帳號密碼，拿不到使用者的 CLI 登入狀態；
#   工作排程器的「登入時觸發＋以目前使用者執行」正好符合這個架構前提，
#   而且不必額外安裝 NSSM／WinSW 二進位，Installer 打包更單純。
#   崩潰自動重啟由排程器內建的 RestartCount／RestartInterval 提供。
#
# 冪等：重跑會覆蓋同名工作（-Force），不會產生重複工作、不會報錯。
#
# 用法（在專案根目錄或任一位置執行皆可，路徑由腳本自行推導）：
#   powershell -ExecutionPolicy Bypass -File scripts\companion_install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\companion_install.ps1 -StateDir "D:\companion-state"
#   powershell -ExecutionPolicy Bypass -File scripts\companion_install.ps1 -StartNow:$false
#
# 移除：scripts\companion_uninstall.ps1

[CmdletBinding()]
param(
    # 排程工作名稱；改名可在同機併存多個 Companion（例如測試用）。
    [string]$TaskName = "PatentCompanion",
    # 專案根目錄；預設由本腳本位置推導，不寫死磁碟路徑。
    [string]$ProjectRoot,
    # 狀態與日誌目錄；預設 <專案>\var。可指到使用者可寫的任意位置。
    [string]$StateDir,
    # 執行 Companion 的指令；預設用 uv（與專案其他腳本一致）。
    [string]$UvPath,
    [double]$PollSeconds = 3.0,
    [int]$StaleAfterSeconds = 1800,
    [string]$LogLevel = "INFO",
    # 單一日誌檔上限與保留份數（輪替，避免長期常駐把磁碟寫爆）。
    [int]$LogMaxBytes = 5242880,
    [int]$LogBackups = 5,
    # 註冊後是否立刻啟動一次（不必等下次登入）。
    [switch]$StartNow = $true
)

$ErrorActionPreference = "Stop"

# ---------- 1. 解析路徑（一律推導或參數化，不寫死） ----------
if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "backend\app\worker\ai_bridge.py"))) {
    throw "ProjectRoot 不像專案根目錄（找不到 backend\app\worker\ai_bridge.py）：$ProjectRoot"
}

if (-not $StateDir) {
    $StateDir = Join-Path $ProjectRoot "var"
}
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
$StateDir = (Resolve-Path $StateDir).Path

$logFile = Join-Path $StateDir "ai_bridge.log"
$heartbeatFile = Join-Path $StateDir "ai_bridge_heartbeat.json"
# 停止旗標檔：uninstall／手動停止建立此檔，serve 會做完手上的 job 再退出。
# Windows 的 Stop-ScheduledTask 是 TerminateProcess，Python signal handler 收不到
# （實測 SIGINT／SIGTERM 皆不觸發，只有 CTRL_BREAK_EVENT 送到 process group 才會，
#  而排程器不建立 process group、也不送 CTRL_BREAK），所以 graceful shutdown 只能靠旗標檔。
$stopFile = Join-Path $StateDir "ai_bridge_stop"

# uv 由 PATH 自動偵測；找不到才要求使用者用 -UvPath 指定。
if (-not $UvPath) {
    $uvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
    if (-not $uvCommand) {
        throw "PATH 找不到 uv；請用 -UvPath 指定 uv.exe 完整路徑。"
    }
    $UvPath = $uvCommand.Source
}
if (-not (Test-Path -LiteralPath $UvPath)) {
    throw "uv 不存在：$UvPath"
}

# .env 位置只作提示；ai_bridge 自己會從 <專案>\.env 載入，環境變數可覆蓋。
$envFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Warning "找不到 $envFile；Companion 將只吃系統環境變數的 PG* 設定。"
}

Write-Host "[companion_install] 專案根目錄：$ProjectRoot"
Write-Host "[companion_install] 狀態／日誌目錄：$StateDir"
Write-Host "[companion_install] uv：$UvPath"

# ---------- 2. 產生啟動包裝腳本（設好 cwd 與環境變數後啟動 serve） ----------
# 排程器只認單一命令列，包裝成 .cmd 可一併固定工作目錄與 UV_CACHE_DIR，
# 也讓使用者能手動雙擊同一支腳本重現排程行為。
$launcher = Join-Path $StateDir "companion_serve.cmd"
$launcherLines = @(
    '@echo off',
    'rem 由 scripts\companion_install.ps1 產生；請勿手改，重跑安裝腳本即可更新。',
    "cd /d `"$ProjectRoot`"",
    'set UV_CACHE_DIR=.uv-cache',
    "set AI_BRIDGE_STATE_DIR=$StateDir",
    ("`"$UvPath`" run python -m backend.app.worker.ai_bridge serve " +
     "--poll-seconds $PollSeconds --stale-after-seconds $StaleAfterSeconds " +
     "--log-level $LogLevel --log-file `"$logFile`" " +
     "--log-max-bytes $LogMaxBytes --log-backups $LogBackups " +
     "--heartbeat-file `"$heartbeatFile`" --stop-file `"$stopFile`"")
)
# 用 UTF8 無 BOM 寫 .cmd，避免 cmd.exe 讀到 BOM 出現首行錯誤。
[System.IO.File]::WriteAllLines($launcher, $launcherLines, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "[companion_install] 啟動包裝：$launcher"

# ---------- 3. 註冊排程工作（登入觸發＋崩潰自動重啟） ----------
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$launcher`"" -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# 以目前使用者身分、互動式執行——這是拿得到 Claude CLI 登入 token 的關鍵。
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettings `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

# -Force ＝ 冪等：已存在同名工作就整份覆蓋，重跑不會出錯也不會重複註冊。
Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Patent Companion (AI bridge serve) — 領取本機 AI 任務並驅動 Claude CLI" `
    -Force | Out-Null

Write-Host "[companion_install] 已註冊排程工作：$TaskName（登入時啟動、崩潰每分鐘重試）"

# ---------- 4. 可選：立刻啟動一次 ----------
if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "[companion_install] 已立即啟動；約數秒後可用 doctor 檢查 heartbeat。"
}

Write-Host ""
Write-Host "檢查指令："
Write-Host "  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  cd `"$ProjectRoot`"; `$env:UV_CACHE_DIR='.uv-cache'; `$env:AI_BRIDGE_STATE_DIR='$StateDir'; uv run python -m backend.app.worker.ai_bridge doctor"
Write-Host "  日誌：$logFile（單檔上限 $LogMaxBytes bytes，保留 $LogBackups 份）"
Write-Host "  heartbeat：$heartbeatFile"
Write-Host ""
Write-Host "優雅停止（做完手上的 job 再退出）："
Write-Host "  New-Item -ItemType File -Path '$stopFile' -Force | Out-Null"
Write-Host "  # serve 會在下一輪（約 $PollSeconds 秒）偵測到並收尾；旗標檔由 serve 自行清除。"
Write-Host "移除：powershell -ExecutionPolicy Bypass -File scripts\companion_uninstall.ps1 -TaskName $TaskName"
Write-Host ""
Write-Warning ("已知限制：Windows 登出／關機時，工作排程器直接終止行程（TerminateProcess），" +
    "來不及走優雅停止。當下若正在執行 AI job，該 job 會留在 running，" +
    "由下次啟動的 stale 回收（--stale-after-seconds $StaleAfterSeconds 秒）自動重新排入佇列。")
