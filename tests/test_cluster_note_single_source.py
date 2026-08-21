"""分群母體揭露句只能有一個定義處（2026-08-20 晚場驗收發現）。

## 症狀

同一份「為什麼少了 10 件」的說明散在三處，而**讀者實際看到的那一處沒被修到**：

| 落點 | 消費者 | 08-20 修正前後 |
|---|---|---|
| `population.py` 的母體註記 | **deck**（HTML 完全不讀） | 已改為依設計案件數推導 |
| `patent_kind.design_exclusion_note` | 封面備註 | 已改為「依文獻種類排除」 |
| `chart_runner` 的 section note | **HTML 報表**（讀者看到的就是這句） | ❌ 仍寫「無獨立項文字者」 |

實測使用者匯出的 `割草機.html`：`population` 那一份的字串**一次都沒出現**，
出現的是 chart_runner 寫死的那句。前兩處改完，HTML 讀者一個字都沒變。

## 為什麼那句話是錯的

排除依據早已是 `DESIGN_DOCUMENT_KINDS`（文獻種類），不是有沒有文字。
實測 10 件設計案中 **1 件確實帶獨立項文字**（patent_id 452），
先前正是靠「有沒有文本」這個代理指標把它漏進技術分群。
留著舊說法，下次有人依它判斷「補上文本就能進分群」就會做錯事。

## 契約

揭露句由 `population` 產生（該模組 docstring 自陳「母體數字與排除原因只在這裡算一次」），
chart_runner 只呼叫、不自己拼字串。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHART_RUNNER = ROOT / "backend/app/reports/chart_runner.py"


class ClusterSectionNoteTests(unittest.TestCase):
    """揭露句本身：理由要與實際排除機制相符。"""

    def _note(self, clustered, total, design_count):
        from backend.app.reports.population import cluster_section_note

        return cluster_section_note(clustered, total, design_count)

    def test_states_the_design_rule_when_counts_match(self):
        """🔴 排除數＝設計案數時，理由指向文獻種類規則。"""
        note = self._note(216, 226, 10)
        self.assertIn("216", note)
        self.assertIn("226", note)
        self.assertIn("外觀設計", note, note)
        self.assertNotIn("無獨立項文字", note, f"仍沿用代理指標說法：{note}")

    def test_splits_when_shortfall_exceeds_design_count(self):
        """設計案以外還少的要分開講——那才是真的缺文本。"""
        note = self._note(210, 226, 10)
        self.assertIn("外觀設計 10", note, note)
        self.assertIn("6", note, f"多排除的 6 件沒交代：{note}")

    def test_no_cause_invented_when_design_count_unknown(self):
        """⚠ 判不出設計案件數時只講數字，不得猜理由。"""
        note = self._note(216, 226, None)
        self.assertIn("216", note)
        self.assertNotIn("外觀設計", note, f"沒有依據卻聲稱是設計案：{note}")
        self.assertNotIn("無獨立項文字", note, note)

    def test_empty_when_no_shortfall(self):
        """母體等於總數時不出這句——沒有東西被排除。"""
        self.assertEqual(self._note(226, 226, 10), "")

    def test_reuses_the_same_reason_builder_as_population_note(self):
        """⚠ 揭露句與頁尾註記的理由必須同源，否則同一件事兩種說法。"""
        from backend.app.reports.population import cluster_exclusion_reason

        self.assertIn(cluster_exclusion_reason(10, 10), self._note(216, 226, 10))


class ChartRunnerDelegatesTests(unittest.TestCase):
    """chart_runner 不得自己拼這句話。"""

    def _source(self) -> str:
        return CHART_RUNNER.read_text(encoding="utf-8")

    def test_old_proxy_wording_is_gone(self):
        """🔴 這是 HTML 讀者實際看到的字串，必須真的從程式碼消失。"""
        src = self._source()
        offenders = [
            f"{i}: {line.strip()[:70]}"
            for i, line in enumerate(src.splitlines(), 1)
            if "無獨立項文字" in line and not line.lstrip().startswith("#")
        ]
        self.assertEqual(offenders, [],
                         "chart_runner 仍在輸出代理指標說法：\n  " + "\n  ".join(offenders))

    def test_delegates_to_population(self):
        src = self._source()
        self.assertIn("cluster_section_note", src,
                      "chart_runner 沒有改呼叫 population 的唯一定義處")

    def test_does_not_rebuild_the_sentence_inline(self):
        """⚠ 只驗「有呼叫」不夠——舊的手拼字串可能還留著並列輸出。"""
        src = self._source()
        inline = re.findall(r"本表母體為分群涵蓋的", src)
        self.assertLessEqual(
            len(inline), 0,
            f"chart_runner 仍自行拼揭露句（{len(inline)} 處），應由 population 產生")


if __name__ == "__main__":
    unittest.main()
