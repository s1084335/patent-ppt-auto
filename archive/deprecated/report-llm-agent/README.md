# archive/deprecated/report-llm-agent — 報表 LLM 調用端（舊路線，已退役）

本目錄已併入統一廢棄目錄 `archive/deprecated/`，保留舊報表 LLM agent 供追溯。

2026-07-16 移入。這些是「報表引擎 → 中間 LLM 迴圈 → 前端」舊消費鏈的產物；
2026-07-15 尾聲目標變更後不再是正式路線，先移到此廢棄目錄（非刪除，可還原）。

## 為何退役

新目標：**正式上線由 Claude Code（agentic CLI）依 SKILL.md 直接調用報表引擎的分析結果去產報告**，
不調用 Claude API、也不需要中間再架一個 LLM 工具迴圈——Claude Code 本身就是那個 agent。
因此下列「讓非 Claude Code 的模型呼叫報表」的中間層變多餘。詳見
`.agents/context/decisions.md`「2026-07-15 ★★ 最終目標變更」。

## 內容

- `report_agent.py`：OpenAI 相容端點（HF router / Qwen3-32B）的 message 形態工具迴圈；
  把 `report_engine.run_reports_batch` 包成 `run_patent_reports` 工具給模型呼叫。
- `claude_report_agent.py`：更早的 anthropic SDK 版轉接殼（當日稍早已改名為 report_agent）。

## 沒有一起搬走的（仍在正式樹）

- **報表引擎本體** `backend/app/reports/`（含 `run_reports_batch`、chart_runner）——是要被 skill 驅動的能力，全留。
- `backend/app/reports/preview_server.py`——臨時前端，命運未定；已改為容錯 import，
  少了本目錄的 report_agent 仍可跑（退化成純手動報表檢視器，AI 問答框停用）。

## 還原方式

把兩個 .py 移回 `backend/app/llm/`，preview_server 的容錯 import 會自動接回 AI 框；
另需確認 `.env` 的 `REPORT_LLM_*` 與 `openai` 依賴仍在（本次退役未動 .env 與 pyproject）。
