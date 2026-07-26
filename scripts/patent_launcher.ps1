# patent_launcher.ps1 — 桌面捷徑的統一入口：一鍵開前端 ＋ 確保 Companion 在跑
#
# 流程：
#   1. 讀 Companion heartbeat（<StateDir>\ai_bridge_heartbeat.json）判斷是否還活著
#      ok        → 已在跑，跳過啟動（冪等：重複點擊不會疊出第二個 Companion）
#      其餘狀態  → 背景隱藏啟動 Companion（優先用既有排程工作，否則直接起隱藏行程）
#   2. 檢查前端網址是否可連
#      可連      → 直接開瀏覽器
#      不可連    → 依 -StartBackend 決定是否順便起本機 backend（僅限本機網址）
#   3. 開瀏覽器到前端網址
#
# 與 companion_install.ps1 的關係：
#   常駐機制的唯一事實來源仍是 companion_install.ps1 註冊的排程工作 PatentCompanion。
#   本腳本不重造常駐機制——偵測到該工作存在就用 Start-ScheduledTask 拉起（享有排程器的
#   崩潰自動重啟），只有在工作不存在（使用者沒跑過安裝腳本）時才退回直接起隱藏行程。
#
# 冪等設計：
#   判活一律以 heartbeat 檔為準（與 ai_bridge.read_heartbeat 同一套判準），
#   不用「找 process 名稱」——同機可能有多個 python.exe，且排程工作跑在 cmd.exe 底下。
#   另外排程工作本身設了 -MultipleInstances IgnoreNew，重複 Start 也不會產生第二份。
#
# 不寫死：
#   專案根由 $PSScriptRoot 推導；uv 由 PATH 偵測；前端網址與 StateDir 走參數或環境變數
#   （PATENT_FRONTEND_URL／AI_BRIDGE_STATE_DIR），未來換成 Railway 公開網址只要改一處。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File scripts\patent_launcher.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\patent_launcher.ps1 -FrontendUrl https://patent.up.railway.app
#   powershell -ExecutionPolicy Bypass -File scripts\patent_launcher.ps1 -DryRun   # 只印計畫，不做事

[CmdletBinding()]
param(
    # 前端網址。現階段預設本機 backend；未來換 Railway 公開網址只要改參數或環境變數。
    [string]$FrontendUrl,
    # Companion 排程工作名稱，需與 companion_install.ps1 的 -TaskName 一致。
    [string]$TaskName = "PatentCompanion",
    # 專案根目錄；預設由本腳本位置推導，不寫死磁碟路徑。
    [string]$ProjectRoot,
    # heartbeat／日誌所在目錄；預設 <專案>\var，可用 AI_BRIDGE_STATE_DIR 覆蓋。
    [string]$StateDir,
    [string]$UvPath,
    # 是否順便起本機 backend：
    #   IfLocal（預設）＝ 只有前端網址指向本機（localhost／127.0.0.1／::1）且連不上時才起。
    #   Never          ＝ 一律不起（前端已由 Railway 等遠端提供時用）。
    #   Always         ＝ 連不上就起，不管網址指向哪（少用，除錯用）。
    [ValidateSet("IfLocal", "Never", "Always")]
    [string]$StartBackend = "IfLocal",
    # 前端可連性探測的逾時秒數。
    [int]$ProbeTimeoutSeconds = 3,
    # 起了本機 backend 之後，最多等幾秒讓它可連（等到就開瀏覽器）。
    [int]$BackendWaitSeconds = 30,
    # 只偵測並印出決策計畫（LAUNCH_PLAN <json>），不啟動任何東西。供自動化測試使用。
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# ---------- 1. 解析路徑與設定（一律推導或參數化，不寫死） ----------
if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

if (-not $StateDir) {
    # 與 ai_bridge.default_state_dir 同一套優先序：環境變數 > 專案 var\。
    if ($env:AI_BRIDGE_STATE_DIR) { $StateDir = $env:AI_BRIDGE_STATE_DIR }
    else { $StateDir = Join-Path $ProjectRoot "var" }
}
if (Test-Path -LiteralPath $StateDir) {
    $StateDir = (Resolve-Path $StateDir).Path
}

if (-not $FrontendUrl) {
    # 參數 > 環境變數 > 現階段預設（本機 backend）。三層都不寫死在流程中。
    if ($env:PATENT_FRONTEND_URL) { $FrontendUrl = $env:PATENT_FRONTEND_URL }
    else { $FrontendUrl = "http://127.0.0.1:8000" }
}

$heartbeatFile = Join-Path $StateDir "ai_bridge_heartbeat.json"

# ---------- 2. 判斷 Companion 是否還活著（與 ai_bridge.read_heartbeat 同判準） ----------
# 判活門檻沿用 ai_bridge.HEARTBEAT_STALE_SECONDS（900 秒）：AI job 執行中不更新
# heartbeat，門檻需容納單筆長時任務。
$staleAfterSeconds = 900

function Get-CompanionState {
    <#
      回傳 missing／unreadable／stale／stopped／ok，語意與 Python 端 read_heartbeat 一致，
      前端狀態燈與本腳本才不會出現兩套判準。
    #>
    param([string]$Path, [int]$StaleAfterSeconds)

    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ state = "missing"; age = $null }
    }
    try {
        $data = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        $updatedAt = [datetime]::Parse(
            $data.updated_at, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
    }
    catch {
        return [pscustomobject]@{ state = "unreadable"; age = $null }
    }
    $age = ((Get-Date).ToUniversalTime() - $updatedAt.ToUniversalTime()).TotalSeconds
    if ($age -gt $StaleAfterSeconds) { $state = "stale" }
    elseif ("$($data.status)" -eq "stopped") { $state = "stopped" }
    else { $state = "ok" }
    return [pscustomobject]@{ state = $state; age = [math]::Round($age, 1) }
}

$companion = Get-CompanionState -Path $heartbeatFile -StaleAfterSeconds $staleAfterSeconds
# 冪等的核心一行：只有 ok（活著）才跳過啟動，其餘狀態都要拉起。
$startCompanion = ($companion.state -ne "ok")

# ---------- 3. 判斷前端是否可連、要不要順便起本機 backend ----------
function Test-FrontendReachable {
    <# 對前端網址發 HEAD/GET 探測；任何連線失敗都算不可連（不區分 4xx/5xx 由呼叫端決定）。#>
    param([string]$Url, [int]$TimeoutSeconds)
    try {
        Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSeconds -UseBasicParsing `
            -MaximumRedirection 2 -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        # HTTP 狀態碼有回應＝服務其實活著（例如首頁 401/404），只有連不上才算不可連。
        if ($_.Exception.Response) { return $true }
        return $false
    }
}

function Test-LocalUrl {
    <# 網址是否指向本機；決定「順便起 backend」是否適用。#>
    param([string]$Url)
    try { $host_ = ([uri]$Url).Host } catch { return $false }
    return @("localhost", "127.0.0.1", "::1", "[::1]") -contains $host_.ToLower()
}

$frontendReachable = Test-FrontendReachable -Url $FrontendUrl -TimeoutSeconds $ProbeTimeoutSeconds
$isLocal = Test-LocalUrl -Url $FrontendUrl

# ⚠ 這個布林旗標刻意不叫 $startBackend：PowerShell 變數名不分大小寫，
# 那樣會直接覆寫帶 ValidateSet 的參數 $StartBackend 而觸發驗證錯誤。
$shouldStartBackend = $false
$backendSkipReason = ""
if ($frontendReachable) {
    $backendSkipReason = "already-reachable"
}
elseif ($StartBackend -eq "Never") {
    $backendSkipReason = "disabled"
}
elseif ($StartBackend -eq "IfLocal" -and -not $isLocal) {
    # 前端在 Railway 等遠端時連不上是遠端的事，起本機 backend 沒有意義也幫不上忙。
    $backendSkipReason = "remote-url"
}
else {
    $shouldStartBackend = $true
}

# ---------- 4. uv 偵測（只有真的要啟動東西時才需要） ----------
if (-not $UvPath) {
    $uvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
    if ($uvCommand) { $UvPath = $uvCommand.Source }
}
$uvAvailable = [bool]($UvPath -and (Test-Path -LiteralPath $UvPath))

# ---------- 5. Companion 啟動方式：優先既有排程工作，否則直接起隱藏行程 ----------
$scheduledTask = $null
if ($startCompanion) {
    $scheduledTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
$companionMethod = "none"
if ($startCompanion) {
    if ($scheduledTask) { $companionMethod = "scheduled-task" }
    elseif ($uvAvailable) { $companionMethod = "hidden-process" }
    else { $companionMethod = "unavailable" }
}

# ---------- 6. DryRun：只印計畫，不動系統（自動化測試用） ----------
$plan = [ordered]@{
    project_root       = $ProjectRoot
    state_dir          = $StateDir
    heartbeat_file     = $heartbeatFile
    frontend_url       = $FrontendUrl
    frontend_is_local  = $isLocal
    frontend_reachable = $frontendReachable
    companion_state    = $companion.state
    companion_age_s    = $companion.age
    start_companion    = $startCompanion
    companion_method   = $companionMethod
    start_backend      = $shouldStartBackend
    backend_skip_reason = $backendSkipReason
    start_backend_mode = $StartBackend
    uv_available       = $uvAvailable
    open_browser       = $true
    browser_url        = $FrontendUrl
}

if ($DryRun) {
    Write-Host ("LAUNCH_PLAN " + ($plan | ConvertTo-Json -Compress -Depth 4))
    exit 0
}

# ---------- 7. 實際執行 ----------
Write-Host "[launcher] 前端網址：$FrontendUrl"
Write-Host "[launcher] Companion 狀態：$($companion.state)"

if (-not $startCompanion) {
    Write-Host "[launcher] Companion 已在執行（heartbeat $($companion.age) 秒前更新），跳過啟動。"
}
elseif ($companionMethod -eq "scheduled-task") {
    # 排程工作設了 IgnoreNew，重複 Start 不會產生第二份實例。
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "[launcher] 已啟動排程工作：$TaskName"
}
elseif ($companionMethod -eq "hidden-process") {
    # 沒安裝排程工作時的退路：直接起隱藏視窗的 Companion（關機才停，符合「常駐到關機」）。
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    $logFile = Join-Path $StateDir "ai_bridge.log"
    $companionArgs = @(
        "run", "python", "-m", "backend.app.worker.ai_bridge", "serve",
        "--log-file", $logFile, "--heartbeat-file", $heartbeatFile
    )
    Start-Process -FilePath $UvPath -ArgumentList $companionArgs `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
    Write-Host "[launcher] 已於背景啟動 Companion（隱藏視窗）。"
}
else {
    Write-Warning "找不到排程工作 $TaskName 也找不到 uv，無法啟動 Companion；請先執行 scripts\companion_install.ps1。"
}

if ($shouldStartBackend) {
    if (-not $uvAvailable) {
        Write-Warning "前端連不上且找不到 uv，無法啟動本機 backend。"
    }
    else {
        $uri = [uri]$FrontendUrl
        $port = if ($uri.Port -gt 0) { $uri.Port } else { 8000 }
        $backendArgs = @(
            "run", "python", "-m", "uvicorn", "backend.app.main:app",
            "--host", "127.0.0.1", "--port", "$port"
        )
        Start-Process -FilePath $UvPath -ArgumentList $backendArgs `
            -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
        Write-Host "[launcher] 已於背景啟動本機 backend（port $port），等待可連…"
        $deadline = (Get-Date).AddSeconds($BackendWaitSeconds)
        while ((Get-Date) -lt $deadline) {
            if (Test-FrontendReachable -Url $FrontendUrl -TimeoutSeconds $ProbeTimeoutSeconds) {
                $frontendReachable = $true
                break
            }
            Start-Sleep -Seconds 1
        }
        if (-not $frontendReachable) {
            Write-Warning "本機 backend 在 $BackendWaitSeconds 秒內仍不可連；仍會開瀏覽器，請自行重整。"
        }
    }
}
elseif ($backendSkipReason) {
    Write-Host "[launcher] 不啟動本機 backend（原因：$backendSkipReason）。"
}

# 一律開瀏覽器：Companion／backend 任一環節失敗也讓使用者看得到前端與狀態燈。
Start-Process $FrontendUrl | Out-Null
Write-Host "[launcher] 已開啟瀏覽器：$FrontendUrl"
