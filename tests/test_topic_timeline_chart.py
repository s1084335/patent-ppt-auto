"""主題 × 時間圖（2026-08-10 使用者裁決新增）。

## 為什麼新增而不是改版

依「刪 > 改版 > 新增」的優先序先查過現有圖：

| 圖 | 軸 | 能不能承載主題×時間 |
|---|---|---|
| `opportunity_quadrant` | 申請人家數 × 件數 | ❌ 沒有時間軸 |
| `annual_trend` | 年份 × 件數 | ❌ 沒有主題維度 |
| `applicant_year_matrix` | 申請人 × 年份 | ❌ 主體是申請人不是主題 |

**主題 × 時間目前沒有任何圖**，資料卻早就備齊——`cluster_topic_table` 每列都帶
`early_count`／`recent_count`／`status`，只是被埋在表格欄位裡，讀者要心算才看得出
「哪個主題在起、哪個在退」。

使用者原話：「如果時間和主題能用圖呈現，為何要一直用表格？」

## 這張圖要回答什麼

```
馬達自鎖   早 0 → 近 6   全新戰場
風磁複合   早 2 → 近 9   加速中
立柱滑輪   早 5 → 近 2   ⚠ 唯一退潮
```

一眼看出技術重心的**移動方向**——那是表格看不出來的。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.reports.chart_runner import render_topic_timeline_chart

ROWS = [
    {"label": "拉繩滑雪模擬機構", "patent_count": 10, "early_count": 2,
     "recent_count": 7, "status": "申請成長"},
    {"label": "風磁複合阻力裝置", "patent_count": 11, "early_count": 2,
     "recent_count": 9, "status": "申請成長"},
    {"label": "立柱滑輪訓練機構", "patent_count": 7, "early_count": 5,
     "recent_count": 2, "status": "申請下降"},
    {"label": "馬達自鎖阻力機構", "patent_count": 6, "early_count": 0,
     "recent_count": 6, "status": "申請成長"},
]


class TopicTimelineChartTests(unittest.TestCase):
    """早期 vs 近期雙條，狀態用顏色編碼。"""

    def _svg(self, rows=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topic_timeline.svg"
            render_topic_timeline_chart(path, "技術主題演進", rows or ROWS)
            return path.read_text(encoding="utf-8")

    def test_every_topic_appears(self):
        """每個主題都要出現——漏一個就是漏掉一個技術方向。"""
        svg = self._svg()
        for row in ROWS:
            self.assertIn(row["label"], svg, f"缺主題 {row['label']}")

    def test_both_periods_are_drawn(self):
        """早期與近期都要畫——只畫一邊就看不出移動方向。"""
        svg = self._svg()
        self.assertIn("早期", svg)
        self.assertIn("近期", svg)

    def test_declining_topic_is_visually_distinct(self):
        """退潮主題要與成長主題視覺可分——那正是這張圖最該講的事。

        ⚠ 立柱滑輪 5→2 是唯一退潮者，讀者一眼要看得出來。
        """
        svg = self._svg()
        self.assertIn("申請下降", svg, "狀態要標示在圖上，不能只留在資料裡")

    def test_sorted_by_recent_activity(self):
        """依近期件數排序——讀者關心的是「現在誰在跑」。"""
        svg = self._svg()
        pos = [svg.index(r["label"]) for r in
               sorted(ROWS, key=lambda r: r["recent_count"], reverse=True)]
        self.assertEqual(pos, sorted(pos), "主題未依近期件數由多到少排列")

    def test_empty_rows_still_renders(self):
        """沒有資料時畫出空圖而非爆掉——分群未完成時也要能出檔。"""
        svg = self._svg([])
        self.assertIn("<svg", svg)

    def test_zero_early_count_is_drawn(self):
        """早期為 0 的主題（全新戰場）不得被當成缺資料而略過。

        ⚠ 馬達自鎖 0→6 是最重要的訊號之一：全新戰場。
        """
        svg = self._svg()
        self.assertIn("馬達自鎖阻力機構", svg)


if __name__ == "__main__":
    unittest.main()
