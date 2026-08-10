"""判讀說明頁的口徑文字必須從引擎傳到組版端（P1 task 2.4）。

動因（2026-08-10 查證）：`content_blocks.reader_guide_blocks()` 產出全報告共用的
四段口徑說明（計數單位／同族合併／共同申請／分類覆蓋），但**全庫只有測試在呼叫它**
——沒有任何生產端消費者。組版端 `build_ppt` 的 `reading_guide` 版型雖有 renderer，
內容卻走 `_render_points_page`，即由 CLI 規劃時自行編寫。

後果與 2026-07-31 `ENCODING_NOTES` 那次完全同型：同一份口徑規則有兩個落點
（引擎的 `population.py`／`reader_guide_blocks()` vs CLI 腦中的說法），各自演進、
不一致本身不會報錯。⚠ 組版 skill 會被 Installer 打包到使用者電腦、不得 import
本 repo 模組，故唯一可行的傳遞方式就是走 `report_data.json`——與 `encoding_notes`
同一條通道，不另開新鍵路徑。

本測鎖住三件事：①引擎有輸出 ②輸出即唯一定義處（不重寫）③skill 文件指明照抄。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.reports import content_blocks
from backend.app.reports.chart_runner import table_display_spec

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 2026-08-10 PPT 線移除：解讀契約文件遷至 backend/app/worker/prompts/。
SKILL_DIR = PROJECT_ROOT / "backend" / "app" / "worker" / "prompts"


class ReaderGuideReachesCliTests(unittest.TestCase):
    """四段口徑說明必須沿 report_data.json 的 table_display 交給組版端。"""

    def test_table_display_spec_carries_reader_guide(self):
        """`table_display` 必須含 reader_guide——否則 CLI 讀不到，只能自己編。"""
        spec = table_display_spec({})
        self.assertIn(
            "reader_guide", spec,
            "table_display 需輸出 reader_guide，與 encoding_notes 走同一條傳遞通道",
        )

    def test_reader_guide_equals_single_definition(self):
        """內容須逐字等於 content_blocks 的唯一定義處，不得在引擎端重寫一份。"""
        self.assertEqual(
            table_display_spec({})["reader_guide"],
            content_blocks.reader_guide_blocks(),
            "reader_guide 必須直接取自 content_blocks.reader_guide_blocks()",
        )

    def test_blocks_have_title_and_body(self):
        """組版端 `_render_points_page` 需要標題與內文兩欄；缺欄會靜默印空白。"""
        blocks = table_display_spec({})["reader_guide"]
        self.assertTrue(blocks, "口徑說明不得為空")
        for block in blocks:
            self.assertTrue(block.get("title"), f"缺標題：{block}")
            self.assertTrue(block.get("body"), f"缺內文：{block}")

    def test_content_standard_keeps_the_four_bias_angles(self):
        """判讀說明頁的四個偏差角度必須留在內容標準裡（從滑雪機 V2 p11 反解）。

        ⚠ 角度固定、內容依 workspace 換。這四條是範例可信度的來源；被刪掉或
        簡化成「說明資料限制」這種空話，CLI 就會退回寫模板句。
        """
        text = (SKILL_DIR / "content_standard.md").read_text(encoding="utf-8")
        for angle in ("資料涵蓋邊界", "同族放大效應", "小樣本不等於不重要",
                      "低密度要外部校正"):
            self.assertIn(angle, text, f"content_standard 缺偏差角度：{angle}")

    def test_content_standard_separates_caliber_from_bias(self):
        """必須寫明「口徑定義 ≠ 可觀測性偏差」——否則兩者會被互相頂替。"""
        text = (SKILL_DIR / "content_standard.md").read_text(encoding="utf-8")
        self.assertIn(
            "不得拿它們去頂替", text,
            "content_standard 需明說口徑定義不能頂替可觀測性偏差四條",
        )

    def test_skill_docs_point_cli_at_the_engine_output(self):
        """skill 必須明寫「照抄引擎輸出」——只在程式裡輸出、文件沒講，CLI 仍會自己編。

        ⚠ 依本專案既有教訓：規則沒寫成組版端讀得到的指示，等於沒有規則。
        """
        docs = "\n".join(
            path.read_text(encoding="utf-8")
            for path in SKILL_DIR.rglob("*.md")
        )
        self.assertIn(
            "table_display.reader_guide", docs,
            "skill 文件需指明判讀說明頁照抄 report_data.json.table_display.reader_guide",
        )


if __name__ == "__main__":
    unittest.main()
