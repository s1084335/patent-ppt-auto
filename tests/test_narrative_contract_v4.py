"""narrative 契約 v3（三件套）＋規則 v4（2026-07-31 Phase 2）。

## 為什麼

實機 PPT 每頁 1 段 400–500 字、`bullet=0`。⚠ AI 沒有違規——舊規則就是教它寫散文：
「標準寫法是：先點出數據現象，再說明…最後收斂成…」。PPT 需要的是要點，
報表頁需要的是長文，同一份 `text` 無法兩用。

## 定案：三件套 ＋ 三道鎖

variant entry 升級為 `{headline, points[], text, ...}`：

- `headline` ≤20 字：一句判讀結論（PPT 標題「{主題}：{headline}」用）
- `points` 3–5 條、每條 `text` ≤40 字、可帶 `emphasis`（PPT 要點框用）
- `text`：長文照舊（報表頁用）——兩者各寫各的，不是互相截斷

三道鎖：① 契約形狀（skill 檔輸出契約 v3）② 規則明定數字（本檔測）
③ runner 程式驗證——超限／缺欄**不靜默**，收進 summary["contract_warnings"]
（⚠ 不 raise 也不截斷：narrative 是資料來源，截了就毀；截斷是 PPT 消費端
fallback 的職責。舊格式要能過渡，只警告）。

⚠ 字數上限是暫定值（theme.json v2 未落地），收成 runner 常數單一來源；
v2 落地後對框尺寸驗算，要改只改那一處。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.worker import ai_narrative_runner as runner

SKILL_MD = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "report-narrative-flow.md"


def _entry(headline="布局集中於 A63B", points=None, text=None):
    """合規樣本。⚠ v5 起長文必須「由要點逐條展開」：段落數不少於要點數、
    要點裡的數字也要出現在長文——所以 fixture 不能再用一句「長文」帶過。
    """
    if points is None:
        points = [
            {"label": "現況", "text": "A63B 47 件、F03G 僅 2 件", "emphasis": True},
            {"label": "意涵", "text": "同一方向反覆布局"},
            {"label": "後續", "text": "以 5 階細分類檢視斷層"},
            {"label": "現況", "text": "近三年新進者僅 1 家"},
        ]
    if text is None:
        text = ("A63B 累計 47 件，F03G 僅 2 件，資源明顯偏向前者。\n"
                "同一方向反覆布局，顯示技術路線已收斂。\n"
                "建議以 5 階細分類檢視斷層所在。\n"
                "近三年新進者僅 1 家，進入門檻偏高。")
    return {"headline": headline, "points": points, "text": text,
            "ai_model": "m", "prompt_version": "report_narrative_v5",
            "generated_at": "2026-07-31T00:00:00"}


def _narratives(entry):
    return {"based_on_version": "v", "reports": {"rk": {"variants": {"default": entry}}}}


class ContractConstantsTests(unittest.TestCase):
    """🔴 上限收成 runner 常數（單一來源）；PROMPT_VERSION 升 v4。"""

    def test_limits_single_source(self):
        self.assertEqual(runner.NARRATIVE_HEADLINE_MAX, 20)
        self.assertEqual(runner.NARRATIVE_POINT_TEXT_MAX, 55)
        self.assertEqual(runner.NARRATIVE_POINTS_MIN, 4)
        self.assertEqual(runner.NARRATIVE_POINTS_MAX, 7)

    def test_prompt_version_bumped(self):
        self.assertEqual(runner.PROMPT_VERSION, "report_narrative_v5")


class SkillRulesTests(unittest.TestCase):
    """🔴 skill 條文：契約 v3 形狀、數字上限入文、散文條文移除。"""

    @classmethod
    def setUpClass(cls):
        cls.md = SKILL_MD.read_text(encoding="utf-8")

    def test_contract_has_headline_and_points(self):
        for key in ("headline", "points", "emphasis"):
            with self.subTest(key=key):
                self.assertIn(key, self.md, f"輸出契約缺 {key}")

    def test_limits_written_in_rules(self):
        """規則要明定數字——沒有數字的「精簡」是無效指令。"""
        for token in ("20", "55", "4", "7"):
            with self.subTest(token=token):
                self.assertIn(token, self.md)
        self.assertIn("report_narrative_v5", self.md, "prompt_version 未升版")

    def test_prose_instruction_removed(self):
        """⚠ 舊散文指令必須移除——它與要點契約直接矛盾（AI 會二選一）。"""
        self.assertNotIn(
            "標準寫法是：先點出數據現象", self.md,
            "教 AI 寫散文的條文還在——headline/points 會被它壓過")

    def test_text_field_kept(self):
        """text 長文保留（報表頁用）——三件套不是取代，是並存。"""
        self.assertIn('"text"', self.md)


class PromptShapeTests(unittest.TestCase):
    """🔴 headless prompt 的形狀描述要含三件套。"""

    def test_prompt_mentions_headline_points(self):
        prompt = runner.build_prompt(Path("X:/run"), "v1")
        for key in ("headline", "points"):
            with self.subTest(key=key):
                self.assertIn(key, prompt, f"prompt 形狀描述缺 {key}")


class ValidateContractTests(unittest.TestCase):
    """🔴 validate_narrative_contract：合規靜默、違規逐條列出、舊格式警告。"""

    def test_compliant_entry_no_warnings(self):
        self.assertEqual(
            runner.validate_narrative_contract(_narratives(_entry())), [])

    def test_headline_too_long(self):
        w = runner.validate_narrative_contract(
            _narratives(_entry(headline="超" * 21)))
        self.assertEqual(len(w), 1)
        self.assertIn("headline", w[0])
        self.assertIn("rk", w[0], "warning 要指出是哪張報表")

    def test_points_count_out_of_range(self):
        """v5 起範圍是 4–7（原 3–6）：2 條太少、8 條太多。"""
        for n in (2, 8):
            with self.subTest(n=n):
                pts = [{"label": "l", "text": "t"} for _ in range(n)]
                w = runner.validate_narrative_contract(_narratives(_entry(points=pts)))
                self.assertTrue(any("points" in x for x in w), f"{n} 條未被警告")

    def test_point_text_too_long(self):
        pts = [{"label": "l", "text": "字" * 56}] + \
              [{"label": "l", "text": "ok"} for _ in range(3)]
        w = runner.validate_narrative_contract(_narratives(_entry(points=pts)))
        self.assertTrue(any("55" in x for x in w))

    def test_old_format_warns_not_raises(self):
        """⚠ 舊格式（只有 text）：警告但不炸——過渡期舊資料要能跑。"""
        old = {"text": "舊長文", "ai_model": "m",
               "prompt_version": "report_narrative_v3", "generated_at": "t"}
        w = runner.validate_narrative_contract(_narratives(old))
        self.assertTrue(any("headline" in x for x in w), "舊格式未被標記")


class SummaryWiringTests(unittest.TestCase):
    """🔴 warnings 要進 run_narrative 的 summary——前端任務進度才看得到。"""

    def test_summary_carries_contract_warnings(self):
        import inspect

        src = inspect.getsource(runner.run_narrative)
        self.assertIn("contract_warnings", src,
                      "run_narrative 未把契約警告放進 summary——違規靜默")
        self.assertIn("validate_narrative_contract", src,
                      "run_narrative 未呼叫契約驗證")


if __name__ == "__main__":
    unittest.main()
