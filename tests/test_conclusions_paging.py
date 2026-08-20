"""結論頁自動分頁（tasks §9.4）。

## 為什麼

§9.3 拆掉數量鎖後，結論列數由資料決定——一頁一定會裝不下。
而字級鎖死不能縮字（縮字＝把「放不下」變成「看不清」），**分頁是唯一不損失
內容的解法**（使用者 2026-08-19 裁決）。

## 判準：項目集合相等，不是頁數等於某個公式

⚠ 斷言「頁數 == ceil(N/k)」會把版面演算法寫進測試——改個行距就假紅，
而假紅久了就會被改成「反正就是這個數字」，那條測試從此不再守任何東西。
真正要守的是：**沒有任何一項在分頁過程中消失**。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "html-report-to-deck" / "scripts"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _rows(n: int) -> list[dict]:
    return [{"topic": f"主題{i:02d}", "finding": f"{i}件/{i}家｜成長",
             "reading": "判讀句" * 6, "action": "追蹤", "pending_count": i}
            for i in range(1, n + 1)]


class PaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")

    def test_small_set_stays_on_one_page(self):
        """⚠ 少量資料不得被硬拆——分頁是為了裝不下，不是為了整齊。"""
        pages = self.dl.paginate_conclusions(_rows(3))
        self.assertEqual(len(pages), 1)

    def test_large_set_is_split(self):
        pages = self.dl.paginate_conclusions(_rows(40))
        self.assertGreater(len(pages), 1, "40 列還塞一頁——一定溢出")

    def test_no_row_is_lost(self):
        """🔴 §9.4.3 的真正判準：項目集合相等。"""
        rows = _rows(40)
        pages = self.dl.paginate_conclusions(rows)
        flat = [r for page in pages for r in page]
        self.assertEqual([r["topic"] for r in flat], [r["topic"] for r in rows],
                         "分頁後有列消失或順序變了")

    def test_no_duplicate_row(self):
        """⚠ 邊界off-by-one 最常見的症狀不是漏掉，是**重複**。"""
        pages = self.dl.paginate_conclusions(_rows(40))
        topics = [r["topic"] for page in pages for r in page]
        self.assertEqual(len(topics), len(set(topics)), "有列被畫了兩次")

    def test_every_page_is_non_empty(self):
        """⚠ 空白續頁比溢出更難發現——它看起來像「設計如此」。"""
        pages = self.dl.paginate_conclusions(_rows(40))
        for i, page in enumerate(pages, 1):
            with self.subTest(page=i):
                self.assertTrue(page, f"第 {i} 頁是空的")

    def test_action_grouping_survives_paging(self):
        """⚠ 同一個行動分組被拆到兩頁時，兩邊都要看得出自己屬於哪一組。

        不然讀者在第二頁看到一堆主題卻不知道它們是什麼行動。
        """
        rows = [{"topic": f"T{i}", "finding": "x", "reading": "y" * 40,
                 "action": "追蹤" if i % 2 else "佈局", "pending_count": i}
                for i in range(30)]
        pages = self.dl.paginate_conclusions(rows)
        for page in pages:
            with self.subTest(first=page[0]["topic"]):
                self.assertTrue(all("action" in r for r in page))


class PageTitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")

    def test_continuation_pages_are_marked(self):
        """§9.4.1：續頁標題要帶「（續）」，讀者才知道還沒完。"""
        titles = self.dl.conclusion_page_titles("綜合結論", 3)
        self.assertEqual(titles[0], "綜合結論")
        self.assertTrue(all("續" in t for t in titles[1:]), titles)

    def test_single_page_has_no_suffix(self):
        """⚠ 只有一頁時不得加「（續）」——那會讓讀者找不存在的下一頁。"""
        self.assertEqual(self.dl.conclusion_page_titles("綜合結論", 1),
                         ["綜合結論"])


if __name__ == "__main__":
    unittest.main()
