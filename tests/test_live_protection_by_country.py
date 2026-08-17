"""受理局頁：現行有效整合進同一張圖（2026-08-17 晚）。

## 定案沿革（同一晚兩次，都是使用者裁決）

1. 「國家現行有效要從資料驅動，不是只是單純去用歷史國家申請的授權件數」
   → 實作 EP 授權案展開成各 EPC 指定國（實測：一件 EP 案生出 24 個國家列，
   表從 4 列變 28 列，其中 24 列都是「0 申請／1 有效」）。
2. 看過實物後：「EP 國家不用展開，用 EP 就好」
   → 展開與收斂機制整套移除（需求收回時機制要一起收回，留著會讓下一個人
   以為系統支援 EP 展開）。

## 現行契約

- 「現行有效」＝**該受理局**申請案中，法律狀態桶為已授權者；EP 算在 EP 名下。
- 判定唯一處是 `transforms.legal_status.status_bucket`——⚠ 不得改抓「授權」
  字面欄，中文資料上剛好相等，英文登錄（granted／registered）會自成一欄而少算。
- 同一張圖每列兩條：上條＝歷史申請（依狀態堆疊）、下條＝現行有效，**共用同一
  把尺**，所以「申請過但現在沒權利了」的差距是直接看得出來的長度差。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner

# (受理局, 原始狀態, 件數)——含簡體與英文狀態
ROWS = [
    {"country_code": "CN", "legal_status": "授权", "patent_count": 24},
    {"country_code": "CN", "legal_status": "审查中", "patent_count": 5},
    {"country_code": "CN", "legal_status": "到期(Non-payment)", "patent_count": 7},
    {"country_code": "US", "legal_status": "granted", "patent_count": 3},
    {"country_code": "US", "legal_status": "审查中", "patent_count": 2},
    {"country_code": "EP", "legal_status": "授权", "patent_count": 1},
    {"country_code": "EP", "legal_status": "审查中", "patent_count": 1},
]


class LiveCountTests(unittest.TestCase):
    def _pivot(self):
        return {r["country_code"]: r for r
                in chart_runner.country_status_display_pivot(ROWS)}

    def test_live_count_per_office(self):
        pivot = self._pivot()
        self.assertEqual(pivot["CN"]["現行有效"], 24)
        self.assertEqual(pivot["CN"]["申請件數"], 36)

    def test_english_status_counted(self):
        """⚠ 防「直接抓『授權』欄」的偷懶寫法：granted 在字面表自成一欄，
        抓欄位會少算，走 status_bucket 才對。"""
        us = self._pivot()["US"]
        self.assertEqual(us["現行有效"], 3)
        self.assertNotIn("授權", [k for k, v in us.items() if v])

    def test_ep_not_expanded(self):
        """🔴 2026-08-17 定案：EP 算在 EP 名下，不展開成 EPC 指定國。"""
        pivot = self._pivot()
        self.assertEqual(pivot["EP"]["現行有效"], 1)
        for state in ("DE", "FR", "GB"):
            self.assertNotIn(state, pivot, f"{state} 不該出現——EP 不展開")

    def test_column_order_puts_summaries_first(self):
        cols = list(chart_runner.country_status_display_pivot(ROWS)[0])
        self.assertEqual(cols[:3], ["country_code", "申請件數", "現行有效"])

    def test_no_legacy_alive_column(self):
        """同一個問題只能有一個答案——舊名「現存有效」不得同時存在。"""
        for row in chart_runner.country_status_display_pivot(ROWS):
            self.assertNotIn("現存有效", row)


class StackChartTests(unittest.TestCase):
    def _svg(self) -> str:
        out = Path(tempfile.mkdtemp()) / "jurisdiction_distribution.svg"
        chart_runner.render_country_status_stack(
            out, "專利受理局分布", chart_runner.country_status_display_pivot(ROWS))
        return out.read_text(encoding="utf-8")

    def test_single_bar_per_row(self):
        """🔴 2026-08-17 定案：一條 bar 就夠。

        現行有效恆為申請件數的子集合（同兩個欄位推導），堆疊裡的「授權」段
        已經在講同一件事——第二條 bar 是把同一份資料畫兩次。
        """
        svg = self._svg()
        self.assertNotIn('data-role="live-bar"', svg, "第二條 bar 應已移除")
        # 資料條只有一種高度＝每列只有一組堆疊。⚠ 只看帶 <title> 的
        # （圖例色塊沒有 title，混進來會誤判成第二條 bar）。
        import re
        heights = set(re.findall(r'height="(\d+)"[^>]*><title>', svg))
        self.assertEqual(len(heights), 1, f"資料條出現多種高度（多條 bar）：{heights}")

    def test_live_number_kept_at_right(self):
        """⚠ 數字不能一起拿掉：它走狀態桶，英文登錄時與字面「授權」段不同，
        那時只看堆疊會看不出真正的有效數。"""
        svg = self._svg()
        self.assertIn("現行有效", svg)
        self.assertIn("累計申請", svg)
        self.assertIn(">24<", svg, "CN 的現行有效數字不見了")

    def test_zero_live_renders(self):
        rows = chart_runner.country_status_display_pivot(
            [{"country_code": "JP", "legal_status": "审查中", "patent_count": 2}])
        out = Path(tempfile.mkdtemp()) / "x.svg"
        chart_runner.render_country_status_stack(out, "t", rows)
        self.assertIn(">0<", out.read_text(encoding="utf-8"))

    def test_valid_svg(self):
        import xml.etree.ElementTree as ET

        ET.fromstring(self._svg())


if __name__ == "__main__":
    unittest.main()
