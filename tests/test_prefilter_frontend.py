"""初階篩選的前端（切片 E，WSP-013／PRE-004／PRE-005／PRE-008）。

## 本檔守的六件事

1. **瀏覽頁有入口且顯示待辦數**——數字取自後端權威 API，前端不自數。
2. **瀏覽頁不承載作業**——關鍵字編輯／確認／裁決都在獨立頁。
3. **不加左導覽項**——只能自瀏覽頁入口進入（沿案件比對既有前例）。
4. 🔴 **確認畫面不得顯示 SQL**——使用者看不懂，秀出來只會讓人不敢按。
5. 🔴 **AI 建議四種狀態各有明確樣子**——尚無建議與無判讀依據不得留白。
6. 🔴 **SSE 建議增量填入不得干擾使用者**——不重繪表格、不動勾選、不位移。

⚠ 本檔是靜態斷言（讀 `index.html` 原始碼）。它抓不到「函式存在但行為錯」，
但抓得到「接線漏了」——而 2026-08-21 的 D-5 實例（移除後端模組時漏清前端
接線）正是後者，而且它讓**整個前端掛掉**，語法檢查抓不到。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
        / "index.html")


class PrefilterFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_text(encoding="utf-8")

    def _fn(self, name: str) -> str:
        """取出某個 JS 函式的本體（到下一個頂層 function 為止）。"""
        start = self.html.find(f"function {name}(")
        self.assertGreater(start, -1, f"找不到 {name}")
        end = self.html.find("\nfunction ", start + 1)
        self.assertGreater(end, start, f"取不到 {name} 的結尾")
        return self.html[start:end]

    def _strings(self, body: str) -> str:
        """只取被放進 HTML 的字面字串（單引號），排除程式邏輯。"""
        return " ".join(re.findall(r"'([^']*)'", body))

    # ── E.4 分派與導覽 ──────────────────────────────────
    def test_dispatched_from_render_main(self):
        body = self._fn("renderMain")
        self.assertIn("'prefilter'", body, "renderMain 沒有分派初階篩選頁")
        self.assertIn("renderPrefilter", body)

    def test_render_prefilter_exists(self):
        """🔴 分派得出去就必須接得住——否則是懸空參照。"""
        self.assertIn("function renderPrefilter(", self.html,
                      "renderMain 分派到一個不存在的函式")

    def test_no_left_nav_item(self):
        """⚠ 不加左導覽項：只能自瀏覽頁入口進入（沿案件比對前例）。"""
        self.assertNotIn(
            'data-nav="prefilter"', self.html,
            "加了左導覽項——定案是只從瀏覽頁入口進入")

    # ── E.1／E.5 入口與待辦數 ───────────────────────────
    def test_browse_has_entry(self):
        # ⚠ 入口是寫在 HTML 字串裡的 onclick，引號被跳脫成 `navTo(\'prefilter\')`。
        #   去掉反斜線再比，否則測的是跳脫寫法而不是「有沒有入口」。
        body = self._fn("renderBrowse").replace("\\'", "'")
        self.assertIn("navTo('prefilter')", body, "瀏覽頁沒有初階篩選入口")

    def test_entry_shows_todo_count_from_backend(self):
        """🔴 待辦數取自權威 API，前端不自數。

        ⚠ 前端自數會變成第二份計數邏輯，兩份必然漂移——本專案已反覆踩過。
        """
        self.assertIn("prefilter/summary", self.html, "沒有呼叫待辦數端點")
        loader = self._fn("loadPrefilterSummary")
        self.assertIn("todo_count", loader, "沒有使用後端算好的 todo_count")
        for bad in (".length", ".reduce(", ".filter("):
            with self.subTest(bad=bad):
                self.assertNotIn(
                    bad, loader,
                    f"待辦數在前端自行計算（{bad}）——數字必須由後端供給")

    # ── E.2 瀏覽頁不承載作業 ────────────────────────────
    def test_browse_does_not_carry_prefilter_operations(self):
        body = self._fn("renderBrowse")
        for op in ("negative-keywords", "prefilter/apply", "prefilter/reviews",
                   "createNegativeKeyword", "decidePrefilterReview"):
            with self.subTest(op=op):
                self.assertNotIn(
                    op, body,
                    f"瀏覽頁承載了 {op}——定案是一切作業都在獨立頁")

    # ── E.3 六段 ────────────────────────────────────────
    def test_prefilter_page_has_six_sections(self):
        body = self._fn("renderPrefilter")
        for section in ("範圍", "負面關鍵字", "比對詞", "命中預覽",
                        "待裁決", "剔除名單"):
            with self.subTest(section=section):
                self.assertIn(section, body, f"初階篩選頁缺少「{section}」段")

    def test_scope_description_is_editable(self):
        """C2.1：範圍描述要能填、能存（PRE-008 的判讀依據）。"""
        body = self._fn("renderPrefilter")
        self.assertIn("prefilter-scope", body, "沒有範圍描述輸入框")
        self.assertIn("savePrefilterScope", body, "範圍描述不能儲存")
        self.assertIn("function savePrefilterScope(", self.html)

    # ── E.6 不得顯示 SQL ────────────────────────────────
    def test_page_shows_no_sql(self):
        """🔴 使用者看不懂 SQL；秀出來只會讓人不敢按。

        ⚠ 檢查對象是**要渲染給使用者看的字串**，不是整份原始碼
        ——後端查詢當然有 SQL，那不是使用者看得到的東西。
        """
        rendered = self._strings(self._fn("renderPrefilter"))
        for sql in ("SELECT", "WHERE", "ILIKE", "~*", "\\m", "regexp"):
            with self.subTest(sql=sql):
                haystack = rendered.upper() if sql.isupper() else rendered
                self.assertNotIn(sql, haystack,
                                 f"確認畫面出現 SQL 片語「{sql}」")

    # ── E.12 確認畫面要有判斷依據（2026-08-21 使用者實測後補）──
    #
    # 🔴 使用者實測看到「機器 → automaton／engine／machin／mechanism／mechaniz」
    # 而問「為啥會有被拼字少字母?」——那些是**詞幹**（比對採前綴詞界），
    # 但畫面沒說，看起來像壞掉。
    #
    # ⚠ 更嚴重的是 `engine` 會命中 `engineering`。使用者沒有依據判斷哪個詞
    # 太寬，就只剩「全部照按」（可能誤剔）或「全部不敢按」（功能等於沒有）。

    def test_confirm_screen_explains_stems(self):
        """詞幹要有說明——不說的話看起來像拼錯。"""
        rendered = self._strings(self._fn("prefilterKeywordRow"))
        self.assertIn("詞幹", rendered, "確認畫面沒有解釋詞幹")

    def test_confirm_screen_shows_hit_counts(self):
        """🔴 每個待確認的詞要顯示件數與實際命中的詞形。"""
        self.assertIn("prefilter/term-counts", self.html,
                      "沒有呼叫試算端點")
        body = self._fn("loadPrefilterTermCounts")
        self.assertIn("patent_count", body, "沒有顯示件數")
        self.assertIn("forms", body, "沒有顯示實際命中的詞形")

    def test_term_counts_only_for_unconfirmed(self):
        """⚠ 已確認的詞不必再試算——那是 `/prefilter/preview` 的事，
        重複打一次只是多花一輪查詢。"""
        body = self._fn("loadPrefilterTermCounts")
        self.assertIn("terms_confirmed", body,
                      "沒有區分已確認／未確認，會對全部關鍵字重算")

    # ── E.7 命中原因顯示文本 ────────────────────────────
    def test_hit_reason_shows_snippet_text(self):
        """🔴 使用者 2026-08-21：「命中原因改顯示文本」。

        ⚠ 只給「被 mow 命中」，使用者無從判斷那是不是誤剔——正式庫
        #591「VEHICLE WITH UNDER-BODY BLOWER」其實是割草載具。
        """
        body = self._fn("renderPrefilterReviewRows")
        self.assertIn("snippet", body, "命中原因沒有顯示原文")
        self.assertIn("keyword", body, "命中原因沒有顯示是哪個關鍵字")
        self.assertIn("label", body, "命中原因沒有顯示命中在哪個欄位")

    # ── E.8 AI 建議四種狀態 ─────────────────────────────
    def test_suggestion_has_four_distinct_states(self):
        """🔴 尚無建議與無判讀依據**不得留白**。

        ⚠ 空白會被讀成「沒問題」，那正是缺席型偏差；而「還沒跑」與
        「跑了但沒依據」混在一起的話，使用者會一直等一個不會來的東西。
        """
        body = self._fn("prefilterSuggestionCell")
        self.assertIn("no_basis", body, "沒有處理『無判讀依據』")
        self.assertIn("'keep'", body, "沒有處理『建議保留』")
        self.assertIn("'exclude'", body, "沒有處理『建議剔除』")
        rendered = self._strings(body)
        for text in ("尚無建議", "無判讀依據", "建議保留", "建議剔除"):
            with self.subTest(text=text):
                self.assertIn(text, rendered, f"四種狀態缺少「{text}」的文案")

    def test_suggestion_does_not_preselect_decision(self):
        """🔴 AI 只能建議，使用者才有決定權。

        ⚠ 建議不得預選、不得讓按鈕變灰或消失——那會把「建議」變成
        「預設答案」，使用者只會一路按下去。
        """
        body = self._fn("renderPrefilterReviewRows")
        for bad in ("checked", "disabled"):
            with self.subTest(bad=bad):
                self.assertNotIn(
                    bad, body,
                    f"待裁決列出現 {bad}——建議不得預選或封鎖裁決")

    # ── E.10 SSE 增量填入 ───────────────────────────────
    def test_suggestion_patch_does_not_rerender_table(self):
        """🔴 使用者 2026-08-21：「不要整頁重載也不要影響到使用者」。

        ⚠ 既有架構的預設是「事件當失效通知、資料重取後**整塊重繪**」
        （refreshBrowsePreservingDetails 就是重繪完再把展開列補回來）。
        那會重置捲動與勾選，正是使用者說不要的。
        """
        body = self._fn("patchPrefilterSuggestions")
        self.assertIn("innerHTML", body, "沒有實際更新格子內容")
        for bad in ("renderPrefilter(", "renderPrefilterReviewRows("):
            with self.subTest(bad=bad):
                self.assertNotIn(
                    bad, body,
                    f"建議填入時呼叫了 {bad}——那是整塊重繪，會重置捲動與勾選")

    def test_suggestion_cell_reserves_height(self):
        """🔴 建議格要預留固定高度。

        ⚠ 填入撐高列會把使用者正在看的位置往下推——你在看第 80 列時
        第 20 列長高，整頁往下跳。那正是「影響到使用者」。
        """
        body = self._fn("prefilterSuggestionCell")
        self.assertIn("min-height", body,
                      "建議格沒有預留高度——填入時會位移")

    def test_refresh_targets_registered(self):
        """E.9：兩條 AI 線的刷新目標必須指到實際資源，不能留空陣列。"""
        block = self.html[self.html.find("const JOB_REFRESH_TARGETS"):]
        block = block[:block.find("};")]
        for job in ("ai:keyword_expand", "ai:prefilter_review"):
            with self.subTest(job=job):
                line = next((ln for ln in block.splitlines() if job in ln), "")
                self.assertTrue(line, f"{job} 沒有登記刷新目標")
                self.assertNotIn(
                    "[]", line,
                    f"{job} 的刷新目標還是空陣列——頁面已經建好了")

    def test_refresh_resources_exist(self):
        """🔴 指到的資源必須有對應的刷新函式。

        ⚠ `RESOURCE_REFRESHERS` 是 const 字面量、**載入時即求值**，
        指向不存在的函式會 ReferenceError 讓整個前端掛掉——而語法檢查
        抓不到（未定義參照不是語法錯），只有真的開頁面才會現形。
        2026-08-21 移除 deckExports 時就是踩這個。
        """
        block = self.html[self.html.find("const RESOURCE_REFRESHERS"):]
        block = block[:block.find("};")]
        for fn in re.findall(r"run:\s*(\w+)", block):
            with self.subTest(fn=fn):
                self.assertRegex(
                    self.html, rf"(function|const|let|var)\s+{fn}\b",
                    f"RESOURCE_REFRESHERS 指到不存在的 {fn}——前端會整個掛掉")


if __name__ == "__main__":
    unittest.main()
