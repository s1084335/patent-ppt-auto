"""CLI 文件提到的識別字必須真的存在（tasks §5.2／§5.3）。

## 要守的是什麼

寫給 CLI 與 prompt 的文件會提到報表名、欄位名、檔名。定義改名或退場時文件不會
自己跟上，於是 CLI 照文件去找一個不存在的東西——**取不到就是空的，不會報錯**。
本輪踩過一次：§7d 移除路線圖後 SKILL.md 還在教 `roadmap`，CLI 會照抄一個畫不
出來的區塊。

## 判準的第一版是錯的，記在這裡

初版寫成「反引號裡含底線的字串必須是 `REPORT_DEFINITIONS` 的鍵」——那是
**代理指標**：文件裡的 `patent_id`、`query_database`、`check_content` 全被掃成
「不存在的報表」，一次噴出 34 個假警報，而白名單愈補愈長、愈長愈沒人看。

⚠ 真正要守的不是「它是不是報表名」，是「**它到底存不存在**」。
所以改成對**整個符號宇宙**比對：報表名、模組層常數／函式、腳本檔名、
intake 產出的檔名、資料欄位標籤。`roadmap` 之所以該紅，正因為它已經什麼都不是。

## 兩種方向不同罰則（刻意不對稱）

| 情形 | 判定 | 為什麼 |
|---|---|---|
| 文件提到、程式裡沒有 | 🔴 **紅** | CLI 會照做，然後取到空的 |
| 程式裡有、文件沒提 | 🟡 **列出不擋** | 沒寫進文件不代表錯；做成紅會逼人把內部東西也寫上去湊數（形式鎖） |
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = (
    ROOT / "skills/html-report-to-deck/SKILL.md",
    ROOT / "skills/html-report-to-deck/references/narrative.md",
    ROOT / "backend/app/worker/prompts/report-narrative-flow.md",
    ROOT / "backend/app/worker/prompts/data_access.md",
)

#: 反引號裡、長得像識別字的（snake_case，至少兩段）
_IDENT = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")

#: intake 與工作目錄產出的檔名（不是程式符號，但文件會提到）
_ARTIFACT_NAMES = {
    "report_data", "topic_facts", "cover_stats", "caliber_facts", "action_scan",
    "content_json", "visual_verdict", "font_choice", "plan_json", "report_json",
    "charts_orig_backup", "chart_rows", "report_meta",
    # ⚠ 外部 API，不是我們的符號：Playwright 的 page.set_content
    "set_content",
}


def _symbol_universe() -> set[str]:
    """程式裡真的存在的識別字。

    ⚠ 涵蓋要夠寬，否則假警報會把訊號淹掉——而假警報一多，這道閘門就會被
    「反正都是誤報」略過，等於沒有。
    """
    import ast

    names: set[str] = set(_ARTIFACT_NAMES)

    from backend.app.reports.report_definitions import REPORT_DEFINITIONS

    names |= set(REPORT_DEFINITIONS)
    for d in REPORT_DEFINITIONS.values():
        names |= set(getattr(d, "columns", ()) or ())
        names |= {a[2] for a in (getattr(d, "aggregates", ()) or ())}

    scan_dirs = [ROOT / "backend/app", ROOT / "skills/html-report-to-deck/scripts"]
    for base in scan_dirs:
        for p in base.rglob("*.py"):
            if ".venv" in p.parts:
                continue
            names.add(p.stem)
            try:
                tree = ast.parse(p.read_text(encoding="utf-8-sig"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            # ⚠ 只收**模組層**定義與字串字面，**不收區域變數**（ast.Name）：
            #   收了的話任何一個 or roadmap_item in ... 都會讓宇宙包含它，
            #   閘門就放行已退場的名字——初版正是這樣讓 roadmap_item 溜過去。
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    names.add(node.name)
                    names.add(node.name.lower())
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if _IDENT.fullmatch(f"`{node.value}`"):
                        names.add(node.value)
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            names.add(tgt.id)
                            names.add(tgt.id.lower())
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
                    names.add(node.target.id.lower())
    return names


def _mentioned() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for doc in DOCS:
        if not doc.is_file():
            continue
        for m in _IDENT.finditer(doc.read_text(encoding="utf-8")):
            found.setdefault(m.group(1), []).append(doc.name)
    return found


class DocsReferenceExistingThingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.universe = _symbol_universe()
        cls.mentioned = _mentioned()

    def test_no_dangling_reference(self):
        """🔴 §5.2：文件提到但程式裡沒有＝紅。"""
        dangling = {k: sorted(set(v)) for k, v in self.mentioned.items()
                    if k not in self.universe}
        self.assertEqual(
            dangling, {},
            "文件提到程式裡不存在的東西（CLI 會照做然後取到空的）：\n" +
            "\n".join(f"  {k} ← {'、'.join(v)}" for k, v in sorted(dangling.items())))

    def test_roadmap_would_be_caught(self):
        """⚠ 反向驗證寫進測試：確認這道閘門真的抓得到「已退場的名字」。

        `roadmap` 是本輪實際踩過的案例——§7d 移除該頁後 SKILL.md 還在教它。
        """
        stale = sorted(n for n in self.universe if n.startswith("roadmap"))
        self.assertEqual(
            stale, [],
            f"符號宇宙裡還有已退場的 roadmap 名字：{stale}"
            "——閘門會放行文件教它們，而那些頁面早就不存在了")

    def test_undocumented_reports_are_listed_not_blocked(self):
        """🟡 程式裡有、文件沒提：列出不擋。

        ⚠ 做成紅＝強制「每個報表都要寫進文件」，那是形式鎖，
        會逼人把內部報表也寫上去湊數。
        """
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        undocumented = sorted(set(REPORT_DEFINITIONS) - set(self.mentioned))
        print(f"\n🟡 未寫進 CLI 文件的報表 {len(undocumented)} 個："
              f"{', '.join(undocumented) if undocumented else '（無）'}")
        self.assertIsInstance(undocumented, list)


if __name__ == "__main__":
    unittest.main()
