# companion_uninstall.ps1 — 乾淨移除 Patent Companion 常駐工作
#
# 對應安裝腳本：scripts\companion_install.ps1
# 冪等：工作不存在時只提示、不報錯，可重複執行。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\companion_uninstall.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\companion_uninstall.ps1 -RemoveState   # 一併刪狀態與日誌

[CmdletBinding()]
param(
    [string]$TaskName = "PatentCompanion",
    [string]$ProjectRoot,
    [string]$StateDir,
    # 預設保留日誌與 heartbeat（移除後仍可查最後為什麼掛掉）；要全清才加此旗標。
    [switch]$RemoveState,
    # 等待 Companion 做完手上 AI job 的上限秒數；逾時才強制終止。
    # 預設 300 秒＝一般 AI job（含 CLI 呼叫）足夠跑完，又不會讓解除安裝無限期卡住。
    [int]$GracefulTimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}
if (-not $StateDir) {
    $StateDir = Join-Path $ProjectRoot "var"
}

# ---------- 1. 停止並移除排程工作 ----------
#
# ⚠ Stop-ScheduledTask 本身**不會**觸發 serve 的優雅停止：Windows 上它等同
#   TerminateProcess，Python 的 signal handler 不會執行（實測 SIGINT／SIGTERM
#   皆不觸發，只有 CREATE_NEW_PROCESS_GROUP ＋ CTRL_BREAK_EVENT 才會，而排程器
#   兩者都不做）。直接 Stop 會把手上的 AI job 硬砍成孤兒 running，要等 stale 回收。
#
# 因此順序是：先建立停止旗標檔（serve 每輪輪詢）→ 等它做完手上的 job 自行退出
# → 逾時或本來就沒在跑，才用 Stop-ScheduledTask 強制收尾。
$stopFile = Join-Path $StateDir "ai_bridge_stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    if ($task.State -eq "Running") {
        New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
        Set-Content -LiteralPath $stopFile -Value (Get-Date -Format "o") -Encoding ascii
        Write-Host "[companion_uninstall] 已建立停止旗標，等待 Companion 做完手上的 job：$stopFile"

        $deadline = (Get-Date).AddSeconds($GracefulTimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            $state = (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue).State
            if ($state -ne "Running") { break }
            Start-Sleep -Seconds 2
        }

        $state = (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue).State
        if ($state -eq "Running") {
            Write-Warning ("Companion 在 $GracefulTimeoutSeconds 秒內未自行退出，改為強制終止；" +
                "手上若有 AI job 會留在 running，由下次啟動的 stale 回收重新排入佇列。")
            Stop-ScheduledTask -TaskName $TaskName
        }
        else {
            Write-Host "[companion_uninstall] Companion 已優雅退出（手上的 job 已完成）。"
        }
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[companion_uninstall] 已移除排程工作：$TaskName"
}
else {
    Write-Host "[companion_uninstall] 排程工作不存在，略過：$TaskName"
}

# 旗標檔是一次性訊號：serve 正常收尾會自行刪除，這裡補刪殘留（例如逾時強制終止的情況），
# 避免留在狀態目錄讓下次安裝後的 Companion 一啟動就退出。
if (Test-Path -LiteralPath $stopFile) {
    Remove-Item -LiteralPath $stopFile -Force
    Write-Host "[companion_uninstall] 已清除停止旗標：$stopFile"
}

# ---------- 2. 移除安裝腳本產生的啟動包裝 ----------
$launcher = Join-Path $StateDir "companion_serve.cmd"
if (Test-Path -LiteralPath $launcher) {
    Remove-Item -LiteralPath $launcher -Force
    Write-Host "[companion_uninstall] 已刪除啟動包裝：$launcher"
}

# ---------- 3. 可選：清掉狀態與日誌 ----------
if ($RemoveState) {
    foreach ($name in @("ai_bridge_heartbeat.json", "ai_bridge.log")) {
        Get-ChildItem -LiteralPath $StateDir -Filter "$name*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Force
                Write-Host "[companion_uninstall] 已刪除：$($_.FullName)"
            }
    }
}
else {
    Write-Host "[companion_uninstall] 保留狀態與日誌（加 -RemoveState 可一併刪除）：$StateDir"
}

Write-Host "[companion_uninstall] 完成。"
