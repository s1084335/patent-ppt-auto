"""批 1：表格頁要把資料放完整（H-1，2026-08-03 第三輪實機驗收）。

## 實機現象

p11 頁尾寫「顯示前 3/5 筆」，實際只看得到 2 筆，第 2 列還被切掉半行
（`OXEFIT, INC. 1` 整段不見）；判讀面板自己下方卻空一大片。

## 兩層根因

① `_render_table_with_points` 把 band 位置估在
   `table_bottom = top + min(height, (shown+1) * row_height_in)`
   —— `row_height_in` 是**宣告值 0.32**，而同一份 theme 的註解自己寫著
   「PowerPoint 列高**只增不減**」。內容一換行實際列高就是 0.6，
   估出來的底部比真的高一截，band 往上貼就蓋住表格。
   ⚠ 這是 07-31 我加「橫幅跟著表格底緣走」造成的；改動前固定 4.62 反而不會壓到。

② 更根本：表格框高寫死 `height_in: 2.88`（只夠 4 列宣告高），
   但 1.62 → 6.78（footnote 上緣）實際有 **5.16 in** 可用。
   使用者：「這個所有主題都放都還能放解讀，為甚麼要卡掉」。

## 定案（2026-08-03 使用者）

- 內頁精選、附錄放齊
- 🔴 **字級維持現狀不得再縮**——放不下只能分頁或精選，縮字視為驗收不過
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"
THEME_PATH = SKILL_DIR / "theme.json"


def _load_builder():
    # ⚠ sys.modules 要先掛上：build_ppt 的 dataclass 在解析延遲註解時會回查模組。
    spec = importlib.util.spec_from_file_location(
        "build_ppt_batch1", SKILL_DIR / "scripts" / "build_ppt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_ppt_batch1", module)
    spec.loader.exec_module(module)
    return module


bp = _load_builder()


def _theme():
    return bp.Theme.load(THEME_PATH)


class PointsBandHeightTests(unittest.TestCase):
    """要點橫幅高度要依**內容**算，不是固定 1.75。"""

    def test_band_height_shrinks_for_short_content(self):
        theme = _theme()
        g = theme.geometry["table_with_points"]
        blocks = [("現況", "拉繩捲輪回收15件為最大且最成熟主題", "accent", True),
                  ("意涵", "馬達捲繩自鎖與風阻磁阻均8件、6件", "ink", False)]
        height = bp._points_band_height(theme, blocks,
                                        width_in=g["points_band_width_in"],
                                        columns=int(g["points_band_columns"]))
        self.assertLess(height, g["points_band_height_in"],
                        "只有 2 條要點卻仍佔滿宣告高度 1.75——空間就是這樣被吃掉的")
        self.assertGreater(height, 0.4, "不能塌到看不見標題列")

    def test_band_height_grows_with_content(self):
        """內容多就要長高——固定高度會讓後面幾條被擠掉。"""
        theme = _theme()
        g = theme.geometry["table_with_points"]
        few = [("現況", "短" * 10, "accent", True)]
        many = [("現況", "長" * 40, "accent", True) for _ in range(6)]
        self.assertGreater(
            bp._points_band_height(theme, many, width_in=g["points_band_width_in"],
                                   columns=int(g["points_band_columns"])),
            bp._points_band_height(theme, few, width_in=g["points_band_width_in"],
                                   columns=int(g["points_band_columns"])))


class TableAvailableHeightTests(unittest.TestCase):
    """表格可用高度＝footnote 上緣 − 表格上緣 − 間距 − 要點區實際高度。"""

    def test_available_height_uses_page_not_declared_constant(self):
        theme = _theme()
        g = theme.geometry["table_with_points"]
        available = bp._table_available_height(theme, "table_with_points", band_height_in=0.9)
        self.assertGreater(available, g["height_in"],
                           "可用高度不該比寫死的 2.88 還小——那正是資料被卡掉的原因")
        # 上界：不得越過 footnote
        limit = theme.geometry["footnote"]["top_in"] - g["top_in"] - 0.9
        self.assertLessEqual(available, limit + 1e-9, "表格會壓到頁尾")

    def test_five_topic_rows_fit(self):
        """p11 技術主題 5 筆必須全部放得下。

        實測（本地產 PPT 逐列量）：表頭與 5 列在 7 欄寬度下**每列都是 2 行**，
        `row_height_in` 0.32 是**每行**高 → 需要 6 × 0.64 = 3.84 in。
        要點橫幅實際 1.18 in（2 條要點、雙欄）。

        🔴 使用者：「這個所有主題都放都還能放解讀，為甚麼要卡掉」——
        差 0.04 in 也是卡掉，不能算「幾乎放得下」。
        """
        theme = _theme()
        available = bp._table_available_height(theme, "table_with_points", band_height_in=1.18)
        self.assertGreaterEqual(available, 3.84,
                                "5 筆技術主題放不下——使用者明確要求全部放進去")


class AddTableReturnsUsedHeightTests(unittest.TestCase):
    """`_add_table` 要把**實際用掉的高度**交出來。

    ⚠ 它內部本來就逐列累加算過（`used_height`），只是沒回傳；呼叫端於是拿
    宣告列高自己重估一次——同一個量兩處落點，且其中一處是錯的。
    """

    def test_signature_returns_pair(self):
        import inspect

        src = inspect.getsource(bp._add_table)
        self.assertIn("-> tuple[int, float]", src,
                      "_add_table 仍只回傳列數，呼叫端只能拿宣告列高重估")
        self.assertIn("return len(display), used_height", src)

    def test_caller_uses_returned_height(self):
        """呼叫端要用回傳的實際高度定位 band，不得再用 row_height_in 估。"""
        import inspect

        src = inspect.getsource(bp._render_table_with_points)
        self.assertNotIn('(shown + 1) * g["row_height_in"]', src,
                         "還在用宣告列高估表格底部——實際列高只增不減")
        self.assertIn("used_height", src, "沒有使用 _add_table 回傳的實際高度")


class AppendixSplitByChannelTests(unittest.TestCase):
    """H-4：附錄不得把技術與功效混在同一張表。

    實機 p20 標題寫「全分類**技術**指標總表」，表內卻有「提升訓練成效」這種功效主題，
    而且 13 筆只出 6 筆。使用者定案：**內頁精選、附錄放齊**。

    ⚠ 拆分機制早就有（`_split_by_channel` 的 `row_filter`），只是被
    `if spec.is_appendix: return [spec]` 擋掉——當初判斷「附錄總表不拆」，
    但總表混兩種主題本來就讀不出意義，且標題還宣稱自己只有技術。
    """

    def _report_data(self):
        rows = [
            {"topic_code": "T001", "label": "拉繩捲輪回收機構",
             "source_field": "wips_independent_claims", "patent_count": 15},
            {"topic_code": "E001", "label": "提升訓練成效",
             "source_field": "effect_summary", "patent_count": 9},
        ]
        return {"reports": {"cluster_topic_table": {"rows": rows, "row_count": len(rows)}}}

    def test_appendix_splits_into_two_tables(self):
        spec = next(s for s in bp.PAGE_LAYOUT
                    if s.is_appendix and "cluster_topic_table" in s.report_keys)
        pages = bp._split_by_channel(spec, self._report_data())
        self.assertEqual(len(pages), 2, "附錄仍把技術與功效混在同一張表")
        self.assertTrue(all(p.is_appendix for p in pages), "拆出來的頁要保持附錄身分")
        filters = [p.row_filter for p in pages]
        self.assertTrue(any(("source_field", "wips_independent_claims") in (f or ())
                            for f in filters))
        self.assertTrue(any(("source_field", "effect_summary") in (f or ()) for f in filters))

    def test_appendix_titles_keep_appendix_prefix(self):
        """標題要同時說明「這是附錄」與「這是哪個通道」。

        ⚠ 內頁拆頁時 title 直接換成通道名（技術主題分布），附錄照抄會丟掉「附錄1」
        ——讀者在目錄與頁序上就找不到它了。
        """
        spec = next(s for s in bp.PAGE_LAYOUT
                    if s.is_appendix and "cluster_topic_table" in s.report_keys)
        pages = bp._split_by_channel(spec, self._report_data())
        for page in pages:
            with self.subTest(title=page.title):
                self.assertIn("附錄", page.title)
        self.assertNotEqual(pages[0].title, pages[1].title, "兩頁標題不得相同")


class AppendixPaginationTests(unittest.TestCase):
    """附錄放不下一頁時要自動分頁——**附錄要放齊**（2026-08-03 使用者定案）。

    ⚠ 只切附錄：內頁是「精選」，少列是刻意的；附錄的職責才是「完整」。
    ⚠ 每頁列數用 `_appendix_rows_per_page`，與渲染端共用 `_table_line_plan`——
    分頁端另寫一套估法的話，切出來的頁數與實際放得下的列數會對不起來。
    """

    def _rows(self, n):
        return [{"topic_code": f"T{i:03d}", "label": f"主題名稱{i}" * 3,
                 "source_field": "wips_independent_claims",
                 "patent_count": n - i, "applicant_count": 3,
                 "top3_share": 50, "representative": f"CN{i:07d}、US{i:07d}、EP{i:07d}"}
                for i in range(n)]

    def _report_data(self, n):
        return {"reports": {"cluster_topic_table": {"rows": self._rows(n), "row_count": n}}}

    def test_long_appendix_is_split(self):
        theme = _theme()
        data = self._report_data(40)
        pages = bp._expand_page_layout(data, None, theme)
        appendix = [p for p in pages if p.is_appendix and "cluster_topic_table" in p.report_keys]
        self.assertGreater(len(appendix), 1, "40 列的附錄仍擠在一頁——放不下的會被截掉")
        self.assertTrue(all(p.row_slice for p in appendix), "分頁後每頁要帶列切片")

    def test_slices_cover_every_row_without_overlap(self):
        """切片要**無縫也無重疊**：漏一列就是沒放齊，重複一列則是同一筆印兩次。"""
        theme = _theme()
        data = self._report_data(40)
        pages = [p for p in bp._expand_page_layout(data, None, theme)
                 if p.is_appendix and "cluster_topic_table" in p.report_keys]
        covered: list[int] = []
        for page in pages:
            start, stop = page.row_slice
            covered.extend(range(start, stop))
        self.assertEqual(covered[:40], list(range(40)), "切片沒有覆蓋全部列或有重疊")

    def test_short_appendix_not_split(self):
        """放得下就不切——為了分頁而分頁只是多一張空頁。"""
        theme = _theme()
        pages = bp._expand_page_layout(self._report_data(3), None, theme)
        appendix = [p for p in pages if p.is_appendix and "cluster_topic_table" in p.report_keys]
        self.assertEqual(len(appendix), 1)
        self.assertIsNone(appendix[0].row_slice)

    def test_spec_with_keeps_new_fields(self):
        """🔴 `_spec_with` 要自動帶上所有欄位。

        原本是手寫 dict，新增 `row_slice` 若忘了同步就會被**靜默**洗回預設值
        ——後面任何一次 `_spec_with(spec, topic=...)` 都會發生，而且不報錯。
        """
        spec = bp.PageSpec(page=1, kind="table", title="t", topic="t",
                           report_keys=("x",), is_appendix=True, row_slice=(3, 9))
        self.assertEqual(bp._spec_with(spec, title="u").row_slice, (3, 9))


if __name__ == "__main__":
    unittest.main()
