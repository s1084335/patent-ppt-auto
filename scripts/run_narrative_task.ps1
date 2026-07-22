# run_narrative_task.ps1 — 報表解讀派工腳本（系統串接原型，v2）
#
# 用途：把「數據＋圖 → headless Claude CLI 解讀 → narratives.json → index 重渲染」
# 整條走系統路徑跑一次。此腳本即未來 Patent Companion 派工 payload 的原型：
# Companion 收到中央 AI 任務後，同樣組這段提示、以 headless `claude -p` 執行、
# 驗收產物、再回呼確定性程式（--refresh-index）完成顯示層更新。
#
# 規格唯一來源：D:\力山\.agents\skills\report-narrative-flow.md
#（prompt 模板 v2、各報表解讀重點、口徑守則、痛點待調查固定文案、narratives.json v2 契約、
#  based_on_version 規則）。本腳本不複製規格內文，只指示 CLI 讀取並遵守。
#
# v2 變更：解讀以變體為單位（variants），
# running header 與缺漏清單使用 variant-key 而非 report_key。
#
# 用法（在專案根目錄執行；機器需已登入 claude CLI）：
#   powershell -File scripts\run_narrative_task.ps1 -RunDir output\full_report_latest\report_trial_YYYYMMDD_HHMMSS
param(
    [Parameter(Mandatory = $true)][string]$RunDir
)

$ErrorActionPreference = 'Stop'

# ---------- 1. 解析目標目錄與版本名（based_on_version ＝ 目錄名） ----------
$runDirAbs = (Resolve-Path $RunDir).Path
$version = Split-Path $runDirAbs -Leaf
$skillPath = 'D:\力山\.agents\skills\report-narrative-flow.md'
$narrativesPath = Join-Path $runDirAbs 'narratives.json'
$reportDataPath = Join-Path $runDirAbs 'report_data.json'

if (-not (Test-Path $reportDataPath)) {
    throw "目標目錄缺 report_data.json：$runDirAbs 不是有效的報表輸出目錄"
}
if (-not (Test-Path $skillPath)) {
    throw "找不到解讀規格 skill 檔：$skillPath"
}

# ---------- 2. 組 headless 任務提示（規格不複製進提示，指示 CLI 讀 skill 全文） ----------
$prompt = @"
任務：產製專利報表解讀 narratives.json（系統派工、非互動、一次性，v2）。

1. 先完整閱讀 $skillPath 全文，逐字遵守其中的解讀 Prompt 模板 v2、各報表解讀重點、
    口徑守則、痛點待調查固定文案（含 {x_median} 實際值代入）與輸出契約 v2。
2. 目標報表目錄：$runDirAbs
3. 讀取該目錄 report_data.json：sections 鍵列出全部卡片與各卡片內的 variants（含
   variant_key，如 L4／L5／default／more／topic_table／opportunity／pain）。對每張
   卡片的「每個變體」「成對」讀取該變體的數據 rows（reports／family_reports／chart_rows
   對應鍵）與該變體的 SVG 圖檔，每一變體產一段解讀文字。
4. 輸出唯一檔案：$narrativesPath
   形狀（v2 引擎讀取契約）：
   {"based_on_version":"$version","reports":{"<report_key>":{"variants":{"<variant_key>":{"text":"...","ai_model":"<實際模型名>","prompt_version":"report_narrative_v2","generated_at":"<ISO8601>"}}}}}
   report_key 取自 sections[].report_key（無則由第一變體檔名推算），variant_key 取自
   各變體的 variant_key 欄。每個 variant_key 各產一段解讀，缺漏清單會列 report_key:variant_key。
5. 只准寫 narratives.json 這一個檔案；不得改動目錄內其他檔案、不得執行 shell 指令；
   寫完即結束，不輸出多餘說明。
"@

# ---------- 3. headless 呼叫（只開放讀寫類工具，限制活動範圍） ----------
Write-Host "[run_narrative_task] headless 派工開始：$version"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
# 保留 headless 指令全文供紀錄（Companion payload 原型）
$cliCommandLine = 'claude -p "<任務提示>" --output-format json --allowedTools "Read" "Glob" "Grep" "Write"'
$cliOutput = & claude -p $prompt --output-format json --allowedTools "Read" "Glob" "Grep" "Write"
$cliExit = $LASTEXITCODE
$sw.Stop()
Write-Host "[run_narrative_task] CLI 結束 exit=$cliExit 耗時=$([math]::Round($sw.Elapsed.TotalSeconds,1))s"
if ($cliExit -ne 0) {
    # 不硬通：完整保留原始輸出供回報
    Write-Host "[run_narrative_task] CLI 原始輸出："
    $cliOutput | ForEach-Object { Write-Host $_ }
    throw "claude CLI 失敗（exit=$cliExit），未產出解讀"
}

# ---------- 4. 驗收產物：narratives.json 存在且 based_on_version 正確 ----------
if (-not (Test-Path $narrativesPath)) {
    Write-Host "[run_narrative_task] CLI 原始輸出："
    $cliOutput | ForEach-Object { Write-Host $_ }
    throw "CLI 正常結束但未產出 $narrativesPath"
}
$narr = Get-Content $narrativesPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($narr.based_on_version -ne $version) {
    throw "narratives.json based_on_version='$($narr.based_on_version)' 與目錄版本 '$version' 不符（解讀過期規則）"
}

# ---------- 5. 重渲染 index（確定性程式嵌入解讀；CLI 不碰 index.html） ----------
$env:UV_CACHE_DIR = '.uv-cache'
$refreshRaw = & uv run python -m backend.app.reports.chart_runner --refresh-index $runDirAbs
if ($LASTEXITCODE -ne 0) {
    $refreshRaw | ForEach-Object { Write-Host $_ }
    throw "--refresh-index 失敗"
}
$refresh = ($refreshRaw -join "`n") | ConvertFrom-Json

# ---------- 6. 輸出摘要（覆蓋變體數／缺漏） ----------
Write-Host "[run_narrative_task] 摘要（v2 變體計數）"
Write-Host "  headless 指令：$cliCommandLine"
Write-Host "  目標版本：$version"
Write-Host "  解讀覆蓋：$($refresh.narrated)/$($refresh.variants_total) 變體（$($refresh.sections) 卡）"
if ($refresh.pending.Count -gt 0) {
    Write-Host "  缺漏 report_key:variant_key：$($refresh.pending -join '、')"
} else {
    Write-Host "  缺漏 report_key:variant_key：無"
}
Write-Host "  index：$(Join-Path $runDirAbs 'index.html')"
