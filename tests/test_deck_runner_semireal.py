"""deck runner 的**機械步真跑**驗證（半真：只有 CLI 是假的）。

## 為什麼單元測試不夠

fake 步驟「假裝上一步產出了什麼」——runner 對腳本 argv 的假設錯了，
fake 版照樣全綠。2026-08-14 實際發生：runner 給 `make_deck.py` 第 4 參數
（SVG 輸出目錄），但腳本當時只吃 3 個，**SVG 從來沒被產出**；單元測試
因 fake 假裝有而全綠，真跑第一次就炸。本檔就是那次的 regression。

## 範圍

真 subprocess（assemble→plan→fit→check→make→audit→shoot）＋真素材＋
真 Chromium；CLI 假接（撰稿＝放入 skill_verify 的真實 content.json，
目視＝pass）。⚠ 驗不了 CLI 撰稿品質與目視判斷——那是 tasks 4.2 組合驗收
用真 CLI 的事（燒 token，需使用者允許）。

素材不在（CI／別台機器）時整檔 skip——這是開發機 acceptance driver，
比照 `RealChartIntegrationTests` 的模式。
"""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.worker import ai_report_deck_runner as deck
from backend.app.worker.cli_gateway import CliResult

OUTPUT = Path(r"D:\力山\專案\專利_ppt自動\output")
VERSION = "report_trial_20260811_094014"
CONTENT_SRC = OUTPUT / "skill_verify" / "work" / "content.json"


@unittest.skipUnless(
    (OUTPUT / VERSION / "report_data.json").is_file() and CONTENT_SRC.is_file(),
    "開發機真素材不在（版本目錄或 skill_verify content.json 缺）")
class DeckRunnerSemiRealTests(unittest.TestCase):
    """機械步全真跑。⚠ 慢測（Chromium fit＋逐頁截圖，約 20 秒）。"""

    def test_full_mechanical_chain_with_real_material(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        work_root = base / "work"
        artifact_root = base / "artifacts"
        prompts: list[str] = []

        def semi_real_cli(argv, timeout):
            prompts.append(argv[2])
            work = work_root / VERSION
            if len(prompts) == 1:
                shutil.copy2(CONTENT_SRC, work / "content.json")
                return CliResult(0, '{"result": "content copied"}', "")
            (work / "visual_verdict.json").write_text(
                json.dumps({"pass": True, "findings": []}), encoding="utf-8")
            return CliResult(0, '{"result": "pass"}', "")

        summary = deck.run_deck(
            VERSION, root=OUTPUT, work_root=work_root,
            artifact_root=artifact_root, cli_runner=semi_real_cli)

        self.assertEqual(summary["based_on_version"], VERSION)
        self.assertGreaterEqual(summary["page_count"], 10)
        vdir = artifact_root / VERSION
        self.assertTrue((vdir / "deck.pptx").is_file())
        self.assertTrue((vdir / "manifest.json").is_file())
        self.assertEqual(len(list((vdir / "pages").glob("*.png"))),
                         summary["page_count"])
        # 封面素材（tasks 2.4）：真素材的 workspace 名要進撰稿 prompt
        self.assertIn("滑雪機", prompts[0])


if __name__ == "__main__":
    unittest.main()
