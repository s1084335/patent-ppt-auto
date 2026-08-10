"""P2 第 4 節：goal-driven 規劃 runner（tasks 4.1–4.5）。

流程：ReportBrief ＋ 選圖資料包 → 組 prompt（含 MCP 工具說明）→ headless CLI
→ 解析 ReportStrategy／SlidePlan／EvidenceManifest → 三道驗證 → 回傳候選。

⚠ runner 是**唯一保存者**：CLI 沒有任何 DB／artifact 寫入工具，產出一律由
runner 驗證後才落檔（design.md 第 4 點）。
"""
from __future__ import annotations

import json
import unittest
from typing import Any

from backend.app.worker import report_planning_runner as rp


BUNDLES = [
    {"chart_identity": "applicant_ranking:default", "report_key": "applicant_ranking",
     "variant_key": "default", "title": "主要申請人排名",
     "image_path": "/work/charts/applicant_ranking.svg",
     "data_rows": [{"applicant_display_name": "曾晴", "patent_count": 14}],
     "population_note": "母體 55/55 件", "version": "v1", "checksum": "c1"},
    {"chart_identity": "lifecycle:default", "report_key": "lifecycle",
     "variant_key": "default", "title": "專利狀態分析",
     "image_path": "/work/charts/lifecycle.svg",
     "data_rows": [{"applicant_display_name": "曾晴", "已授權": 11}],
     "population_note": "母體 68/55 件", "version": "v1", "checksum": "c2"},
]
BRIEF = {"north_star_goal": "找出可切入的技術空白", "audience": "研發主管",
         "page_budget": 6, "workspace_id": 3, "snapshot_id": "v1",
         "selected_charts": BUNDLES}


def _good_reply() -> str:
    return json.dumps({
        "strategy": {"north_star_goal": "找出可切入的技術空白",
                     "storyline": ["現況", "競爭", "空白"]},
        "slides": [
            {"slide_id": "s1", "layout_preset": "chart_hero", "purpose": "競爭格局",
             "chart_identities": ["applicant_ranking:default"],
             "narrative": [{"text": "曾晴 14 件居首", "evidence_ref": "e1"}]},
            {"slide_id": "s2", "layout_preset": "chart_hero", "purpose": "權利存續",
             "chart_identities": ["lifecycle:default"],
             "narrative": [{"text": "多數已授權", "evidence_ref": "e2"}]},
        ],
        "evidence": {
            "e1": {"source": "selected_chart", "chart_identity": "applicant_ranking:default",
                   "snapshot_id": "v1"},
            "e2": {"source": "selected_chart", "chart_identity": "lifecycle:default",
                   "snapshot_id": "v1"},
        },
    }, ensure_ascii=False)


def _cli_with_audit(reply: str, calls: list[str] | None = None):
    """回傳一個 fake cli_runner，並在被呼叫時寫一筆查詢稽核。

    ⚠ 2026-08-10 契約新增：`validate_research_effort` 要求規劃**必須實際查證過**
    （使用者定案「不能讓它可以不去查資料庫就直接寫」）。真實 CLI 經 report-research
    MCP 查詢時會把每次查詢寫進稽核 JSONL，runner 讀回來驗證；fake runner 不寫就會被
    當成「完全沒查」而擋下。

    這不是為了讓測試變綠而放寬斷言——是**補上 fake 缺少的真實行為**：原本的 fake
    只模擬了「CLI 會回一段 JSON」，沒模擬「CLI 會查資料庫」。新契約要求後者，
    fake 就得跟上。
    """
    import os
    from pathlib import Path

    from backend.app.mcp_server.report_research import AUDIT_PATH_ENV

    def cli(prompt: str, **kwargs):
        if calls is not None:
            calls.append(prompt)
        audit_path = os.environ.get(AUDIT_PATH_ENV)
        if audit_path:
            with Path(audit_path).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(
                    {"tool": "query_patents", "status": "ok", "rows": 3},
                    ensure_ascii=False) + "\n")
        return reply

    return cli


def _run(reply: str, brief: dict[str, Any] | None = None):
    calls: list[str] = []
    result = rp.run_report_planning(
        brief=brief or BRIEF, cli_runner=_cli_with_audit(reply, calls))
    return result, calls


class PromptTests(unittest.TestCase):
    def test_prompt_carries_goal_charts_and_tools(self):
        _, calls = _run(_good_reply())
        prompt = calls[0]
        self.assertIn("找出可切入的技術空白", prompt)
        self.assertIn("applicant_ranking:default", prompt)
        self.assertIn("曾晴", prompt, "結構化數據要進 prompt（不能只給圖）")
        self.assertIn("母體 55/55 件", prompt, "母體口徑要給，否則判讀會誤述")
        self.assertIn("query_report_evidence", prompt, "要告訴 CLI 可用哪些查證工具")

    def test_prompt_states_page_budget_and_layout_presets(self):
        _, calls = _run(_good_reply())
        self.assertIn("6", calls[0])
        self.assertIn("kp_quadrant", calls[0], "版型清單要給，CLI 才知道能選什麼")

    def test_invalid_brief_fails_before_calling_cli(self):
        bad = dict(BRIEF, north_star_goal="")
        with self.assertRaises(rp.ReportPlanningError):
            _run(_good_reply(), brief=bad)


class ValidationTests(unittest.TestCase):
    def test_valid_plan_accepted(self):
        result, _ = _run(_good_reply())
        self.assertEqual(result["slides"][0]["slide_id"], "s1")
        self.assertEqual(result["validation_errors"], [])
        self.assertEqual(result["plan_id"], result["plan"]["plan_id"])

    def test_missing_selected_chart_rejected(self):
        reply = json.loads(_good_reply())
        reply["slides"] = reply["slides"][:1]      # 少用 lifecycle
        with self.assertRaises(rp.ReportPlanningError) as ctx:
            _run(json.dumps(reply, ensure_ascii=False))
        self.assertIn("未使用", str(ctx.exception))

    def test_geometry_in_plan_rejected(self):
        reply = json.loads(_good_reply())
        reply["slides"][0]["font_pt"] = 18
        with self.assertRaises(rp.ReportPlanningError) as ctx:
            _run(json.dumps(reply, ensure_ascii=False))
        self.assertIn("幾何", str(ctx.exception))

    def test_number_without_evidence_is_not_rejected(self):
        """帶數字但沒 evidence_ref → **不擋**（2026-08-10 契約變更）。

        🔴 原本擋。判斷「有沒有數字」用的是 `\\d` 正規表示式，**年份也算**——
        實跑 job 270 被「2025後回落多屬公開遲延、非真衰退」擋下，而那是判讀句。
        誤擋的代價是整份規劃失敗；防造假改由 narrative 溯源與 query_audit 承擔。
        """
        reply = json.loads(_good_reply())
        reply["slides"][0]["narrative"][0].pop("evidence_ref")
        result, _ = _run(json.dumps(reply, ensure_ascii=False))
        self.assertTrue(result["slides"], "不得因缺 evidence_ref 而整份失敗")

    def test_stale_snapshot_evidence_rejected(self):
        reply = json.loads(_good_reply())
        reply["evidence"]["e1"]["snapshot_id"] = "v0"
        with self.assertRaises(rp.ReportPlanningError) as ctx:
            _run(json.dumps(reply, ensure_ascii=False))
        self.assertIn("snapshot", str(ctx.exception))

    def test_page_budget_enforced(self):
        reply = json.loads(_good_reply())
        base = reply["slides"][0]
        reply["slides"] = [dict(base, slide_id=f"s{i}") for i in range(8)]
        reply["slides"][-1]["chart_identities"] = ["lifecycle:default"]
        with self.assertRaises(rp.ReportPlanningError) as ctx:
            _run(json.dumps(reply, ensure_ascii=False))
        self.assertIn("page_budget", str(ctx.exception))

    def test_non_json_reply_fails_loud(self):
        with self.assertRaises(rp.ReportPlanningError):
            _run("這裡沒有 JSON")


class NoWriteCapabilityTests(unittest.TestCase):
    def test_runner_is_sole_persister(self):
        """CLI 沒有寫入工具：runner 才呼叫 persister，且只在驗證通過後。"""
        saved: list[dict[str, Any]] = []
        rp.run_report_planning(brief=BRIEF, cli_runner=_cli_with_audit(_good_reply()),
                               persister=lambda payload: saved.append(payload))
        self.assertEqual(len(saved), 1)
        self.assertIn("plan", saved[0])

    def test_failed_validation_persists_nothing(self):
        saved: list[dict[str, Any]] = []
        reply = json.loads(_good_reply())
        reply["slides"][0]["layout_preset"] = "自創版型"
        with self.assertRaises(rp.ReportPlanningError):
            rp.run_report_planning(brief=BRIEF,
                                   cli_runner=lambda p, **k: json.dumps(reply, ensure_ascii=False),
                                   persister=lambda payload: saved.append(payload))
        self.assertEqual(saved, [], "驗證未過不得留下候選 artifact")


if __name__ == "__main__":
    unittest.main()
