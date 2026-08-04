"""拆出來的每一頁都要講清楚自己是誰（2026-08-04 實機 J-2）。

## 症狀

第五輪實機 p13 與 p14 的標題**一字不差**，都是「主要申請人排名（Applicants）」。
但 p14 的圖是 `owner_ranking.svg`、資料來源寫「現專利權人排名」、
內容講的也是權利人（「現權利人以廈門帝瑪斯10件居首」）——**只有標題錯**。

## 根因：拆頁發生在 topic 決定之後

```
_expand_page_layout()          ← 這裡算 topic（此時還是一頁兩張圖）
  └─ topic = _chart_page_topic(spec, report_data)   依 charts[0]
_apply_layout_overrides()      ← 這裡才拆成兩頁
  └─ _split_multi_chart_page() 收窄了 report_keys，**但沒重算 topic**
```

⚠ `_split_multi_chart_page` 的 docstring 明寫「把 report_keys 一併收窄——否則兩頁
都掛著全部 report_key，會抓到同一段 narrative、印出兩張一模一樣的標題」。
它想防的就是這件事，卻漏了 topic 這一半。

## 判準

同一批圖拆出來的頁，**topic 必須各自對應自己那張圖**。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "scripts"
sys.path.insert(0, str(_SKILL))

import build_ppt as bp  # noqa: E402

_RUN = Path(__file__).resolve().parents[1] / "var" / "report_cache" / "report_trial_20260803_164251"


@unittest.skipUnless((_RUN / "report_data.json").exists(), "需要實機報表輸出")
class SplitPageTitleTests(unittest.TestCase):
    def _layout(self):
        report_data = json.loads((_RUN / "report_data.json").read_text(encoding="utf-8"))
        manifest = json.loads((_RUN / "artifact_manifest.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            charts = bp.ChartIndex(_RUN, Path(tmp), manifest)
            return bp.resolve_layout(report_data, charts, bp.Theme.load(), {})

    def test_split_pages_do_not_share_a_topic(self):
        """一圖一頁拆出來的頁，topic 不得重複。"""
        by_topic: dict[str, list[str]] = {}
        for spec in self._layout():
            if spec.is_appendix or not spec.charts:
                continue
            by_topic.setdefault(spec.topic, []).append(spec.charts[0])
        dupes = {topic: files for topic, files in by_topic.items() if len(set(files)) > 1}
        self.assertEqual(dupes, {}, f"不同的圖共用同一個標題：{dupes}")

    def test_owner_ranking_page_says_owner(self):
        """畫 owner_ranking 的那頁，標題要講「專利權人」不是「申請人」。"""
        for spec in self._layout():
            if spec.charts[:1] == ("owner_ranking.svg",) and not spec.is_appendix:
                self.assertIn("專利權人", spec.topic,
                              f"專利權人排名頁的標題是「{spec.topic}」")
                return
        self.skipTest("本次版面沒有 owner_ranking 單圖頁")


if __name__ == "__main__":
    unittest.main()
