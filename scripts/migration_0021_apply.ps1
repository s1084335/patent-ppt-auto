<#
.SYNOPSIS
    0021 derived+app 併表正式套用流程（停機 → 備份 → upgrade → 驗收 → 異常回滾）。

.DESCRIPTION
    將 0018→0021 的 migration 以可重複、可回滾方式套到指定資料庫。
    預設目標為拋棄式 rehearsal 庫，「絕不預設打到正式庫 patent_ppt」；
    要套正式庫必須顯式帶 -Database patent_ppt 並加 -ConfirmProduction。

    流程：
      1) 停 backend/worker（避免套用期間有寫入）。
      2) 全量備份目標庫（呼叫 db_backup.ps1）。
      3) alembic upgrade head（PGDATABASE=目標庫）。
      4) 驗收：結構不變式（app/derived 各 3 實體表、legacy_0021 14 表、
         clustering run 無 NULL workspace_id）＋資料保存（workflow_runs、
         topic_assignments、report_* VIEW 筆數對照套用前來源）。
      5) 任何一步失敗 → 自動 alembic downgrade 回 $BaselineRevision，並提示可由備份還原。
      6) 重啟 backend/worker。

    本腳本只負責建檔與被呼叫時執行；不由 CI 或其他流程自動對正式庫執行。

.NOTES
    驗收採「套用前 vs 套用後」對照，故不綁定特定資料集數字；
    2026-07-21 rehearsal 實測快照對照值為 workflow_runs=43、topic_assignments=1601、
    report VIEW 932/154/861、clustering workspace NOT NULL=18，可作人工複核參考。
#>
[CmdletBinding()]
param(
    # 預設為拋棄式 rehearsal 庫，避免誤打正式庫
    [string]$Database = "patent_ppt_rehearsal",
    [string]$User = "postgres",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 5433,
    [string]$PgBin = "D:\PostgreSQL\18\bin",
    [string]$BaselineRevision = "0018_compose_created_at_comment",
    # docker compose 服務名（停機／重啟寫入端）
    [string[]]$AppServices = @("backend", "worker"),
    # 只有顯式加此旗標才允許對正式庫 patent_ppt 套用
    [switch]$ConfirmProduction
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$psql = Join-Path $PgBin "psql.exe"

# ── 安全護欄：正式庫必須顯式確認 ────────────────────────────────
if ($Database -eq "patent_ppt" -and -not $ConfirmProduction) {
    throw "拒絕：目標為正式庫 patent_ppt 但未帶 -ConfirmProduction。請先在 rehearsal 庫演練。"
}
if (-not (Test-Path -LiteralPath $psql)) { throw "psql.exe not found: $psql" }
if (-not $env:PGPASSWORD) {
    $userPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "User")
    if ($userPassword) { $env:PGPASSWORD = $userPassword }
}

# psql 純量查詢輔助：-t -A 取單值，ON_ERROR_STOP 讓錯誤直接中止
function Invoke-Scalar([string]$Sql) {
    $out = & $psql -w -h $HostName -p $Port -U $User -d $Database -v ON_ERROR_STOP=1 -t -A -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql 查詢失敗（exit $LASTEXITCODE）：$Sql" }
    return ($out | Select-Object -First 1).Trim()
}

function Assert-Equal([string]$Name, $Expected, $Actual) {
    if ("$Expected" -ne "$Actual") {
        throw "驗收失敗 [$Name]：期望 $Expected，實得 $Actual"
    }
    Write-Host ("  PASS {0} = {1}" -f $Name, $Actual)
}

# alembic 對目標庫執行（顯式設 PGDATABASE，永不指向他庫）
function Invoke-Alembic([string]$Args) {
    Push-Location $ProjectRoot
    try {
        $env:PGDATABASE = $Database
        $prevUrl = $env:DATABASE_URL
        Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
        & uv run alembic $Args.Split(" ")
        $code = $LASTEXITCODE
        if ($prevUrl) { $env:DATABASE_URL = $prevUrl }
        if ($code -ne 0) { throw "alembic $Args 失敗（exit $code）" }
    } finally {
        Pop-Location
    }
}

Write-Host "=== 目標庫：$Database（baseline=$BaselineRevision）==="

# ── 1) 停機 ──────────────────────────────────────────────────
Write-Host "1) 停 $($AppServices -join ',')"
& docker compose stop @AppServices
if ($LASTEXITCODE -ne 0) { throw "docker compose stop 失敗" }

$dumpFile = $null
try {
    # ── 2) 套用前來源計數（供資料保存驗收）＋全量備份 ───────────
    Write-Host "2) 擷取套用前來源計數並備份"
    $preHead = Invoke-Scalar "SELECT version_num FROM alembic_version;"
    Assert-Equal "pre_head" $BaselineRevision $preHead

    # workflow_runs 目標數 = 4 個來源列數合計（processing_jobs+analysis_runs+company_normalization_tasks+topic_runs）
    $expWorkflowRuns = Invoke-Scalar @"
SELECT (SELECT count(*) FROM app_layer.processing_jobs)
     + (SELECT count(*) FROM app_layer.analysis_runs)
     + (SELECT count(*) FROM app_layer.company_normalization_tasks)
     + (SELECT count(*) FROM derived_layer.topic_runs);
"@
    # topic_assignments 目標數 = 舊表 distinct(assigned_run_id,patent_id)
    $expTopicAssign = Invoke-Scalar "SELECT count(*) FROM (SELECT DISTINCT assigned_run_id, patent_id FROM derived_layer.topic_assignments) s;"
    $expRptBase    = Invoke-Scalar "SELECT count(*) FROM derived_layer.report_patent_base;"
    $expRptCountry = Invoke-Scalar "SELECT count(*) FROM derived_layer.report_family_country;"
    $expRptQuality = Invoke-Scalar "SELECT count(*) FROM derived_layer.report_family_quality;"
    Write-Host ("  來源對照：workflow_runs={0} topic_assignments={1} report={2}/{3}/{4}" -f `
        $expWorkflowRuns, $expTopicAssign, $expRptBase, $expRptCountry, $expRptQuality)

    $dumpFile = & (Join-Path $PSScriptRoot "db_backup.ps1") -Database $Database -User $User `
        -HostName $HostName -Port $Port -PgBin $PgBin | Select-Object -Last 1
    Write-Host "  備份：$dumpFile"

    # ── 3) upgrade head ─────────────────────────────────────
    Write-Host "3) alembic upgrade head"
    Invoke-Alembic "upgrade head"
    Assert-Equal "post_head" "0021_derived_app_consolidation" (Invoke-Scalar "SELECT version_num FROM alembic_version;")

    # ── 4) 驗收 ─────────────────────────────────────────────
    Write-Host "4) 驗收（結構不變式＋資料保存）"
    Assert-Equal "app_base_tables" 3 (Invoke-Scalar "SELECT count(*) FROM information_schema.tables WHERE table_schema='app_layer' AND table_type='BASE TABLE';")
    Assert-Equal "derived_base_tables" 3 (Invoke-Scalar "SELECT count(*) FROM information_schema.tables WHERE table_schema='derived_layer' AND table_type='BASE TABLE';")
    Assert-Equal "legacy_0021_tables" 14 (Invoke-Scalar "SELECT count(*) FROM information_schema.tables WHERE table_schema='legacy_0021' AND table_type='BASE TABLE';")
    Assert-Equal "clustering_ws_null" 0 (Invoke-Scalar "SELECT count(*) FROM app_layer.workflow_runs WHERE run_type LIKE 'clustering:%' AND workspace_id IS NULL;")
    Assert-Equal "workflow_runs" $expWorkflowRuns (Invoke-Scalar "SELECT count(*) FROM app_layer.workflow_runs;")
    Assert-Equal "topic_assignments" $expTopicAssign (Invoke-Scalar "SELECT count(*) FROM derived_layer.topic_assignments;")
    Assert-Equal "report_patent_base" $expRptBase (Invoke-Scalar "SELECT count(*) FROM derived_layer.report_patent_base;")
    Assert-Equal "report_family_country" $expRptCountry (Invoke-Scalar "SELECT count(*) FROM derived_layer.report_family_country;")
    Assert-Equal "report_family_quality" $expRptQuality (Invoke-Scalar "SELECT count(*) FROM derived_layer.report_family_quality;")

    Write-Host "=== 0021 套用並驗收成功：$Database ==="
}
catch {
    Write-Warning "套用流程失敗：$($_.Exception.Message)"
    Write-Warning "嘗試 alembic downgrade 回 $BaselineRevision（就地回滾）"
    try {
        Invoke-Alembic "downgrade $BaselineRevision"
        Write-Warning "已 downgrade 回 $BaselineRevision。若資料仍異常，請由備份還原：$dumpFile"
    }
    catch {
        Write-Error "downgrade 亦失敗，請立即由備份還原：$dumpFile。錯誤：$($_.Exception.Message)"
    }
    throw
}
finally {
    # ── 6) 重啟寫入端 ───────────────────────────────────────
    Write-Host "6) 重啟 $($AppServices -join ',')"
    & docker compose start @AppServices
}
