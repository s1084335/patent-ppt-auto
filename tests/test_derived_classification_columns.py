"""derived 層要搬齊 IPC／CPC 四個 Main 欄，且 Main／All 不得混用（2026-07-28）。

實機發現（使用者問「21-02、19-07 是三小」時追出）：

    來源檔 滑雪機.xlsx   Curr. IPC(Main) 12 筆、Curr. CPC(Main) 19 筆
      → mappings/wips.py  有定義（含 is_current／is_original 標記）      ✓
      → core_layer.patents  Curr. IPC 12、Curr. CPC 19                  ✓
      → derived_layer.report_patent_base  **連欄位都沒有**              ✗ 斷在這

`refresh_report_patent_base.py` 的 SELECT 只挑了 Orig. 兩欄，Curr. 兩欄從未被搬到
derived 層。報表因此永遠讀不到現行分類（Curr.），只能用原始分類（Orig.）。

使用者定案（2026-07-28）：
1. derived 層**要接通** Curr. IPC(Main)／Curr. CPC(Main)。
2. 報表**先只讀 Orig. Main**（Curr 覆蓋率僅 20%／32%，直接切過去圖會空掉）；
   欄位先搬好，口徑之後再定。
3. Main 與 All **不得混用**。

Main／All 的分野（已實查 DB 確認乾淨，本測試鎖住不許退化）：
- **Main＝單一分類**，存 `core_layer.patents`；實查 4 個 Main 欄含 `|` 的列數皆為 0。
- **All＝多分類**，以 ` | ` 分隔，存 `core_layer.patent_attributes`；60 列中 34 列多值。
- Main 恆等於 All 的第一項（抽驗 6 筆全相符）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFRESH_SQL = (PROJECT_ROOT / "backend" / "app" / "derived"
               / "refresh_report_patent_base.py").read_text(encoding="utf-8")

MAIN_COLUMNS = (
    "Orig. IPC(Main)",
    "Curr. IPC(Main)",
    "Orig. CPC(Main)",
    "Curr. CPC(Main)",
)


class DerivedCarriesClassificationTests(unittest.TestCase):
    """四個 Main 欄都要被 derived 層搬過去。"""

    def test_all_four_main_columns_selected(self):
        for col in MAIN_COLUMNS:
            with self.subTest(col=col):
                self.assertIn(
                    f'"{col}"', REFRESH_SQL,
                    f"derived 沒搬 {col}——core 有值但報表永遠讀不到")

    def test_columns_appear_in_table_definition(self):
        """不只 SELECT，建表欄位清單也要有，否則寫不進去。"""
        for col in MAIN_COLUMNS:
            with self.subTest(col=col):
                # SELECT 一次、欄位清單一次、外層 SELECT 一次，至少兩處
                self.assertGreaterEqual(
                    REFRESH_SQL.count(f'"{col}"'), 2,
                    f"{col} 只出現一次——多半漏了建表欄位清單或外層 SELECT")


class MainAllNotMixedTests(unittest.TestCase):
    """Main 與 All 不得混用（語意不同：單值 vs ` | ` 分隔多值）。"""

    def test_derived_carries_no_all_columns(self):
        """report_patent_base 是「一列一專利」的寬表，只放 Main。

        All 是多值欄，放進來會讓 group by 直接把整串當一個分類（例如
        'A63B-069/18 | A63B-024/00' 會變成一個 bar），統計必錯。
        All 若要用，應另立展開表（一列一分類），不是塞進這張寬表。
        """
        for col in ("Orig. IPC(All)", "Curr. IPC(All)",
                    "Orig. CPC(All)", "Curr. CPC(All)"):
            with self.subTest(col=col):
                self.assertNotIn(
                    f'"{col}"', REFRESH_SQL,
                    f"{col} 是 ' | ' 分隔的多值欄，混進一列一專利的寬表會讓 group by 統計錯誤")

    def test_report_definitions_use_main_only(self):
        """報表定義只能 group by Main 欄，不得直接 group by All。"""
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        for name, d in REPORT_DEFINITIONS.items():
            used = list(getattr(d, "group_by", ()) or ()) + list(getattr(d, "columns", ()) or ())
            for col in used:
                if "(All)" in str(col):
                    self.fail(f"報表 {name} 直接使用多值欄 {col}——需先展開成一列一分類")


class ReportsReadOrigMainTests(unittest.TestCase):
    """口徑：報表先只讀 Orig. Main（使用者 2026-07-28 定案）。"""

    def test_ipc_report_reads_orig_main(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        d = REPORT_DEFINITIONS["ipc_main_distribution"]
        self.assertEqual(tuple(d.group_by), ("Orig. IPC(Main)",))

    def test_cpc_report_reads_orig_main(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        d = REPORT_DEFINITIONS["cpc_main_distribution"]
        self.assertEqual(tuple(d.group_by), ("Orig. CPC(Main)",))


if __name__ == "__main__":
    unittest.main()
