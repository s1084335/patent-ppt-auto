"""文件契約同步（tasks §5.1／§5.4–§5.7）。

## 要守什麼

CLI 與 prompt 讀的文件不得**教**已退場的東西。⚠ 但「提到」與「教」不同：

| 情形 | 判定 |
|---|---|
| 拿它當可用的欄位／頁型示範 | 🔴 紅——CLI 會照抄 |
| 在變更說明裡提到「它已移除」 | ✅ 正常——那是紀錄，刪掉反而讓下一個人不知道發生過什麼 |

⚠ 這個區分做不出來的話，只有兩種結局：要嘛閘門把所有變更說明都判成紅
（於是被關掉），要嘛放寬到什麼都抓不到。判準用**上下文**：
同一行有「已移除／已退場／原本」等變更詞就是紀錄。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CLI_DOCS = {
    "SKILL.md": ROOT / "skills/html-report-to-deck/SKILL.md",
    "narrative.md": ROOT / "skills/html-report-to-deck/references/narrative.md",
    "content-template.json": (
        ROOT / "skills/html-report-to-deck/references/content-template.json"),
    "report-narrative-flow.md": (
        ROOT / "backend/app/worker/prompts/report-narrative-flow.md"),
    "content_standard.md": ROOT / "backend/app/worker/prompts/content_standard.md",
    "data_access.md": ROOT / "backend/app/worker/prompts/data_access.md",
}

#: 已退場的東西 → 是哪一節移除的
RETIRED = {
    "roadmap": "§7d 路線圖頁併入結論頁",
    "recommendations": "§9.3 建議頁退場",
    "rec_title": "§9.3",
    "rec_takeaway": "§9.3",
    "只走外觀": "§4 用詞改「只走設計」",
    "技術+外觀": "§4 用詞改「技術+設計」",
    "外觀保護策略": "§4 用詞改「設計保護策略」",
}

#: 變更說明用詞——同一行出現這些就是**紀錄**不是**教學**
_CHANGE_MARKERS = ("已移除", "已退場", "原本", "不再", "改為", "改成", "取代",
                   "§7d", "§9.3", "§4", "2026-08-1", "2026-08-19")


class DocsDoNotTeachRetiredThingsTests(unittest.TestCase):
    def test_no_doc_teaches_a_retired_thing(self):
        offences: list[str] = []
        for name, path in CLI_DOCS.items():
            if not path.is_file():
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if any(m in line for m in _CHANGE_MARKERS):
                    continue          # 變更說明，不是教學
                for term, why in RETIRED.items():
                    if term in line:
                        offences.append(f"{name}:{i} 教「{term}」（{why}）：{line.strip()[:60]}")
        self.assertEqual(
            offences, [],
            "文件仍在教已退場的東西（CLI 會照抄）：\n  " + "\n  ".join(offences))

    def test_change_notes_are_preserved(self):
        """⚠ 反面：變更說明**不該**被清掉。

        把「roadmap 已移除」一起刪掉，下一個人只會看到一個沒有來歷的空缺，
        然後在某次重構時「順手加回來」。
        """
        skill = CLI_DOCS["SKILL.md"].read_text(encoding="utf-8")
        self.assertIn("§7d", skill, "SKILL.md 的變更說明被清掉了")


class TemplateMatchesLayoutRegistryTests(unittest.TestCase):
    """§5.6：範本示範的版型必須就是 `LAYOUTS`（§7a.2 的三處同步，這裡再驗一次）。"""

    def test_template_layouts_match_registry(self):
        import importlib.util
        import json
        import sys

        scripts = ROOT / "skills/html-report-to-deck/scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        spec = importlib.util.spec_from_file_location(
            "deck_layout", scripts / "deck_layout.py")
        dl = importlib.util.module_from_spec(spec)
        sys.modules["deck_layout"] = dl
        spec.loader.exec_module(dl)

        data = json.loads(CLI_DOCS["content-template.json"].read_text(encoding="utf-8"))
        shown = {p.get("layout") or "chart" for p in data.get("pages") or []}
        self.assertEqual(
            shown, set(dl.LAYOUTS),
            "範本示範的版型與 LAYOUTS 不一致——CLI 只照抄範本，"
            "沒示範的版型等於不存在")


class PromptCoversProducedReportsTests(unittest.TestCase):
    """§5.4：prompt 沒提到的報表要**列得出來**（黃，不擋）。"""

    def test_uncovered_reports_are_enumerable(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        doc = CLI_DOCS["report-narrative-flow.md"].read_text(encoding="utf-8")
        mentioned = set(re.findall(r"`([a-z][a-z0-9_]+)`", doc)) & set(REPORT_DEFINITIONS)
        uncovered = sorted(set(REPORT_DEFINITIONS) - mentioned)
        print(f"\n🟡 prompt 未涵蓋的報表 {len(uncovered)} 個："
              f"{', '.join(uncovered) if uncovered else '（無）'}")
        # ⚠ 不斷言為空：做成紅＝強制每個報表都要寫進 prompt，那是形式鎖
        self.assertIsInstance(uncovered, list)


if __name__ == "__main__":
    unittest.main()
