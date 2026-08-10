"""選圖清單能列的，必須正好是 PPT 用得到的（2026-08-10 實機抓到）。

## 實機失敗

從前端按下「產生 PPT」→ job failed：

    ChartBundleError: 選圖 identity 'applicant_year_matrix:more' 不在本報表版本

前端列了 13 張，其中「申請人年度專利分布矩陣（11-20）」＝ `applicant_year_matrix:more`
是 `chart_bundle` 不認得的。兩邊各自從 sections 推清單，於是分岔：

| 來源 | variants |
|---|---|
| `report_data.json`（`chart_bundle` 讀的） | `variants`: default／`more_variants`: more（**分開**） |
| API `/content`（前端讀的） | `variants`: default ＋ more（**已合併**） |

⚠ API 的合併是**正當的**——網頁瀏覽要顯示 more 圖與它的解讀。錯的是選圖清單
拿「網頁顯示什麼」當判準，而它需要的是「PPT 能用什麼」（SKILL.md 明訂
`_more` 長尾圖不上 PPT）。

## 修法：把兩端釘在一起，不各自推

API 每個 variant 帶 `ppt_eligible`，前端只消費不推導。本檔直接比對
**API 標為 eligible 的集合** 與 **`chart_bundle` 認得的 identity 集合**必須相等
——任一端改規則，這裡就紅。

（同今日 `test_audit_contract_end_to_end.py` 的做法：無法 import 共用時，
就讓測試把兩端釘在一起。這是本專案第九次「同一份知識兩個落點」。）
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports.chart_bundle import _index_report_data

REPORT_DATA = {
    "sections": [
        {
            "report_key": "applicant_year_matrix",
            "title": "申請人年度矩陣",
            "variants": [{"variant_key": "default", "file": "applicant_year_matrix.svg"}],
            "more_variants": [{"variant_key": "more",
                               "file": "applicant_year_matrix_more.svg"}],
        },
        {
            "report_key": "application_trend",
            "title": "申請趨勢",
            "variants": [{"variant_key": "default", "file": "annual_trend.svg"}],
        },
    ],
    "chart_rows": {"applicant_year_matrix": [], "application_trend": []},
}


class PptEligibleVariantTests(unittest.TestCase):
    """API 標的與 chart_bundle 認的必須是同一組。"""

    def test_more_variants_are_not_ppt_eligible(self):
        """`more_variants` 是網頁長尾圖，SKILL.md 明訂不上 PPT。"""
        from backend.app.main import ppt_eligible_variant_keys

        keys = ppt_eligible_variant_keys(REPORT_DATA["sections"][0])
        self.assertIn("default", keys)
        self.assertNotIn("more", keys,
                         "more 變體不得標為可上 PPT——選圖清單會列它，然後 job 失敗")

    def test_api_eligible_set_equals_chart_bundle_index(self):
        """🔴 兩端釘在一起：API 標 eligible 的 identity 集合 == chart_bundle 認得的。

        任一端改規則就紅。這正是實機失敗的根因——兩邊各自從 sections 推。
        """
        from backend.app.main import ppt_eligible_variant_keys

        api_eligible = {
            f"{section['report_key']}:{key}"
            for section in REPORT_DATA["sections"]
            for key in ppt_eligible_variant_keys(section)
        }
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "report_data.json"
            path.write_text(json.dumps(REPORT_DATA, ensure_ascii=False), encoding="utf-8")
            bundle_index = set(_index_report_data(REPORT_DATA))
        self.assertEqual(
            api_eligible, bundle_index,
            "選圖清單能列的與 PPT 用得到的必須一致——不一致就是使用者按下去才失敗",
        )

    def test_frontend_filters_by_ppt_eligible(self):
        """前端只列 `ppt_eligible` 的變體，不得自己推導規則。

        ⚠ 前端若寫死「排除 variant_key === 'more'」就是第十個落點。
        """
        html = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
                / "index.html").read_text(encoding="utf-8")
        start = html.index("function loadPptChartPicker")
        picker = html[start:html.index("function collectPptPlanBrief")]
        self.assertIn("ppt_eligible", picker,
                      "選圖清單要依後端標記過濾，不自行推導哪些能上 PPT")


if __name__ == "__main__":
    unittest.main()
