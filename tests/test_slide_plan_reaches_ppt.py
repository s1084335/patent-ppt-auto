"""SlidePlan 必須回存 DB，否則 goal-driven 整條路徑是斷的（2026-08-10 實測）。

## 實測到的斷鏈

`ai:report_plan` 產出 SlidePlan 後寫進 `<run_dir>/report_data.json` 的 `slide_plan`
鍵，**但沒有回存 `report_artifacts`**。而下游 `ai:report_ppt` 走
`ai_narrative_runner.resolve_run_dir`——本機找不到該版本目錄時從 DB
`materialize_report_version` 還原，於是拿到**沒有 slide_plan 的舊 report_data.json**，
`resolve_layout` 找不到 plan 就靜默退回固定頁序。

實測結果（job 254 → 255）：

| | 內容 |
|---|---|
| SlidePlan 規劃 | 11 頁：cover → exec_summary → … → **kp_quadrant** → reading_guide |
| 實際產出 | 14 頁：cover → chart_hero ×6 → … → direction → 附錄 ×3 |

且 manifest 的 `missing_reports` 含 `applicant_strength_profile`——Key Player 象限圖
**整個沒進 PPT**。⚠ 沒有任何錯誤訊息，因為「找不到 plan 就用固定頁序」是設計上的
保底行為；斷鏈與「使用者沒規劃」在下游看起來一模一樣。

## 為什麼是回存而不是改讀取端

跨容器（2026-07-23 定案）：Railway 上 worker 與 backend 檔案系統不共享。
`report_artifacts` 就是為此存在的傳輸媒介，`upload_run_dir` 是它的唯一寫入入口。
讓下游改讀本機檔案等於把已解決的問題再打開一次。
"""
from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AI_BRIDGE = PROJECT_ROOT / "backend" / "app" / "worker" / "ai_bridge.py"


def _plan_job_source() -> str:
    """取 `_run_ai_report_plan_job` 的函式本體原始碼。"""
    source = AI_BRIDGE.read_text(encoding="utf-8")
    start = source.index("def _run_ai_report_plan_job")
    end = source.index("\n_AI_JOB_RUNNERS", start)
    return source[start:end]


class SlidePlanPersistenceTests(unittest.TestCase):
    """規劃結果要跨容器活下來，下游才拿得到。"""

    def test_plan_job_uploads_after_writing_report_data(self):
        """寫完 report_data.json 必須 upload_run_dir，否則 DB 版本沒有 slide_plan。"""
        body = _plan_job_source()
        self.assertIn(
            "upload_run_dir", body,
            "ai:report_plan 寫回 slide_plan 後必須回存 report_artifacts——"
            "下游 ai:report_ppt 從 DB materialize，只寫本機檔案它永遠讀不到",
        )

    def test_upload_happens_after_the_write(self):
        """順序契約：先寫檔再上傳。反過來會上傳到舊內容。"""
        body = _plan_job_source()
        self.assertLess(
            body.index("write_text"), body.index("upload_run_dir"),
            "upload_run_dir 必須在 report_data.json 寫入之後",
        )

    def test_uses_the_single_upload_entry(self):
        """沿用唯一寫入入口 `report_artifact_store.upload_run_dir`，不自造第二條路。"""
        body = _plan_job_source()
        self.assertIn("report_artifact_store", body,
                      "回存一律走 report_artifact_store，不得自行寫 SQL 或另開存取層")


if __name__ == "__main__":
    unittest.main()
