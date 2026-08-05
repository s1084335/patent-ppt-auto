"""R-1（題目卡字數上限失真）／R-5（PPT 接續斷鏈）的 regression 測試。

R-1：`report_ppt_content_rules.md` 寫死「basis／action 各 ≤20 字」，那個 20 是
**12.5pt 時代**由卡片寬 3.5in ÷ (12.5/72)=20.1 字算出來的。K-10 把 topic_text_pt
改 16pt 後每行只剩 3.5 ÷ (16/72)=15.7 字、再扣「依據｜」3 字＝12.7 字，
於是 20 字必被 `_fit_text` 截成「…」（實機 p19「行動｜需先做權利範圍…」）。
治法＝上限**由幾何推導**（direction_capacity）並注入提示，改字級自動跟著動。

R-5：解讀完成後由**瀏覽器輪詢**才排 PPT——關分頁就斷鏈（實測 #200 完成後無 #201）。
治法＝narrative job 成功後由 worker 端接續派工，前端輪詢只負責顯示。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "patent-report-ppt" / "scripts"))


class R1DirectionCapacityTests(unittest.TestCase):
    def setUp(self):
        import build_ppt as B

        self.B = B
        self.theme = B.Theme.load()

    def test_capacity_matches_card_geometry(self):
        """每段可寫字數＝(卡片文字寬 ÷ 字寬 × 行數) − 標籤成本，全部由 theme 推導。"""
        cap = self.B.direction_capacity(self.theme)
        g = self.theme.geometry["direction_flow"]
        size = self.theme.size("topic_text_pt")
        per_line = int((g["topic_width_in"] - g["topic_inset_in"] * 2) / (size / 72.0))
        self.assertEqual(cap["topic_detail_per_line"], per_line)
        self.assertGreaterEqual(cap["topic_detail_max_chars"], 1)
        # 標籤「依據｜」佔 3 字，必須被扣掉
        self.assertLessEqual(cap["topic_detail_max_chars"],
                             per_line * cap["topic_detail_lines"] - 3)

    def test_capacity_shrinks_when_font_grows(self):
        """字級調大→上限自動變小（這正是寫死 20 失真的那個環節）。"""
        big = self.B.direction_capacity(self.theme, topic_text_pt=20.0)
        small = self.B.direction_capacity(self.theme, topic_text_pt=12.5)
        self.assertLess(big["topic_detail_max_chars"], small["topic_detail_max_chars"])

    def test_rules_doc_no_longer_hardcodes_20(self):
        """規範檔不得再寫死字數——要指向任務提示給的容量。"""
        doc = (ROOT / "skills" / "patent-report-ppt"
               / "report_ppt_content_rules.md").read_text(encoding="utf-8")
        self.assertNotIn("各 ≤20 字", doc)
        self.assertIn("容量", doc)

    def test_prompt_carries_capacity(self):
        """PPT 文案提示必須帶入實際容量數字，CLI 才知道能寫幾個字。"""
        from backend.app.worker import ai_report_ppt_runner as R

        payload = R.build_report_ppt_payload({"sections": []}, ["direction.body"])
        text = str(payload)
        self.assertIn("topic_detail_max_chars", text)

    def test_overlong_detail_is_flagged(self):
        """超出容量要記 warning（只寫在提示裡沒有程式驗證＝沒有規則）。"""
        cap = self.B.direction_capacity(self.theme)
        limit = cap["topic_detail_max_chars"]
        body = {
            "situation": ["一"], "opportunity": ["二"], "direction": ["三"],
            "topics": [{"name": "測試題目", "basis": "依" * (limit + 5), "action": "行"}],
            "conclusion": "結論",
        }
        warnings = self.B.validate_direction_body(self.theme, body)
        self.assertTrue(any("basis" in w for w in warnings), warnings)
        ok = {**body, "topics": [{"name": "測試題目", "basis": "依" * limit, "action": "行"}]}
        self.assertEqual(self.B.validate_direction_body(self.theme, ok), [])


class R5ServerSideChainTests(unittest.TestCase):
    """解讀成功後由 worker 端接續派 PPT——關瀏覽器不得斷鏈。"""

    def test_narrative_success_enqueues_ppt_when_flagged(self):
        from backend.app.worker import handlers

        summary = {"based_on_version": "report_trial_X", "narrated": 3}
        with mock.patch("backend.app.db.job_repository.create_job") as create:
            handlers._enqueue_chained_report_ppt(
                {"then_export_ppt": True, "workspace_id": 3}, summary)
        create.assert_called_once()
        job_type, payload = create.call_args[0][:2]
        self.assertEqual(job_type, "ai:report_ppt")
        self.assertEqual(payload["based_on_version"], "report_trial_X")

    def test_no_flag_no_chain(self):
        from backend.app.worker import handlers

        with mock.patch("backend.app.db.job_repository.create_job") as create:
            handlers._enqueue_chained_report_ppt({}, {"based_on_version": "v"})
        create.assert_not_called()

    def test_chain_failure_is_isolated(self):
        """接續派工失敗不得讓已成功的解讀變 failed。"""
        from backend.app.worker import handlers

        with mock.patch("backend.app.db.job_repository.create_job",
                        side_effect=RuntimeError("db down")):
            handlers._enqueue_chained_report_ppt(
                {"then_export_ppt": True}, {"based_on_version": "v"})  # 不得 raise

    def test_api_declares_flag(self):
        """⚠ Pydantic 對未知欄位靜默忽略——旗標沒宣告就永遠傳不到 worker。"""
        from backend.app.api.ai_tasks import CreateAiTaskRequest

        req = CreateAiTaskRequest(task_type="ai:narrative", then_export_ppt=True)
        self.assertTrue(req.then_export_ppt)

    def test_frontend_poll_does_not_double_enqueue(self):
        """前端輪詢看到解讀成功時**不得再送一次** PPT——兩邊都派會產生兩個任務。"""
        html = (ROOT / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")
        start = html.index("async function pollNarrativeThenExportPpt")
        block = html[start:start + 1200]
        self.assertNotIn("requestExportPpt({ skipNarrativeCheck: true })", block)

    def test_frontend_sends_flag(self):
        html = (ROOT / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")
        idx = html.index("runNarrativeThenExportPpt")
        self.assertIn("then_export_ppt", html[idx:idx + 2000],
                      "前端沒送旗標，worker 端接續等於沒有觸發條件")


if __name__ == "__main__":
    unittest.main()
