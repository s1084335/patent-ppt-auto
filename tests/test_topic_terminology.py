"""報表成品的主題術語統一（2026-08-12 使用者定案）。

使用者原話：「我這個系統 BERTopic 做的叫技術主題，所以『群』這個用詞不要用」。

範圍＝**讀者看得到的成品文字**：表格欄名、卡片標題、讀圖須知、解讀 prompt
（它決定 CLI 寫出的字）。⚠ 例外：「IPC／CPC 主群組」是專利分類的官方用語
（main group），不在此列；程式內部註解與分類區操作 UI 的「分群」動詞另議。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.reports import chart_runner, content_blocks

PROMPTS = Path(__file__).resolve().parents[1] / "backend" / "app" / "worker" / "prompts"


class ColumnLabelTests(unittest.TestCase):
    def test_topic_columns_say_topic_not_cluster(self):
        labels = chart_runner.DATA_COLUMN_LABELS
        self.assertEqual(labels["topic_count"], "涉及技術主題")
        self.assertEqual(labels["new_topic_count"], "首現技術主題")

    def test_no_bertopic_cluster_wording_in_labels(self):
        """欄名不得出現「技術群」「分群」；IPC/CPC 主群組（官方用語）豁免。"""
        for key, label in chart_runner.DATA_COLUMN_LABELS.items():
            if "主群組" in label:
                continue
            self.assertNotIn("群", label, f"{key} 的表頭仍用群稱呼主題：{label!r}")


class SectionTitleTests(unittest.TestCase):
    def test_cluster_card_title_is_topic_analysis(self):
        src = Path(chart_runner.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"title": "分群分析"', src, "分群卡標題仍叫分群分析")
        self.assertIn('"title": "主題分析"', src)


class ReaderGuideTests(unittest.TestCase):
    def test_reader_guide_free_of_cluster_wording(self):
        text = "".join(b["title"] + b["body"] for b in content_blocks.reader_guide_blocks())
        self.assertNotIn("群", text, "讀圖須知仍以群稱呼主題")
        self.assertIn("主題", text, "計數單位應改以主題表述")


class NarrativePromptTests(unittest.TestCase):
    """prompt 決定 CLI 寫出的字——prompt 用群，成品就會用群。"""

    def test_prompts_free_of_cluster_topic_wording(self):
        for name in ("report-narrative-flow.md", "content_standard.md", "data_access.md"):
            text = (PROMPTS / name).read_text(encoding="utf-8")
            self.assertNotIn("技術群", text, f"{name} 仍用「技術群」")
            self.assertNotIn("分群主題", text, f"{name} 仍用「分群主題」")


if __name__ == "__main__":
    unittest.main()
