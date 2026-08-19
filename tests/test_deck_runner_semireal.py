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
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.worker import ai_report_deck_runner as deck
from backend.app.worker.cli_gateway import CliResult

OUTPUT = Path(r"D:\力山\專案\專利_ppt自動\output")
VERSION = "report_trial_20260811_094014"
CONTENT_SRC = OUTPUT / "skill_verify" / "work" / "content.json"


def _synth_conclusions(work: Path) -> dict | None:
    """依 intake 的 `topic_facts.json` 造一份最小結論頁（假 CLI 的代打）。

    2026-08-19：結論頁閘門改為「引擎有主題就必須有結論」（原本沒宣告會被無條件
    放行，於是那頁根本不產出而閘門一聲不吭）。半真素材的 content.json 是那道
    閘門之前錄的，所以缺 conclusions ——**這是閘門抓對了，不是誤擋**。
    真 CLI 會寫這一段，假 CLI 就在這裡代打。

    ⚠ 刻意**不涵蓋全部主題**：故意留幾個進 `uncovered`，讓機械鏈也跑到對帳
    那條路徑。全涵蓋會讓覆蓋率閘門直接 return，等於沒驗到。
    ⚠ 發現欄逐字取自 topic_facts，不自行改寫（逐字比對閘門盯著）。
    """
    facts_path = work / "topic_facts.json"
    if not facts_path.is_file():
        return None
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    if not facts:
        return None
    # 一列多動詞：讓半真鏈也涵蓋 2026-08-19 的多選格式，不只單字串。
    actions = ["追蹤", ["迴避設計", "追蹤"], "細讀比對"]
    written = facts[:len(actions)]
    return {
        "title": "綜合結論：各主題的研發意涵與專利行動",
        "takeaway": "半真素材：機械鏈驗證用，判讀文字非真 CLI 產出。",
        "covered": f"{len(written)}/{len(facts)}",
        "uncovered": [{"topic": f["topic"], "reason": "半真測試素材未撰稿"}
                      for f in facts[len(actions):]],
        "rows": [{"topic": f["topic"], "finding": f["finding"],
                  "reading": "半真素材佔位判讀句，不代表真實分析結論。",
                  # ⚠ §9.3：`依據：` 紀律移到結論列，代打也要帶
                  "evidence": "依據：半真素材主題 " + f["topic"],
                  "action": a, "pending_count": 0}
                 for f, a in zip(written, actions)],
    }


def _write_p2_content(path: Path) -> None:
    """把既有半真素材轉成目前 P2 content contract，避免改動 output 原檔。"""
    content = json.loads(CONTENT_SRC.read_text(encoding="utf-8"))
    content.pop("read_me", None)
    content.pop("chart_rule", None)
    if (cc := _synth_conclusions(path.parent)) is not None:
        content["conclusions"] = cc
    for rec in content.get("recommendations") or []:
        lines = [str(line) for line in rec.get("lines") or []]
        if "依據：" not in "\n".join(lines):
            if lines and "情報依據" in lines[0]:
                lines[0] = lines[0].replace("情報依據", "依據", 1)
            else:
                lines.insert(0, "依據：半真實測試素材")
        rec["lines"] = lines
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


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
                _write_p2_content(work / "content.json")
                return CliResult(0, '{"result": "content copied"}', "")
            (work / "visual_verdict.json").write_text(
                json.dumps({"pass": True, "findings": []}), encoding="utf-8")
            return CliResult(0, '{"result": "pass"}', "")

        chained: list[str] = []

        def fake_narrative(version, **kw):
            """真版本目錄沒有 narratives.json → 前置會觸發；注入假產生器，
            否則會真跑 narrative（真 CLI、燒 token）。寫空殼 `{}` 進真目錄
            讓鏈繼續，addCleanup 負責清掉、不留污染。"""
            chained.append(version)
            (OUTPUT / VERSION / "narratives.json").write_text("{}", encoding="utf-8")
            return {}

        self.addCleanup(
            lambda: (OUTPUT / VERSION / "narratives.json").unlink(missing_ok=True))
        summary = deck.run_deck(
            VERSION, root=OUTPUT, work_root=work_root,
            artifact_root=artifact_root, cli_runner=semi_real_cli,
            ensure_narrative=fake_narrative)
        self.assertEqual(chained, [VERSION])   # 前置真的被觸發且只一次

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
