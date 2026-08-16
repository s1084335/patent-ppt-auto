"""報表 catalog 與母體註記的契約（improve-report-professionalism 2.1）。

## 這支測試守什麼

**catalog**：13 張 → 11 張（2026-08-09 使用者裁決）。刪掉的兩張必須從 registry、
前端清單與母體註記登記三處**一起**消失——⚠ 只刪 registry 會留下指向不存在報表
的死條目，而那不會報錯，只會在某天讓註記查不到對應報表。

**母體註記**：`reports/population.py` 是母體的唯一定義處。1.3 查出三個缺口，
共同點都是「母體必然小於總數，但沒有解釋」——讀者看到「母體 40/55 件」
只會認為資料錯誤，那正是母體對帳器當初要解決的問題本身。
"""
from __future__ import annotations

import unittest

from backend.app.reports.population import (
    OVER_COUNTING_REPORTS,
    POPULATION_REASONS,
)
from backend.app.reports.report_definitions import DEFAULT_REPORT_NAMES, REPORT_DEFINITIONS

#: 2026-08-09 使用者裁決刪除的報表。
#:
#: ⚠ `applicant_strength_profile` **本批不刪**：查證後發現它還是 `kp_quadrant`
#: （Key Player 四象限）的資料來源，刪掉會讓正要「強化 Key Player 深度」的那張
#: 圖沒有資料。使用者裁決「連 kp_quadrant 一起重新設計」，併入 2.3 處理。
REMOVED_REPORTS = ("lifecycle",)

#: 待 2.3 與 kp_quadrant 一起重新設計，屆時再決定去留。
PENDING_REDESIGN = ("applicant_strength_profile",)


class CatalogTests(unittest.TestCase):
    def test_removed_reports_gone_from_registry(self):
        """⚠ 刪除理由見 openspec tasks 1.2：兩者的維度都已由其他報表回答，
        且在本專案的典型樣本規模（數十件）下讀不出東西。"""
        for name in REMOVED_REPORTS:
            self.assertNotIn(name, REPORT_DEFINITIONS)
            self.assertNotIn(name, DEFAULT_REPORT_NAMES)

    def test_removed_reports_gone_from_population_registry(self):
        """⚠ 死條目：`lifecycle` 原本登記在 OVER_COUNTING_REPORTS，刪報表時
        必須一併清掉，否則留下指向不存在報表的登記。"""
        for name in REMOVED_REPORTS:
            self.assertNotIn(name, OVER_COUNTING_REPORTS)
            self.assertNotIn(name, POPULATION_REASONS)

    def test_kept_reports_intact(self):
        """⚠ 對照組：該留的一張都不能少。國別線三張全留——它們單位不同
        （件數 vs 同族數），合併會把兩種單位混在一張圖上。"""
        for name in ("application_trend", "publication_trend", "country_distribution",
                     "family_country_layout", "applicant_country_distribution",
                     "ipc_main_distribution", "cpc_main_distribution",
                     "applicant_ranking", "design_protection_detail", "applicant_year_matrix",
                     "cluster_topic_table", "opportunity_quadrant"):
            self.assertIn(name, REPORT_DEFINITIONS)

    def test_catalog_size(self):
        """13 → 12（本批只刪 lifecycle）。⚠ applicant_strength_profile 待 2.3
        與 kp_quadrant 一起重新設計後才決定去留。"""
        self.assertEqual(len(REPORT_DEFINITIONS), 13)

    def test_pending_redesign_still_present(self):
        """⚠ 對照組：待重新設計的報表現在必須還在——刪早了 Key Player 會沒資料。"""
        for name in PENDING_REDESIGN:
            self.assertIn(name, REPORT_DEFINITIONS)


class PopulationCoverageTests(unittest.TestCase):
    """1.3 查出的三個缺口。"""

    def test_publication_trend_has_reason(self):
        """⚠ 未授權公告的專利沒有公告年，母體**必然**小於總數。

        沒有原因登記時，讀者看到「母體 40/55 件」不知為何——那正是 A3 母體
        對帳器當初要解決的問題（「封面寫 55、各頁各說各話，而沒有一頁解釋」）。
        """
        self.assertIn("publication_trend", POPULATION_REASONS)

    def test_country_distribution_has_reason(self):
        """缺 country_code 的專利不會進這張圖，同樣要有解釋。"""
        self.assertIn("country_distribution", POPULATION_REASONS)

    def test_opportunity_quadrant_unit_is_not_patents(self):
        """⚠ 這張圖的單位是**主題**不是件——沿用件數句型會產出
        「母體 7/55 件」這種語意錯誤的註記。

        處置：登記為非件數報表，由 population 改用主題單位或不印。
        """
        from backend.app.reports.population import NON_PATENT_UNIT_REPORTS

        self.assertIn("opportunity_quadrant", NON_PATENT_UNIT_REPORTS)

    def test_every_report_is_accounted_for(self):
        """⚠ 每張報表都要落在四類之一：有原因／會重複計數／非件數單位／
        母體等於總數。沒有第五類——「沒登記」等於「沒人檢查過它的母體對不對」。
        """
        from backend.app.reports.population import (
            NON_PATENT_UNIT_REPORTS,
            SAME_AS_TOTAL_REPORTS,
        )

        classified = (set(POPULATION_REASONS) | set(OVER_COUNTING_REPORTS)
                      | set(NON_PATENT_UNIT_REPORTS) | set(SAME_AS_TOTAL_REPORTS))
        missing = sorted(set(REPORT_DEFINITIONS) - classified)
        self.assertEqual(missing, [], f"這些報表的母體沒有人檢查過：{missing}")


if __name__ == "__main__":
    unittest.main()


class RegistryReferenceIntegrityTests(unittest.TestCase):
    """⚠ 每個引用 report_key 的地方都必須指向真的存在的報表。

    2026-08-09 教訓：刪掉 `lifecycle` 的 ReportDefinition 後，**全部報表測試依然
    全綠**，但實際產圖立刻炸 `ValueError: Unknown report: lifecycle`——因為
    `chart_runner` 的 SECTION_SPECS、圖檔對應表與讀圖說明各自留著死引用。

    測試綠不代表能跑。這支測試把「引用完整性」變成單元測試層守得住的東西，
    不必每次都跑一次要 DB 的完整產圖流程。
    """

    #: 無對應 ReportDefinition 的虛擬 section 別名（`chart_runner.py` 明載的既有契約）。
    #: ⚠ 白名單只放**刻意**沒有報表定義的 key；死引用不得靠加白名單解決。
    VIRTUAL_SECTION_KEYS = frozenset({"cluster_analytics"})

    def test_section_specs_reference_existing_reports(self):
        from backend.app.reports.chart_runner import SECTION_SPECS

        missing = sorted({
            key for spec in SECTION_SPECS for key in spec.reports
            if key not in REPORT_DEFINITIONS and key not in self.VIRTUAL_SECTION_KEYS
        })
        self.assertEqual(missing, [], f"section 引用了不存在的報表：{missing}")

    def test_chart_file_map_references_existing_reports(self):
        from backend.app.reports.chart_runner import CHART_FILE_REPORTS

        missing = sorted({
            key for keys in CHART_FILE_REPORTS.values() for key in keys
            if key not in REPORT_DEFINITIONS
        })
        self.assertEqual(missing, [], f"圖檔對應表引用了不存在的報表：{missing}")

    def test_population_registries_reference_existing_reports(self):
        from backend.app.reports.population import (
            NON_PATENT_UNIT_REPORTS,
            SAME_AS_TOTAL_REPORTS,
        )

        registered = (set(POPULATION_REASONS) | set(OVER_COUNTING_REPORTS)
                      | set(NON_PATENT_UNIT_REPORTS) | set(SAME_AS_TOTAL_REPORTS))
        missing = sorted(registered - set(REPORT_DEFINITIONS))
        self.assertEqual(missing, [], f"母體登記指向不存在的報表：{missing}")


class ThresholdAndTruncationTests(unittest.TestCase):
    """出頁門檻與截斷註記（2.1）——兩者都已實作，本測試把契約釘住。"""

    def test_truncation_note_only_when_truncated(self):
        from backend.app.reports.chart_runner import truncation_note

        self.assertEqual(truncation_note(20, 20), "")
        self.assertEqual(truncation_note(25, 20), "")
        self.assertIn("20/50", truncation_note(20, 50))

    def test_truncation_note_points_to_web_report(self):
        """⚠ 附錄 2 已於 2026-08-04 移除——註記不得再指向附錄。

        F-12 的教訓：兩張排名圖各寫各的註記會漂移（實機 p14 有、p15 沒有），
        讀者以為 p15 就是全部。唯一來源在 `truncation_note`。
        """
        from backend.app.reports.chart_runner import truncation_note

        note = truncation_note(20, 50)
        self.assertIn("網頁報表", note)
        self.assertNotIn("附錄", note)

    def test_classification_threshold_is_three(self):
        """2026-08-05 使用者定案：4 階沒有 3 種以上，IPC/CPC 就不進簡報。"""
        from backend.app.reports.chart_runner import CLASSIFICATION_MIN_DISTINCT_L4

        self.assertEqual(CLASSIFICATION_MIN_DISTINCT_L4, 3)
