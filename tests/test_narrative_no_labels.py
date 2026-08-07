"""PPT 要點取消固定標籤（2026-08-07 使用者定案，推翻 2026-08-04 三層定案）。

## 定案

「ppt解讀格式不用再定標籤，但一樣要能解釋現象背後的原因」。

- 要點改**自由條列**（條數依版面容量），不再強制「現況／意涵／後續」標籤與固定三段。
- 保留的硬要求（換了驗法，不是拿掉）：
  1. **至少一條帶統計數字**——原鎖四綁在「現況」標籤上，改為頁級檢查。
  2. **至少一條解釋成因**——原鎖五驗「有沒有意涵標籤」，標籤沒了改驗
     因果語彙（因／由／反映／顯示…）。⚠ 這是啟發式，驗得到「完全沒解釋」，
     驗不出「解釋得對不對」——後者一直都靠 prompt 與人工，鎖五時代也一樣。
- `label` 欄位仍容忍（舊 narratives 檔帶著它照渲染），只是不再要求、不再檢查。
"""
from __future__ import annotations

import unittest

from backend.app.worker import ai_narrative_runner as runner


def _wrap(points):
    return {"based_on_version": "v", "reports": {"application_trend": {"variants": {
        "default": {"points": points, "headline": "h",
                    "text": "2022 年申請 15 件，高峰來自 13 個新家族、2024 年 11 件投入。\n\n近兩年回落與公開時滯重疊。\n\n補充段。"}}}}}


class LabelFreePointsTests(unittest.TestCase):
    GOOD = [
        {"text": "2022 年申請 15 件為全期高峰"},
        {"text": "高峰來自 13 個新家族投入，非同族延伸", "emphasis": True},
        {"text": "近兩年回落與公開時滯重疊，尚不能斷定降溫"},
    ]

    def _warnings(self, points, key="application_trend"):
        data = _wrap(points)
        return [w for w in runner.validate_narrative_contract(data)
                if key in w or "points" in w or "成因" in w or "數字" in w]

    def test_label_free_points_pass(self):
        """無標籤、有數字、有成因解釋 → 零警告（新常態）。"""
        self.assertEqual(self._warnings(self.GOOD), [])

    def test_page_without_numbers_warns(self):
        pts = [{"text": "布局集中於少數申請人"}, {"text": "因主要玩家由同一陣營主導"}]
        hits = [w for w in self._warnings(pts) if "數字" in w]
        self.assertTrue(hits, "整頁沒數字要警告（原鎖四的頁級版）")

    def test_page_without_causal_explanation_warns(self):
        pts = [{"text": "2022 年申請 15 件"}, {"text": "2024 年 11 件次高"}]
        hits = [w for w in self._warnings(pts) if "成因" in w]
        self.assertTrue(hits, "只描述現象不解釋成因要警告（原鎖五的無標籤版）")

    def test_legacy_labeled_points_still_accepted(self):
        """舊格式帶標籤照過（label 容忍不檢查），不逼重產歷史版本。"""
        pts = [{"label": "現況", "text": "2022 年 15 件高峰"},
               {"label": "意涵", "text": "反映新家族集中投入"}]
        self.assertEqual(self._warnings(pts), [])

    def test_prompt_no_longer_mandates_labels(self):
        from pathlib import Path

        prompt = runner.build_prompt(Path("X"), "v")
        self.assertNotIn("不得增減、不得改名", prompt)
        self.assertIn("成因", prompt, "prompt 必須要求解釋現象背後的原因")


if __name__ == "__main__":
    unittest.main()
