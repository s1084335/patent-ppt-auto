"""路線圖頁併入結論頁、期程整個拿掉（tasks §7d，2026-08-18 使用者裁決）。

## 為什麼拿掉期程

結論頁四欄每一欄都有引擎來源（發現逐字比對、行動五選一），
**唯獨路線圖的時間桶沒有任何資料支撐**——`短期 0–3 個月` 是 CLI 憑空填的。
系統不知道人力、預算與產品排程，那個數字必然是編的。

## 排序改由外部訊號決定

該主題有多少件**他人的審查中案件**（§7e 已供給）。那是對手給的時間壓力、
可查證；不是我們假設的月份。

## 為什麼合併而不是留兩頁

拿掉期程後，路線圖頁與結論頁功能重疊（結論頁已有 主題｜發現｜意涵｜行動）。
合併成依 `ACTION_VERBS` 分組的行動盤，少一頁也少一份要維護的一致性。
"""
from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "html-report-to-deck"
sys.path.insert(0, str(SKILL / "scripts"))


class TemplateHasNoTimeBucketsTests(unittest.TestCase):
    def _template_text(self) -> str:
        """只取**內容欄位**，排除 `_xxx說明` 這類給 CLI 看的註記。

        ⚠ 本測試第一版直接掃整份 JSON，結果被**自己寫的警語**餵飽——
        `_排序說明` 裡寫著「不要自己排期程，『短期 0–3 個月』那種數字必然是編的」，
        測試看到「短期」就紅。與同期兩次（`-- UNION` 註解、`存活家族共` 註解）同型：
        **只斷言字串出現在整份檔案**，說明文字也算。
        """
        data = json.loads(
            (SKILL / "references" / "content-template.json").read_text(encoding="utf-8"))
        content = {k: v for k, v in data.items() if not k.startswith("_")}
        return json.dumps(content, ensure_ascii=False)

    def test_roadmap_block_is_gone(self):
        """🔴 範本不得再有 roadmap 區塊——CLI 照抄範本。"""
        data = json.loads(self._template_text())
        leftovers = [k for k in data if k.startswith("roadmap")]
        self.assertEqual(
            leftovers, [],
            f"範本仍有路線圖欄位：{leftovers}——那頁已併入結論頁")

    def test_no_time_bucket_wording(self):
        """期程字樣整個拿掉：它是全份唯一沒有資料支撐的欄位。"""
        text = self._template_text()
        for phrase in ("短期", "中期", "長期", "0–3 個月", "1–3 年"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(
                    phrase, text,
                    f"範本仍有「{phrase}」——系統不知道人力與排程，那個數字必然是編的")


class ComposeDropsRoadmapPageTests(unittest.TestCase):
    def test_compose_no_longer_emits_roadmap(self):
        import deck_layout

        from tests.source_assertions import executable_source

        # ⚠ 剝註解：`_compose` 的註解裡寫著「slide_roadmap 暫留供追溯」，
        #   直接掃原始碼會被自己的說明絆倒（同日第四次，見 source_assertions）
        src = executable_source(inspect.getsource(deck_layout._compose))
        self.assertNotIn(
            "slide_roadmap", src,
            "仍在產路線圖頁——已裁決併入結論頁（取代不並存）")


class ConclusionsGroupsByActionTests(unittest.TestCase):
    """結論頁改為依動詞分組、依外部訊號排序。"""

    def test_conclusions_row_can_carry_the_external_signal(self):
        """列要能帶「他人審查中件數」，否則排不了序。"""
        data = json.loads(
            (SKILL / "references" / "content-template.json").read_text(encoding="utf-8"))
        row = ((data.get("conclusions") or {}).get("rows") or [{}])[0]
        self.assertIn(
            "pending_count", row,
            "結論列沒有審查中件數——排序訊號沒有落點（§7e 已在引擎供給）")

    def test_renderer_sorts_by_action_then_signal(self):
        """分組序＝動詞宣告序，組內序＝pending_count 由多到少。

        ⚠ 2026-08-19 改寫：原本斷言 `slide_conclusions` 原始碼裡有
        「ACTION_VERBS」與「pending_count」兩個字串。前者只出現在 **docstring**
        ——註解餵出來的假通過（本專案同型陷阱第 5 次）；後者在排序抽成純函式
        `conclusion_groups` 之後就不在該函式裡了，於是這條紅得莫名其妙。
        改驗行為：排序**做對了沒有**，而不是字串**寫在哪個函式**。
        """
        import deck_layout

        groups = deck_layout.conclusion_groups([
            {"topic": "少", "action": "追蹤", "pending_count": 1},
            {"topic": "後", "action": "佈局", "pending_count": 5},
            {"topic": "多", "action": "追蹤", "pending_count": 9},
        ])
        self.assertEqual(
            [v for v, _ in groups], ["佈局", "追蹤"],
            "分組沒有照 ACTION_VERBS 宣告序——會變成第二份順序定義")
        self.assertEqual(
            [r["topic"] for r in dict(groups)["追蹤"]], ["多", "少"],
            "組內沒有依外部訊號（審查中件數）由多到少——拿掉期程後就沒有先後感了")


class GuideUpdatedTests(unittest.TestCase):
    def test_narrative_guide_drops_time_buckets(self):
        text = (SKILL / "references" / "narrative.md").read_text(encoding="utf-8")
        self.assertNotIn(
            "短期 0", text,
            "寫作指引仍教期程寫法")


if __name__ == "__main__":
    unittest.main()
