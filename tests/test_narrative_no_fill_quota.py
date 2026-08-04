"""要點不得因為「寫得不夠多」而被判不合格（2026-08-04 使用者定案）。

## 背景

`NARRATIVE_MIN_FILL_RATIO = 0.6`（鎖七）是 08-03 加的，起因是 IPC L4 只寫了
81/432 字（18.8%）。但它造成的後果比原問題更糟：**逼 CLI 寫到接近版面上限**
→ 必然踩到邊界 → 尾端整條被丟，而丟的都是排在後面的「意涵」「後續」。

🔴 使用者定案原話：

> 「拿掉字數下限，但可以給他格式，畢竟**現在版面是符合目標的只是資訊被丟棄要修**」

## 這支測什麼

1. **字數少不再是警告**——內容完整（有意涵、有覆蓋）就算合格
2. **格式（結構）要求仍在**——標籤結構、條數上限、每條字數上限都還要驗

⚠ 不是「把所有檢查都拿掉」。拿掉的只有**字數下限**這一項。
"""
from __future__ import annotations

import unittest

from backend.app.worker import ai_narrative_runner as runner


def _narr(points):
    return {
        "reports": {
            "demo": {
                "variants": {
                    "default": {
                        "headline": "示範標題",
                        "points": points,
                        "text": "".join(p["text"] for p in points),
                    }
                }
            }
        }
    }


class NoFillQuotaTests(unittest.TestCase):
    CAPACITY = {"demo:default": {"max_points": 8, "max_chars": 50}}

    def test_short_but_complete_passes(self):
        """只寫兩條、字數遠低於上限，但有現況也有意涵——不得因為「太短」報警。"""
        points = [
            {"label": "現況", "text": "本頁共 8 件，A63B 佔 5 件。"},
            {"label": "意涵", "text": "技術集中單一大類，差異化要往細分層找。"},
        ]
        warnings = runner.validate_narrative_contract(_narr(points), self.CAPACITY)
        fill = [w for w in warnings if "版面用量" in w]
        self.assertEqual(fill, [], f"字數少不該報警：{fill}")

    def test_fill_ratio_constant_is_gone(self):
        """常數本身要移除——留著它就會有人再把它接回去。"""
        self.assertFalse(hasattr(runner, "NARRATIVE_MIN_FILL_RATIO"),
                         "NARRATIVE_MIN_FILL_RATIO 應已移除")

    def test_over_length_still_warns(self):
        """⚠ 上限仍要守：拿掉的是下限，不是所有字數檢查。"""
        points = [
            {"label": "現況", "text": "字" * 80},
            {"label": "意涵", "text": "字" * 10},
        ]
        warnings = runner.validate_narrative_contract(_narr(points), self.CAPACITY)
        self.assertTrue(any("超限" in w for w in warnings), f"超長沒被抓到：{warnings}")

    def test_missing_implication_still_warns(self):
        """⚠ 內容完整性仍要守：全是「現況」＝只複述數據，不說意義。"""
        points = [
            {"label": "現況", "text": "本頁共 8 件。"},
            {"label": "現況", "text": "A63B 佔 5 件。"},
        ]
        warnings = runner.validate_narrative_contract(_narr(points), self.CAPACITY)
        self.assertTrue(any("意涵" in w for w in warnings), f"缺意涵沒被抓到：{warnings}")


if __name__ == "__main__":
    unittest.main()
