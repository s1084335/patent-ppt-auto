"""申請人統計口徑：**共同申請人各自計數**（2026-08-06 使用者定案，改回展開 VIEW）。

## 沿革（⚠ 本檔前身是反向契約，已第二度翻轉）

| 日期 | 定案 | 本檔當時斷言什麼 |
|---|---|---|
| 2026-07-28 | 共同申請人拆開各自計數，建 0042 展開 VIEW | — |
| 2026-07-31 | **推翻**：分析只計第一順位 | 「展開 VIEW 不得被引用」 |
| 2026-08-06 | **再次推翻**：改回展開口徑 | 「三張申請人報表必須引用展開 VIEW」 |

⚠ **為什麼 08-06 這次不一樣**：前兩次是口徑偏好之爭，這次是**正確性**——

    實測「曾晴」在 14 件專利／4 個國家具名為共同申請人，
    第一順位口徑只顯示 2 件／1 國。報表在陳述不實資訊（問題 16）。

而「件數總和大於專利件數」（55→68 列）是**標示問題不是真相問題**，
0042 原文件本就要求該頁加註「含共同申請」。兩者不對等，故取正確性。

## 現行契約

| 層 | 處理 |
|---|---|
| 瀏覽專利／詳情顯示 | **保留完整字面 `A | B`**（三次翻轉都沒動過這條） |
| 申請人三報表（排名／國別交叉／年度矩陣） | **展開 VIEW**，共同申請人各自計數 |
| 其餘 aggregate 報表 | 維持一專利一列的 base，否則專利總數重複計數 |
| 權人與分群家數 | 走 base 的 split_part 第一順位欄（本次未動） |
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_ROOT / "alembic" / "versions" / "0045_expanded_view_columns.py"
REFRESH_PATH = (PROJECT_ROOT / "backend" / "app" / "derived"
                / "refresh_report_patent_base.py")

# 依 0042 定案只給這三張用；其餘一律 base。
EXPANDED_REPORTS = frozenset({
    "applicant_ranking",
    "applicant_country_distribution",
    "applicant_year_matrix",
})


def _load_migration(tag: str):
    """載入 0045 migration 模組（每次獨立載入，避免測試間共用被改過的 op）。"""
    spec = importlib.util.spec_from_file_location(f"mig0045_{tag}", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExpandedApplicantCountingTests(unittest.TestCase):
    """共同申請人各自計數；其餘報表不得被波及。"""

    def test_applicant_reports_use_expanded_view(self):
        """三張申請人報表必須讀展開 VIEW，否則共同申請人會被吃掉。"""
        from backend.app.reports.report_definitions import (
            APPLICANT_EXPANDED_TABLE,
            REPORT_DEFINITIONS,
        )

        for name in sorted(EXPANDED_REPORTS):
            with self.subTest(report=name):
                self.assertEqual(
                    REPORT_DEFINITIONS[name].source_table, APPLICANT_EXPANDED_TABLE,
                    f"{name} 沒讀展開表——共同申請人（如曾晴 14 件/4 國）"
                    "會被算成第一順位那一家")

    def test_other_aggregate_reports_stay_on_base(self):
        """⚠ 其餘 aggregate 報表必須維持 base，否則專利總數重複計數。"""
        from backend.app.reports.report_definitions import (
            REPORT_DEFINITIONS,
            REPORT_SOURCE_TABLE,
        )

        for name, d in REPORT_DEFINITIONS.items():
            if d.report_type != "aggregate" or name in EXPANDED_REPORTS:
                continue
            if d.source_table.endswith(("report_family_country", "report_family_quality")):
                continue
            with self.subTest(report=name):
                self.assertEqual(
                    d.source_table, REPORT_SOURCE_TABLE,
                    f"{name} 讀了展開表——一件會被算成多件，專利總數會膨脹")

    def test_detail_display_keeps_full_text(self):
        """⚠ 瀏覽／詳情層的「申請人」保留完整 `A | B`（三次翻轉都沒動過這條）。"""
        sql = REFRESH_PATH.read_text(encoding="utf-8")
        lines = [line.strip() for line in sql.splitlines()]
        self.assertIn('b."申請人",', lines,
                      "原始申請人欄被改動了——顯示層要完整字面 `A | B`")

    def test_display_name_column_is_still_first_position(self):
        """`applicant_display_name` 仍是 split_part 第一順位。

        ⚠ 這條**不因改回展開口徑而失效**：展開 VIEW 展的是原始 `申請人` 欄，
        `applicant_display_name` 在 base 那層仍是主申請人——兩者語意不同、各有用途，
        不得因為「都叫申請人」就把其中一個改掉。
        """
        sql = REFRESH_PATH.read_text(encoding="utf-8")
        self.assertIn("split_part", sql)
        self.assertIn("第一個", sql)


class ExpandedViewColumnContractTests(unittest.TestCase):
    """0045 補欄契約：少一欄就有報表算不出來或直接壞。"""

    def test_view_carries_columns_the_reports_need(self):
        """展開 VIEW 必須帶齊五欄，逐欄寫明沒有會怎樣。"""
        mod = _load_migration("cols")
        needed = {
            "申請人": "applicant_ranking 的 4 個 aggregate 指名讀這欄，沒有直接壞",
            "WIPS同族ID": "算不出家族數（權利強度三維之部署強度）",
            "legal_status": "敘述寫不出「孟喬 5 件 0% 授權」",
            "patent_type": "專利種類維度算不出來",
            "document_kind": "設計案 11 件無法標示（A4）",
        }
        for col, why in needed.items():
            with self.subTest(column=col):
                self.assertIn(col, mod._ADDED, f"展開 VIEW 缺 {col}：{why}")

    def test_claim_count_column_is_deliberately_absent(self):
        """⚠ `權利要求的項數` **刻意不補**——權利強度已收斂三維，權利範圍該維度已否決。

        寫成測試是為了擋「看起來少一欄就順手加回去」：加了會讓已否決的維度悄悄復活。
        """
        mod = _load_migration("noclaim")
        self.assertNotIn("權利要求的項數", mod._ADDED,
                         "權利範圍維度已於 2026-08-05 否決，不應把欄位加回展開 VIEW")

    def test_dependent_view_is_dropped_before_base_view(self):
        """🔴 相依順序：展開 VIEW 必須在 DROP base VIEW **之前**先拆。

        不先拆，PostgreSQL 會以「其他物件相依」擋住 DROP，整支 migration 失敗。
        ⚠ 0029 當年沒有這一步，是因為 0042 的展開 VIEW 那時還不存在——照抄會炸。
        """
        for direction in ("upgrade", "downgrade"):
            with self.subTest(direction=direction):
                mod = _load_migration(direction)
                executed: list[str] = []

                # ⚠ 用預設參數把 `executed` 綁進來，不靠閉包捕捉迴圈變數
                # （B023：迴圈變數在閉包裡是後期綁定，下一圈會改到上一圈的物件）。
                class _Op:
                    @staticmethod
                    def execute(stmt, _sink=executed):
                        _sink.append(" ".join(str(stmt).split()))

                mod.op = _Op
                getattr(mod, direction)()

                drop_expanded = next(
                    i for i, q in enumerate(executed)
                    if q.startswith("DROP VIEW") and "applicant_expanded" in q)
                drop_base = next(
                    i for i, q in enumerate(executed)
                    if q.startswith("DROP VIEW") and q.endswith("report_patent_base"))
                self.assertLess(
                    drop_expanded, drop_base,
                    f"{direction}：展開 VIEW 沒先拆，DROP base VIEW 會被相依性擋住")

    def test_refresh_carries_new_columns_into_base(self):
        """⚠ 加欄還不夠——refresh 沒搬就是欄位存在但恆 NULL。

        本專案發生過：`Curr. IPC/CPC` 兩欄實體表早有、refresh 從未搬，
        導致 derived 恆 NULL、報表只能讀 Orig.。三個落點缺一都會靜默失效。
        """
        sql = REFRESH_PATH.read_text(encoding="utf-8")
        for col in ("patent_type", "document_kind"):
            with self.subTest(column=col):
                self.assertIn(f"p.{col},", sql, f"base CTE 沒從 core_layer 搬 {col}（會恆為 NULL）")
                self.assertIn(f"b.{col},", sql, f"外層 SELECT 沒帶出 {col}")
                self.assertIn(f"    {col},\n", sql, f"INSERT 欄位清單沒列 {col}")


if __name__ == "__main__":
    unittest.main()
