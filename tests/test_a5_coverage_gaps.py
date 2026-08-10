"""補上 A5 量到的覆蓋缺口（2026-08-09）。

`scripts/verify_module.py` 以 A 輪起點為基準量出新增行覆蓋率 57%，未達 90% 門檻。
未覆蓋的集中在三處，都是**只有失敗路徑或分支條件**沒被走到：

- `planning_contracts` 的要點守門（條數／字數超限）
- `report_planning_runner` 的稽核落檔 helper（含讀不到檔的退回路徑）
- `build_ppt` 的 kp 版型「無圖走要點頁」分支

⚠ 這些不是為了衝數字補的空測試——每一支對應一條**目前沒有任何測試走過**的
分支，而那些分支正是本輪修掉缺陷的地方（守門、稽核、版型降級）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import backend.app.reports.planning_contracts as pc
from backend.app.reports.planning_contracts import (
    MAX_POINT_CHARS,
    MAX_POINTS_PER_SLIDE,
    validate_slide_plan,
)
from backend.app.worker import report_planning_runner as rpr

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_ppt_a5", SKILL_DIR / "scripts" / "build_ppt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_ppt_a5", module)
    spec.loader.exec_module(module)
    return module


bp = _load_builder()


def _slide(points: list[dict], preset: str = "exec_summary") -> dict:
    return {"plan_id": "p", "slides": [{
        "slide_id": "s1", "purpose": "測試", "layout_preset": preset,
        "chart_identities": [], "narrative": points}]}


class PointGuardTests(unittest.TestCase):
    """要點容量：超限要**現形但不阻擋**（2026-08-10 契約變更）。

    🔴 原本是 errors（整份規劃失敗）。連續三次實測（job 278／279／280）都卡在這，
    而且每次都是同一種自相矛盾：prompt 要求寫深寫滿（無圖頁至少 6 個單元、
    direction 頁「方向｜依據｜行動」三段），守門卻按舊尺度打回。

    改為警告的三個理由：
    ① 組版端 `_trim_blocks` 依實際容量自動截斷，超標不會爆版；
    ② 代價不成比例——擋下來＝整份 PPT 產不出來，不擋＝某條要點被截短；
    ③ 上限本來就是**建議值**，真正的容量由版型決定（同一份 plan 放進不同版型
       容量差好幾倍），在規劃階段用單一數字硬擋本來就不精確。
    """

    def test_too_many_points_warned_not_blocked(self):
        plan = _slide([{"text": "短"}] * (MAX_POINTS_PER_SLIDE + 1))
        self.assertEqual(validate_slide_plan(plan, set()), [],
                         "條數超標不得阻擋整份規劃")
        self.assertTrue(any("條要點" in w for w in pc.slide_plan_capacity_warnings(plan)))

    def test_too_long_point_warned_not_blocked(self):
        plan = _slide([{"text": "長" * (MAX_POINT_CHARS + 1)}])
        self.assertEqual(validate_slide_plan(plan, set()), [],
                         "字數超標不得阻擋整份規劃")
        self.assertTrue(any("字" in w for w in pc.slide_plan_capacity_warnings(plan)))

    def test_within_limits_passes(self):
        """⚠ 對照組：剛好在上限內不得誤報（守門太緊會讓 job 整個失敗）。"""
        plan = _slide([{"text": "長" * MAX_POINT_CHARS}] * MAX_POINTS_PER_SLIDE)
        errors = [e for e in validate_slide_plan(plan, set())
                  if "要點" in e or "超過上限" in e]
        self.assertEqual(errors, [])


# ⚠ 2026-08-10：稽核開檔／讀回已從 report_planning_runner 移到
# mcp_server.report_research（AUDIT_PATH_ENV 的定義處），供解讀線與規劃線共用
# ——複製第二份會讓兩條線的稽核格式各自演進，而不一致本身不會報錯。
from backend.app.mcp_server import report_research as rrs  # noqa: E402


class QueryAuditFileTests(unittest.TestCase):
    """稽核落檔 helper：環境變數設定、還原與讀不到檔的退回。"""

    def test_env_set_inside_and_restored_after(self):
        import os

        from backend.app.mcp_server.report_research import AUDIT_PATH_ENV

        before = os.environ.get(AUDIT_PATH_ENV)
        with rrs.query_audit_file() as path:
            self.assertEqual(os.environ.get(AUDIT_PATH_ENV), str(path))
            self.assertTrue(path.exists())
        self.assertEqual(os.environ.get(AUDIT_PATH_ENV), before,
                         "離開後要還原環境變數，不得污染後續任務")

    def test_temp_file_removed_after(self):
        with rrs.query_audit_file() as path:
            recorded = path
        self.assertFalse(recorded.exists(), "暫存檔要刪掉")

    def test_read_returns_entries(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.jsonl"
            target.write_text(
                json.dumps({"tool": "x"}) + "\n" + json.dumps({"tool": "y"}) + "\n",
                encoding="utf-8")
            self.assertEqual([e["tool"] for e in rrs.read_query_audit(target)], ["x", "y"])

    def test_read_skips_broken_lines(self):
        """⚠ 半行壞掉不得讓整份稽核消失。"""
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.jsonl"
            target.write_text('{"tool": "x"}\n{壞掉\n{"tool": "y"}\n', encoding="utf-8")
            self.assertEqual([e["tool"] for e in rrs.read_query_audit(target)], ["x", "y"])

    def test_read_missing_file_returns_empty(self):
        """⚠ 稽核缺失不得讓規劃失敗——讀不到就回空清單。"""
        self.assertEqual(rrs.read_query_audit(Path("Z:/nope/none.jsonl")), [])


class KpPresetFallbackTests(unittest.TestCase):
    """kp 版型在沒有選圖時走要點頁，不降級（A4 修的那條）。"""

    PRESETS = ("kp_deepdive", "kp_cards", "kp_compare")

    def test_no_chart_uses_points_page(self):
        for preset in self.PRESETS:
            with self.subTest(preset=preset):
                self.assertIn(preset, bp.POINTS_PAGE_PANEL_TITLES,
                              "無圖時要有自己的面板標題，不能落到通用「重點」")

    def test_kp_deepdive_not_chart_dependent(self):
        """⚠ 它的內容是單一 KP 的數字與軌跡，本來就不需要選圖。"""
        self.assertNotIn("kp_deepdive", bp.CHART_DEPENDENT_KINDS)
        self.assertNotIn("kp_cards", bp.CHART_DEPENDENT_KINDS)
        self.assertNotIn("kp_compare", bp.CHART_DEPENDENT_KINDS)

    def test_quadrant_still_chart_dependent(self):
        """⚠ 對照組：象限圖沒有圖就真的畫不出東西，它該留在清單裡。"""
        self.assertIn("kp_quadrant", bp.CHART_DEPENDENT_KINDS)



class RemainingBranchTests(unittest.TestCase):
    """把剩下**不需要真 DB**的分支補齊。

    ⚠ 需要真實資料庫的路徑（query_database 的執行、_fetch_workspace_name、
    query_patents.run_query）不在此補——那些要連 DB 才走得到，改用實測腳本
    驗證（見 A6 的 verify_a6.py：四種寫入全被拒、逾時生效）。
    """

    def test_geometry_fields_rejected(self):
        """CLI 不得輸出座標／字級／色彩——排版由 builder 決定。"""
        plan = _slide([{"text": "短"}])
        plan["slides"][0]["left_in"] = 1.2
        errors = validate_slide_plan(plan, set())
        self.assertTrue(any("幾何欄位" in e for e in errors), errors)

    def test_failed_snapshot_query_is_audited_then_raised(self):
        """查詢不合契約時要**先留痕再拋**——只記成功的話會像從來沒查過。"""
        from backend.app.mcp_server import report_research as rr

        rr.reset_query_audit()
        with self.assertRaises(rr.ReportResearchError):
            rr.query_report_evidence("不存在的報表", "v1")
        audit = rr.get_query_audit()
        self.assertEqual(len(audit), 1)
        self.assertTrue(audit[0]["error"])

    def test_kp_presets_use_chart_layout_when_charts_present(self):
        """⚠ 對照組：有選圖時 kp 三種版型都走各自的圖版型，不是一律變要點頁。"""
        cases = [
            ("_render_kp_deepdive", "_render_chart_with_points"),
            ("_render_kp_cards", "_render_table_with_points"),
            ("_render_kp_compare", "_render_comparison"),
        ]
        for renderer_name, delegate_name in cases:
            with self.subTest(preset=renderer_name):
                calls = []
                original = getattr(bp, delegate_name)
                setattr(bp, delegate_name, lambda *a, **k: calls.append("chart"))
                try:
                    spec = bp.PageSpec(page=1, kind="x", title="t", topic="t",
                                       report_keys=("lifecycle",), charts=("lifecycle.svg",))
                    getattr(bp, renderer_name)(None, None, spec, {})
                finally:
                    setattr(bp, delegate_name, original)
                self.assertEqual(calls, ["chart"], f"{renderer_name} 有圖時該走圖版型")


if __name__ == "__main__":
    unittest.main()
