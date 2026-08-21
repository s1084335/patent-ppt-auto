"""母體閘門：自行查 DB 的彙總必須接母體，或顯式豁免並寫理由。

## 為什麼要閘門而不是逐個修

同型錯誤已出現三次，全部不報錯：

| # | 位置 | 顯示 | 實際（滑雪機） |
|---|---|---|---|
| 1 | 報表引擎母體 | 61 | 55（2026-08-17 已修） |
| 2 | 受理局頁家族註記 | 187 | 48 |
| 3 | 封面三分法 | 281 件（設計 21） | 55 件（設計 11） |

⚠ 三次代表這是**系統性**的。逐次修的話，第四次還是會在別的地方冒出來。

## 這道閘門的效力邊界

三問（deepen design §1.2）結果：Q1 過、**Q2 不過**（塞進豁免表、理由隨便寫就通關，
是代理指標不是恆等式）、Q3 過（豁免表多一筆，diff 看得見）。

⚠ 所以它保證的是「**每個全庫彙總都被登記過**」，不是「登記的理由是對的」。
豁免表變長是要被質疑的訊號。本測試因此**同時斷言豁免表的內容**——
新增豁免會讓測試紅，逼人在 PR 裡解釋，而不是靜悄悄多一筆。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.db import population_scope as ps

ROOT = Path(__file__).resolve().parents[1]

#: 目前已知且已複核的豁免（模組:函式 → 一句話理由）。
#: ⚠ 這裡是**第二道**：模組自己宣告理由（給讀程式的人看），這裡登記已複核清單
#:   （給 review 的人看）。新增豁免要同時改兩處，讓它不可能悄悄長大。
REVIEWED_EXEMPTIONS = {
    "backend/app/derived/company_alias_importer.py:list_zh_name_drafts",
    "backend/app/derived/company_alias_importer.py:count_company_normalization_queue",
    "backend/app/derived/patent_search_terms.py:refresh_patent_search_terms",
    "backend/app/derived/refresh_report_patent_base.py:refresh_report_patent_base",
    "backend/app/api/jobs.py:ready",
    "backend/app/repositories/company_group_repository.py:list_company_groups",
    "backend/app/repositories/company_group_repository.py:list_confirmed_group_candidates",
    "backend/app/repositories/workflow_outputs_repository.py:_append",
    "backend/app/mcp_server/tools_reporting.py:get_data_status",
}


class PopulationScopeGateTests(unittest.TestCase):
    def test_every_aggregate_query_is_scoped_or_exempt(self):
        """🔴 核心：沒接母體又沒登記豁免的，一律紅。"""
        bad = ps.violations(ROOT)
        self.assertEqual(
            [f.describe() for f in bad], [],
            "以下彙總既沒接母體也沒登記豁免——它們會用全庫算出數字而不報錯。\n"
            f"接母體，或在該模組宣告 {ps.EXEMPT_ATTR} 並寫明為什麼是全庫用途。")

    def test_exemptions_all_carry_a_reason(self):
        """豁免必須寫理由；空字串等於沒登記。"""
        for f in ps.scan(ROOT):
            if f.exempt_reason is None:
                continue
            with self.subTest(func=f.describe()):
                self.assertTrue(
                    f.exempt_reason.strip(),
                    f"{f.describe()} 登記了豁免但理由是空的——"
                    "「忘了接」與「刻意全庫」在程式碼上長得一樣，不寫下來分不出來")

    def test_exemption_list_does_not_grow_silently(self):
        """⚠ 豁免表變長是訊號不是捷徑：新增就要在這裡也登記一次。"""
        current = {
            f"{f.module}:{f.func}" for f in ps.scan(ROOT)
            if f.exempt_reason is not None
        }
        added = current - REVIEWED_EXEMPTIONS
        removed = REVIEWED_EXEMPTIONS - current
        self.assertEqual(
            added, set(),
            f"新增了未複核的豁免：{sorted(added)}——"
            "請在 PR 說明為什麼它是全庫用途，並加進 REVIEWED_EXEMPTIONS")
        self.assertEqual(
            removed, set(),
            f"這些豁免已不存在，請從 REVIEWED_EXEMPTIONS 移除：{sorted(removed)}")


class ScannerBlindSpotTests(unittest.TestCase):
    """⚠ 掃描器抓不到「走 run_report 但定義不支援 patent_ids」那一類。

    第 2 例（受理局家族註記 187 vs 48）就是那類——它沒有自行 execute，
    是 `family_country_layout` 這張報表的 `supports_patent_ids=False`。
    本閘門對它**無效**，另立這條擋。
    """

    #: `supports_patent_ids=False` 且已複核的報表。
    #: cluster 型的範圍由 workspace_id 經 load_cluster_workspace_data 給，非 patent_ids。
    REVIEWED_UNSCOPED_REPORTS = {
        "applicant_strength_profile": "cluster 型：範圍由 workspace_id 給",
        "cluster_topic_table": "cluster 型：範圍由 workspace_id 給",
        "opportunity_quadrant": "cluster 型：範圍由 workspace_id 給",
    }

    def _unscoped_reports(self) -> set[str]:
        import re

        src = (ROOT / "backend/app/reports/report_definitions.py").read_text(
            encoding="utf-8")
        out: set[str] = set()
        for m in re.finditer(
                r"ReportDefinition\((?:(?!ReportDefinition\().)*?\)", src, re.S):
            body = m.group(0)
            name = re.search(r'name="([a-z_]+)"', body)
            if name and re.search(r"supports_patent_ids=False", body):
                out.add(name.group(1))
        return out

    def test_unscoped_reports_are_all_reviewed(self):
        """🔴 新增 `supports_patent_ids=False` 的報表就要說明理由。"""
        current = self._unscoped_reports()
        added = current - set(self.REVIEWED_UNSCOPED_REPORTS)
        self.assertEqual(
            added, set(),
            f"新增了未複核的不吃母體報表：{sorted(added)}——"
            "它們會用全庫算數字（第 2、3 例就是這樣來的）")


if __name__ == "__main__":
    unittest.main()
