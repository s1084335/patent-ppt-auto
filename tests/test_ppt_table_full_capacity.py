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


class RowHeightSafetyMarginTests(unittest.TestCase):
    """I-2：表格不得壓到頁尾（2026-08-03 第四輪實機 p23）。

    實機：附錄2 第 1 頁最後一列「OXEFIT, INC.」與「資料來源：…」重疊。

    根因：`row_height_in`＝0.32 是**宣告值**，PowerPoint 的列高只增不減
    ——實測每列約 0.33–0.34。15 列累積差約 0.3 in，剛好越過 footnote 上緣。

    ⚠ **不猜實際列高**：那要靠轉圖量測，而且字型一換就變
    （I-3 的字寬係數已經猜了三次還沒中，不重蹈覆轍）。
    改為**結構性保險**：可用高度預留一整列的緩衝。
    即使每列都比宣告高 5%，15 列累積 0.24 in 仍在一列（0.32）之內。

    ⚠ 代價是少放一列——但「寧可少一列，也不能壓到頁尾」：
    少一列會誠實顯示在「顯示前 N/M 筆」，壓到頁尾則是兩段文字疊在一起、兩邊都讀不了。
    """

    def test_available_height_reserves_one_row(self):
        """附錄頁（無要點橫幅）要預留緩衝。"""
        theme = _theme()
        g = theme.geometry["table"]
        available = bp._table_available_height(theme, "table")
        room = theme.geometry["footnote"]["top_in"] - g["top_in"]
        self.assertLessEqual(available, room - g["row_height_in"] + 1e-9,
                             "沒有預留列高誤差緩衝——列數一多就會壓到頁尾")

    def test_band_pages_do_not_lose_a_row(self):
        """🔴 有要點橫幅的頁**不扣**緩衝。

        ⚠ 表格下方還接著 band ＋ 間距，那本身就是緩衝；再扣一列會讓技術主題頁
        少放第 5 筆——而「5 筆全放」是使用者明確要求的
        （「這個所有主題都放都還能放解讀，為甚麼要卡掉」）。
        """
        theme = _theme()
        self.assertGreaterEqual(
            bp._table_available_height(theme, "table_with_points", band_height_in=1.18),
            3.84, "有橫幅的頁被多扣了一列，技術主題第 5 筆會放不下")

    def test_margin_applies_to_both_call_sites(self):
        """🔴 同型落點要一起吃到緩衝。

        ⚠ `_appendix_rows_per_page`（算每頁列數）與 `_add_table`（決定顯示列數）
        都用 `_table_available_height`；上一輪的錯誤正是「同一個假設兩處落點、
        只修了一處」。這裡驗兩者拿到同一個值。
        """
        import inspect

        for fn in (bp._appendix_rows_per_page, bp._render_table):
            src = inspect.getsource(fn)
            with self.subTest(fn=fn.__name__):
                self.assertIn("_table_available_height", src,
                              f"{fn.__name__} 沒有走統一的可用高度計算")


class AppendixEvenSplitTests(unittest.TestCase):
    """I-5：附錄分頁要**平均分配**，不是塞滿再溢出（2026-08-03 實機 p21／p22）。

    實機：附錄1 功效 8 筆被切成 **7＋1**，第 2 頁只有一列、整頁 90% 空白。

    ⚠ 原因是 `_paginate_appendix` 用「每頁塞滿 per_page 筆」切——
    最後一頁拿到的是餘數。8 筆、每頁 7 筆 → 7＋1。
    改為**先算頁數、再平均分**：8 筆 2 頁 → 4＋4。

    ⚠ 每頁上限仍是 `per_page`（版面放得下的量），平均只是在頁數確定後
    把筆數攤平——不會因為攤平而讓某頁放不下。
    """

    def _rows(self, n):
        return [{"topic_code": f"T{i:03d}", "label": f"主題名稱{i}" * 3,
                 "source_field": "wips_independent_claims",
                 "patent_count": n - i, "applicant_count": 3, "top3_share": 50,
                 "representative": f"CN{i:07d}、US{i:07d}、EP{i:07d}"} for i in range(n)]

    def test_even_split(self):
        """8 筆分 2 頁要是 4＋4，不是 7＋1。"""
        sizes = bp.split_rows_evenly(8, per_page=7)
        self.assertEqual(sizes, [4, 4], f"分頁不平均：{sizes}")

    def test_never_exceeds_per_page(self):
        """平均後每頁都不得超過版面上限。"""
        for total, per_page in ((8, 7), (20, 15), (30, 7), (13, 5), (100, 9)):
            sizes = bp.split_rows_evenly(total, per_page=per_page)
            with self.subTest(total=total, per_page=per_page):
                self.assertEqual(sum(sizes), total, "有列被漏掉或重複")
                self.assertTrue(all(0 < n <= per_page for n in sizes), f"{sizes} 超出上限")
                self.assertLessEqual(max(sizes) - min(sizes), 1, f"{sizes} 不夠平均")

    def test_single_page_when_fits(self):
        self.assertEqual(bp.split_rows_evenly(5, per_page=7), [5])

    def test_appendix_uses_even_split(self):
        """`_paginate_appendix` 要走這支，不自己算餘數。"""
        import inspect

        self.assertIn("split_rows_evenly", inspect.getsource(bp._paginate_appendix))


class UnbreakableTokenTests(unittest.TestCase):
    """表格欄寬要放得下**最長的不可斷 token**（2026-08-03 實機 p22）。

    實機：附錄1 功效表的「代表專利」欄把 `121754861` 折成 `12175486` / `61`
    ——那是**一個專利號**，中間斷開會被讀成兩個號碼。

    ⚠ 兩個成因疊在一起：
    ① `build_ppt._display_width` 的英數係數是 **0.55**，而 `chart_runner` 那份
       已於 I-3 依實測改為 **0.62**（真實字寬比估算多約 13%）
       ——**同一個估算在兩處各寫一份，只改了一邊**（本專案第 8 次兩處落點）。
    ② 欄寬只看「整串內容」的比例需求，沒有保障**單一 token 不被拆開**。

    ⚠ 專利號、代碼這類 token 斷開後語意就毀了，與一般文字換行不同。
    """

    def test_display_width_matches_chart_runner(self):
        """兩份估算要同一個係數——只改一邊就是下一個 bug。"""
        from backend.app.reports import chart_runner as cr

        for text in ("A63B-0022", "121754861", "HUSQVARNA AB"):
            with self.subTest(text=text):
                self.assertAlmostEqual(bp._display_width(text), cr._display_width(text),
                                       places=6, msg="兩處字寬估算不一致")

    def test_longest_token_fits(self):
        """欄寬需求要涵蓋最長 token，不能只看整串比例。"""
        rows = [{"code": "121754861、4104909、2026-0158353"},
                {"code": "3628018"}]
        widths = bp._column_widths(["code"], rows, {"code": "代表專利"}, 4.0,
                                   size_pt=11, inset_in=0.08)
        longest = max(bp._display_width(t) for t in "121754861、4104909、2026-0158353".split("、"))
        need_in = longest * (11 / 72.0) + 0.08 * 2
        self.assertGreaterEqual(widths[0], need_in,
                                f"欄寬 {widths[0]:.2f} 放不下最長 token（需 {need_in:.2f}）")


if __name__ == "__main__":
    unittest.main()
