"""無圖 variant 不得被當成可選圖表（2026-08-10 實測缺陷）。

動因：`cluster_topic_table` 的 `topic_table_tech`／`topic_table_effect` 兩個 variant
**刻意沒有圖檔**（`file` 為空字串），只是解讀（narrative）的掛點——主題統計表是
表格，由前端／PPT 自己畫。見 `chart_runner.py` `_build_cluster_analytics_section`。

但 `build_selected_bundles` 的存在性檢查踩空：

    source = run_dir / meta["file"]   # run_dir / "" == run_dir 本身
    if not source.exists():           # 目錄存在 → True，防禦失效
    shutil.copy2(source, target)      # 對目錄做 copy2

實測結果是 `PermissionError: [Errno 13] Permission denied: 'output\\report_trial_...'`
——訊息完全看不出真因。

🔴 而前端 `loadPptChartPicker` **預設全選**（`checked`），使用者按下 PPT 按鈕
必然送出這兩個無圖 variant，也就是**正式預設路徑就是壞的**。

兩處都要守：前端不列出無圖 variant（治源頭），`build_selected_bundles` 對空 `file`
明確報錯（API 可被直接呼叫，防禦不能只靠前端）。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports.chart_bundle import ChartBundleError, build_selected_bundles

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"

# 最小 report_data：一個有圖的 variant ＋ 一個無圖的解讀掛點。
REPORT_DATA = {
    "sections": [
        {
            "report_key": "cluster_topic_table",
            "title": "主題統計",
            "variants": [
                {"variant_key": "topic_table_tech", "label": "主題統計表（技術）",
                 "file": ""},
                {"variant_key": "opportunity_tech", "label": "機會矩陣",
                 "file": "opportunity_quadrant_tech.svg"},
            ],
        },
    ],
    "chart_rows": {"cluster_topic_table": [{"topic": "T1", "patent_count": 3}]},
}


class ImagelessVariantTests(unittest.TestCase):
    """空 `file` 必須當場說清楚，不能崩在 shutil 或靜默通過。"""

    def _run_dir(self, tmp: str) -> Path:
        run_dir = Path(tmp) / "report_trial_test"
        run_dir.mkdir()
        (run_dir / "report_data.json").write_text(
            json.dumps(REPORT_DATA, ensure_ascii=False), encoding="utf-8")
        (run_dir / "opportunity_quadrant_tech.svg").write_text("<svg/>", encoding="utf-8")
        return run_dir

    def test_imageless_variant_raises_chart_bundle_error(self):
        """選到無圖 variant 要拋 ChartBundleError，不是 PermissionError／IsADirectoryError。

        ⚠ 型別本身就是契約：呼叫端（ai_bridge 的 report_plan job）只認得
        ChartBundleError，其餘例外會以裸 traceback 冒到 job 失敗訊息裡。
        """
        with TemporaryDirectory() as tmp:
            run_dir = self._run_dir(tmp)
            with self.assertRaises(ChartBundleError) as ctx:
                build_selected_bundles(
                    run_dir, ["cluster_topic_table:topic_table_tech"], Path(tmp) / "work")
            message = str(ctx.exception)
        self.assertIn("cluster_topic_table:topic_table_tech", message,
                      "錯誤訊息要指出是哪一個 identity")
        self.assertIn("沒有圖檔", message,
                      "要說清楚真因是『這個 variant 本來就沒有圖』，不是檔案遺失")

    def test_normal_variant_still_works(self):
        """有圖的 variant 不受影響（防禦不得誤傷正常路徑）。"""
        with TemporaryDirectory() as tmp:
            run_dir = self._run_dir(tmp)
            bundles = build_selected_bundles(
                run_dir, ["cluster_topic_table:opportunity_tech"], Path(tmp) / "work")
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]["chart_identity"], "cluster_topic_table:opportunity_tech")

    def test_frontend_picker_filters_imageless_variants(self):
        """前端選圖清單不得列出無圖 variant——它預設全選，列了就必然送出。

        ⚠ 治源頭：後端報錯只是防禦，使用者按鈕仍會失敗。
        """
        html = INDEX_HTML.read_text(encoding="utf-8")
        start = html.index("function loadPptChartPicker")
        picker = html[start:html.index("function collectPptPlanBrief")]
        self.assertIn("v.file", picker,
                      "選圖清單需依 variant 的 file 過濾，無圖者不列入")


if __name__ == "__main__":
    unittest.main()
