"""J-3（2026-08-04）：代表專利號不得在連字號處被 PowerPoint 折成兩行。

## 症狀（第五輪實機 p12／p21）

代表專利欄的 `2019-0247710` 被拆成 `2019-` ／ `0247710` 兩行。

## 根因

⚠ 不是欄寬不夠——PowerPoint 把 ASCII 連字號 `-` 當**合法斷點**，
自動換行不寫進 XML，程式掃 `text_frame.paragraphs` 永遠是 0 處，
只有轉圖／實機看得見（第五輪我因此誤報過一次「I-10 通過」）。

## 修法

引擎組 `representative` 時就把 `-` 換成不斷行連字號 U+2011——
顯示長相相同、PowerPoint 不再視為斷點；網頁端同樣受益。
關口只有一個（`_pick_representative`），組版端零改動。
"""
import unittest

from backend.app.reports.cluster_analytics import _pick_representative


class NonBreakingHyphenTests(unittest.TestCase):
    def _pick(self):
        patents = {1: {"number": "2019-0247710", "application_year": 2021},
                   2: {"number": "US11234567", "application_year": 2020}}
        app_by = {1: {"甲"}, 2: {"甲"}}
        return _pick_representative({1, 2}, patents, app_by, [{"name": "甲"}])

    def test_hyphen_is_non_breaking(self):
        rep = self._pick()["representative"]
        self.assertNotIn("-", rep, f"仍含 ASCII 連字號：{rep!r}")
        self.assertIn("‑", rep, "連字號應換成 U+2011（不斷行）")

    def test_digits_unchanged(self):
        rep = self._pick()["representative"]
        self.assertIn("2019", rep)
        self.assertIn("0247710", rep)


if __name__ == "__main__":
    unittest.main()
