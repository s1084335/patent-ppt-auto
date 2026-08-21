"""敘述統計章節（EXP-026）——結論之後、圖表章節之前的「這份資料是什麼」。

## 為什麼要有

報表現在是「一進去就是圖」。讀者在看第一張圖之前，沒有任何地方告訴他這份資料
涵蓋多少件、哪些類型、什麼年份、還活著多少。使用者 2026-08-21：
「結論後面，報表之前要有個敘述統計，就是專利類型各幾件」。

## 🔴 本檔守的核心：不得有第二份計算

敘述統計要的數字，**大部分引擎已經在算**：

| 數字 | 既有來源 |
|---|---|
| 件數／家族數／受理局數／類型三分法 | `fetch_cover_stats` |
| 法律狀態桶 | `transforms/legal_status.status_bucket` |

⚠ 若敘述統計自己再查一次 `count(*)`，就會出現「封面 216、敘述統計 214」這種
**不會報錯**的不一致——症狀要等讀者發現才浮出來。故本檔斷言它**委派**而非重算。
"""
from __future__ import annotations

import inspect
import unittest
from unittest import mock

from backend.app.reports import chart_runner


def _fake_conn(rows_by_sql: dict | None = None, all_by_sql: dict | None = None):
    """側錄 SQL 的假連線（形狀沿用 test_cover_stats，不另立一套）。"""
    seen: list[tuple] = []

    class Cur:
        def execute(self, sql, params=None):
            seen.append((str(sql), params))
            self._sql = str(sql)

        def fetchone(self):
            text = getattr(self, "_sql", "")
            for needle, value in (rows_by_sql or {}).items():
                if needle in text:
                    return value
            return {"n": 0}

        def fetchall(self):
            text = getattr(self, "_sql", "")
            for needle, value in (all_by_sql or {}).items():
                if needle in text:
                    return value
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn(Cur):
        def cursor(self, **kw):
            return Cur()

    return Conn(), seen


class DescriptiveStatsExistsTests(unittest.TestCase):
    def test_engine_exposes_descriptive_stats(self):
        self.assertTrue(
            hasattr(chart_runner, "fetch_descriptive_stats"),
            "引擎沒有供給敘述統計——章節只能自己湊數字")

    def test_patent_ids_is_required(self):
        """與 fetch_cover_stats 同一條紀律：忘記傳母體要當場炸，不是靜默退回全庫。"""
        sig = inspect.signature(chart_runner.fetch_descriptive_stats)
        self.assertIn("patent_ids", sig.parameters)
        self.assertIs(
            sig.parameters["patent_ids"].default, inspect.Parameter.empty,
            "patent_ids 有預設值——呼叫端忘記傳就靜默退回全庫")


class DelegationTests(unittest.TestCase):
    """🔴 本檔最重要的一組：證明它消費既有計算，不是自己再算一份。"""

    def test_delegates_cover_numbers_not_recompute(self):
        """件／族／受理局／三分法一律來自 fetch_cover_stats。"""
        conn, _ = _fake_conn()
        sentinel = {
            "patent_count": 55,
            "family_count": 48,
            "jurisdiction_count": 7,
            "kind_tally": {"發明": 17, "新型": 27, "設計": 11},
        }
        with mock.patch.object(chart_runner, "_app_layer_connect", lambda: conn), \
                mock.patch.object(chart_runner, "fetch_cover_stats",
                                  lambda **kw: dict(sentinel)):
            result = chart_runner.fetch_descriptive_stats(patent_ids=[1, 2, 3])
        for key, value in sentinel.items():
            self.assertEqual(
                result.get(key), value,
                f"{key} 不是來自 fetch_cover_stats——敘述統計自己算了一份")

    def test_does_not_write_its_own_family_expression(self):
        """家族口徑只能有一個定義處。"""
        src = inspect.getsource(chart_runner.fetch_descriptive_stats)
        self.assertNotIn(
            "COALESCE", src,
            "敘述統計自己寫了家族 ID 運算式——兩份會各自演進而不報錯")

    def test_does_not_judge_patent_kind_itself(self):
        src = inspect.getsource(chart_runner.fetch_descriptive_stats)
        self.assertNotRegex(
            src, r"document_kind",
            "敘述統計自行判定專利種類——唯一定義處是 transforms/patent_kind")

    def test_does_not_enumerate_status_literals(self):
        """法律狀態桶走 transforms/legal_status，不在此列舉狀態字面。"""
        src = inspect.getsource(chart_runner.fetch_descriptive_stats)
        for literal in ("已授權", "審查中", "已失效"):
            self.assertNotIn(
                literal, src,
                f"敘述統計自行列舉狀態字面「{literal}」——桶的唯一定義處是 transforms/legal_status")


class ContentTests(unittest.TestCase):
    def _run(self, patent_ids=(1, 2, 3)):
        conn, seen = _fake_conn(
            rows_by_sql={"min(": {"min_year": 2011, "max_year": 2024}},
            all_by_sql={"legal_status": [
                {"legal_status": "已授權", "n": 30},
                {"legal_status": "審查中", "n": 15},
                {"legal_status": None, "n": 10},
            ]},
        )
        with mock.patch.object(chart_runner, "_app_layer_connect", lambda: conn), \
                mock.patch.object(chart_runner, "fetch_cover_stats",
                                  lambda **kw: {"patent_count": 55, "family_count": 48,
                                                "jurisdiction_count": 7,
                                                "kind_tally": {"發明": 17}}):
            return chart_runner.fetch_descriptive_stats(patent_ids=list(patent_ids)), seen

    def test_has_legal_status_tally(self):
        result, _ = self._run()
        self.assertIn("legal_status_tally", result, "敘述統計缺法律狀態分布")
        self.assertTrue(result["legal_status_tally"], "法律狀態分布是空的")

    def test_legal_status_uses_shared_buckets(self):
        """空值要落在「未知」桶，不得憑空生一個新桶名。"""
        from backend.app.transforms.legal_status import STATUS_BUCKET_ORDER

        result, _ = self._run()
        for bucket in result["legal_status_tally"]:
            self.assertIn(
                bucket, STATUS_BUCKET_ORDER,
                f"「{bucket}」不在狀態桶的唯一定義處裡")

    def test_has_year_range(self):
        result, _ = self._run()
        self.assertIn("year_range", result, "敘述統計缺時間範圍")
        self.assertEqual(result["year_range"], {"min": 2011, "max": 2024})

    def test_every_query_is_scoped(self):
        """母體閘門：本函式送出的每一條 SQL 都要帶母體條件。"""
        _, seen = self._run()
        self.assertTrue(seen, "沒有送出任何 SQL")
        for sql, _params in seen:
            with self.subTest(sql=sql[:60]):
                self.assertRegex(sql, r"(?i)\bWHERE\b", "敘述統計的查詢沒有母體條件")
                self.assertRegex(sql, r"(?i)patent_id")


class WiringTests(unittest.TestCase):
    """接線：算出來還要真的送到讀者眼前，否則等於沒做。"""

    def test_report_data_carries_descriptive_stats(self):
        src = inspect.getsource(chart_runner.run_chart_trial)
        self.assertRegex(
            src,
            r'"descriptive_stats"\s*:\s*fetch_descriptive_stats\(\s*patent_ids\s*=\s*ctx\.patent_ids',
            "report_data 沒有帶 descriptive_stats，或沒有把 ctx 的母體傳下去")

    def test_preview_payload_exposes_descriptive_stats(self):
        """前端預覽區塊要拿得到——只寫進 report_data.json 前端看不到。"""
        from backend.app import main as app_main

        src = inspect.getsource(app_main._report_content_payload)
        self.assertIn(
            "descriptive_stats", src,
            "預覽 payload 沒帶 descriptive_stats——前端拿不到就渲染不出來")


class SeamGuardTests(unittest.TestCase):
    """🔴 新增 DB 接縫就要在既有的「不碰 DB」測試裡多擋一個。

    2026-08-21 實測：只擋 `fetch_cover_stats` **擋不住**敘述統計——它自己另外查
    狀態分布與年份範圍，`_app_layer_connect` 仍被呼叫。當時本機 DB 剛好連得上，
    測試全綠，**看起來完全正常**。這種綠比紅危險：換一台沒有 DB 的機器就整批紅，
    而且紅在與改動無關的檔案上。
    """

    _DB_FREE_FILES = (
        "tests/test_chart_sections.py",
        "tests/test_report_analysis_types.py",
        "tests/test_workspace_name_in_report_data.py",
        "tests/test_workspace_scoped_versions.py",
    )

    def test_db_free_suites_block_the_new_seam(self):
        from pathlib import Path

        for rel in self._DB_FREE_FILES:
            with self.subTest(file=rel):
                text = Path(rel).read_text(encoding="utf-8")
                self.assertIn(
                    "fetch_cover_stats", text,
                    f"{rel} 不是宣告不碰 DB 的測試？請確認本清單")
                self.assertIn(
                    "fetch_descriptive_stats", text,
                    f"{rel} 擋了 fetch_cover_stats 卻沒擋 fetch_descriptive_stats"
                    "——第三個接縫沒擋住，測試會偷連本機 DB")


class PreviewCardTests(unittest.TestCase):
    """敘述統計在預覽區是**一張可選的卡**，不是黏在趨勢圖上方的常駐區塊。

    使用者 2026-08-21 三句連續修正：
      「敘述統計在前端預覽單獨放一區塊好了」
      「我是說敘述統計**單獨一張卡**」
      「**檢視那裏也要能選**」「也就是**不要再放在趨勢圖那裡**」

    ⇒ 它要成為 `report-view-select` 的一個選項，選中時渲染進 `report-inline-view`
    （卡片外殼由 `#report-inline-view:not(:empty)` 的 CSS 提供，不必自訂卡片樣式）。
    """

    def setUp(self):
        from pathlib import Path

        self.html = Path("backend/app/static/index.html").read_text(encoding="utf-8")

    def test_is_an_option_in_the_view_select(self):
        """檢視選單要選得到它。"""
        start = self.html.find("function buildReportViewOptions(")
        self.assertGreater(start, -1)
        end = self.html.find("\nfunction ", start + 1)
        body = self.html[start:end]
        self.assertIn(
            "descriptive_stats", body,
            "buildReportViewOptions 沒有把敘述統計加進選項——檢視選單裡選不到")

    def test_viewer_renders_the_card(self):
        """選中時 renderReportViewer 要畫得出來。"""
        start = self.html.find("function renderReportViewer(")
        self.assertGreater(start, -1)
        end = self.html.find("\nfunction ", start + 1)
        body = self.html[start:end]
        self.assertIn(
            "descriptive", body.lower(),
            "renderReportViewer 沒有處理敘述統計選項——選了也畫不出來")

    def test_standalone_block_is_gone(self):
        """🔴 不得再黏在趨勢圖上方（使用者：不要再放在趨勢圖那裡）。"""
        self.assertNotIn(
            'id="report-descriptive-stats"', self.html,
            "常駐區塊還在——它會出現在趨勢圖卡片上方，看起來像趨勢圖的一部分")

    def test_no_separate_card_css(self):
        """卡片外殼沿用 #report-inline-view 的既有樣式，不另寫一套。

        ⚠ 另寫一套 = 同一種卡片視覺有兩個定義處，改版時只會改到一邊。
        """
        self.assertNotIn(
            ".descriptive-stats {", self.html,
            "又自訂了一份卡片樣式——卡片外殼已由 #report-inline-view 提供")


class PreviewRenderTests(unittest.TestCase):
    """前端預覽要真的畫出來（使用者：預覽區塊敘述統計做一下，不用圖）。"""

    def setUp(self):
        from pathlib import Path

        self.html = Path("backend/app/static/index.html").read_text(encoding="utf-8")

    def test_frontend_renders_descriptive_stats(self):
        self.assertIn(
            "descriptive_stats", self.html,
            "前端沒有消費 descriptive_stats——後端算了但沒人顯示")

    def test_frontend_has_a_named_block(self):
        self.assertIn(
            "敘述統計", self.html,
            "前端沒有敘述統計區塊的標題")

    def test_no_chart_in_descriptive_stats(self):
        """使用者明示「不用圖」——這一節是純數字，不得引入圖表資產。

        ⚠ 精準取該函式本體再比對，不用「附近幾行」的字元窗——窗會隨無關的
        程式碼移動而誤報或漏報，那種測試紅起來沒人知道是不是真的壞了。
        """
        start = self.html.find("function renderDescriptiveStatsHtml(")
        self.assertGreater(start, -1, "找不到 renderDescriptiveStatsHtml")
        end = self.html.find("\nfunction ", start + 1)
        self.assertGreater(end, start, "取不到函式結尾")
        body = self.html[start:end]
        for forbidden in ("chart_url", "<svg", "chart-media", "chart_"):
            self.assertNotIn(
                forbidden, body,
                f"敘述統計區塊出現 {forbidden}——使用者明示這一節不用圖")

    def test_frontend_does_not_compute_stats_itself(self):
        """🔴 數字由後端供給，前端不自算——前端算一份就是第二個定義處。"""
        start = self.html.find("function renderDescriptiveStatsHtml(")
        end = self.html.find("\nfunction ", start + 1)
        body = self.html[start:end]
        for forbidden in ("reduce(", "filter(", ".length +"):
            self.assertNotIn(
                forbidden, body,
                f"敘述統計在前端自行計算（{forbidden}）——數字必須由後端供給")


if __name__ == "__main__":
    unittest.main()
