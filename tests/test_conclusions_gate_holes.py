"""結論頁閘門的三個破口（tasks §7b）。

## 現況三行

```python
cc = c.get("conclusions")
if not cc: return []      # ① 沒宣告就放行 → 那頁根本不產出，閘門也不吭聲
if not rows: ...          # ② 只驗非空 → 10 個主題只寫 1 列，全綠
if topic in facts: ...    # ③ 主題名不在 facts 就整個跳過逐字比對
```

## 🔴 涵蓋率對帳，不是最小列數

規定「至少 N 列」是**形式鎖**——v5／v7／v9 三次同型失敗都是這樣來的：
CLI 為了過鎖而硬湊，或乾脆刪掉整段（缺席，目視兜不住）。

而且 §2.3 已明訂「接不上依據的建議句**直接擋下**」——那等於規格授權了丟棄。
配上「只驗非空」，10 個主題剩 2 列會全綠。

改用**涵蓋率對帳**：要求宣告 `covered N/M` 與未涵蓋主題的逐條原因。
它不規定要寫幾列，只要求**沒寫的要現形**——把缺席型偏差轉成一份看得見的清單，
讀者自己判斷是資料不夠還是 CLI 偷懶。

⚠ 三問：Q1 過（數字與集合比對）、Q2 **過**（要綠只有一種方式＝把未涵蓋的列出來，
沒有更省力的路）、Q3 不適用。這是恆等式型，不是代理指標。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "html-report-to-deck"
sys.path.insert(0, str(SKILL / "scripts"))

FACTS = [
    {"topic": "拉繩滑雪模擬機構", "topic_code": "T1", "finding": "10件/6家｜集中持有"},
    {"topic": "風磁複合阻力裝置", "topic_code": "T2", "finding": "11件/7家｜分散待驗"},
    {"topic": "捲輪回捲阻力機構", "topic_code": "T3", "finding": "10件/5家｜成長"},
]


def _row(topic, action="佈局"):
    finding = next(f["finding"] for f in FACTS if f["topic"] == topic)
    return {"topic": topic, "finding": finding,
            "reading": "對 RD 的意義", "action": action}


class _Base(unittest.TestCase):
    def _check(self, conclusions):
        from check_content import _check_conclusions

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topic_facts.json"
            path.write_text(json.dumps(FACTS, ensure_ascii=False), encoding="utf-8")
            content = {} if conclusions is None else {"conclusions": conclusions}
            return _check_conclusions(content, path)


class HoleOneTests(_Base):
    """① 沒宣告 conclusions 就放行。"""

    def test_missing_conclusions_is_an_error(self):
        bad = self._check(None)
        self.assertTrue(
            bad,
            "沒有 conclusions 也放行——那頁根本不會產出（畫法與閘門都在，"
            "只是沒人宣告），而且不會有任何人發現")


class HoleTwoTests(_Base):
    """② 只驗非空：10 主題寫 1 列全綠。"""

    def test_partial_coverage_without_reconciliation_is_an_error(self):
        bad = self._check({"rows": [_row("拉繩滑雪模擬機構")]})
        self.assertTrue(
            bad,
            "只寫 1 列、3 個主題，卻沒有涵蓋率對帳也放行——"
            "讀者會以為「只有這一個主題值得結論」")

    def test_full_coverage_needs_no_excuse(self):
        """全部涵蓋時不得誤擋（過度攔截與擋不住一樣壞）。"""
        rows = [_row(f["topic"]) for f in FACTS]
        bad = self._check({"rows": rows, "covered": "3/3", "uncovered": []})
        self.assertEqual(bad, [], f"全涵蓋卻被擋：{bad}")

    def test_partial_coverage_with_reconciliation_passes(self):
        """🔴 關鍵：**沒寫的要現形**，不是「一定要寫滿」。"""
        bad = self._check({
            "rows": [_row("拉繩滑雪模擬機構")],
            "covered": "1/3",
            "uncovered": [
                {"topic": "風磁複合阻力裝置", "reason": "無獨立項證據"},
                {"topic": "捲輪回捲阻力機構", "reason": "無獨立項證據"},
            ],
        })
        self.assertEqual(bad, [], f"有對帳卻被擋：{bad}")

    def test_reconciliation_must_match_reality(self):
        """對帳數字與實際列數對不上要紅——否則對帳本身變成裝飾。"""
        bad = self._check({
            "rows": [_row("拉繩滑雪模擬機構")],
            "covered": "3/3",
            "uncovered": [],
        })
        self.assertTrue(bad, "宣告 3/3 但只寫 1 列，仍然放行")

    def test_uncovered_list_must_be_complete(self):
        """漏列的主題沒進 uncovered 也要紅。"""
        bad = self._check({
            "rows": [_row("拉繩滑雪模擬機構")],
            "covered": "1/3",
            "uncovered": [{"topic": "風磁複合阻力裝置", "reason": "無獨立項證據"}],
        })
        self.assertTrue(bad, "少列一個未涵蓋主題卻放行——那個主題就這樣消失了")

    def test_uncovered_needs_a_reason(self):
        bad = self._check({
            "rows": [_row("拉繩滑雪模擬機構")],
            "covered": "1/3",
            "uncovered": [{"topic": "風磁複合阻力裝置", "reason": ""},
                          {"topic": "捲輪回捲阻力機構", "reason": "無獨立項證據"}],
        })
        self.assertTrue(bad, "未涵蓋沒寫原因卻放行——「資料不夠」與「沒寫」分不出來")


class HoleThreeTests(_Base):
    """③ 主題名不在 facts 就整個跳過逐字比對。"""

    def test_unknown_topic_name_is_an_error(self):
        rows = [_row(f["topic"]) for f in FACTS]
        rows[0] = {"topic": "我自己編的主題", "finding": "隨便寫",
                   "reading": "x", "action": "佈局"}
        bad = self._check({"rows": rows, "covered": "3/3", "uncovered": []})
        self.assertTrue(
            bad,
            "主題名不在 topic_facts 卻跳過比對——CLI 只要換個主題名，"
            "逐字比對就完全失效")


if __name__ == "__main__":
    unittest.main()
