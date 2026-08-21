"""分頁鈕切 variant 時，**表格**要跟著換（2026-08-17，第三次修同一個症狀）。

前兩次都修錯地方，原因是驗錯層級：
- 第一次：驗「Python 端有把 rows 塞進 variant」——顯示層根本沒讀。
- 第二次：修 `sectionForReportView` 的 `option.variantIndex` 分支並用它驗——
  但**非分群 section 的下拉一個 section 只給一項、不帶 variantIndex**
  （`buildReportViewOptions`），畫面上的分頁鈕走的是另一條路
  （`reportVariantPick[sectionIndex]`）。那個分支對 IPC/CPC 是死碼。

所以這支測試直接用 node **渲染整張卡的 HTML**，斷言表格內容隨分頁鈕改變。
判準是恆等式：切到哪個 variant，表格就必須出現那個 variant 的資料、
且不得出現另一個 variant 獨有的資料。中間層長什麼樣不在判準內。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "backend/app/static/index.html"
_NODE_CANDIDATES = ("node", r"D:\vscode\node.js\node.exe")

# 取一段**連續**的原始碼（isClusterSection → reportSingleHtml 結尾），
# 不逐一挑函式——挑漏了就會在測試裡補一個與正式碼不同步的假貨。
_BLOCK_START = "function isClusterSection("
_BLOCK_END = "function exportCardHtml("

# 區塊外定義的依賴，用最小 stub 補（都是純顯示工具，與本判準無關）。
# ⚠ 只補**區塊裡沒有**的：區塊已自帶的照用正式碼，重複宣告會是語法錯，
#   而且假貨會與正式碼漂移——這正是「同一份知識兩個落點」的老問題。
_CONSTS = """
const CLUSTER_REPORT_NAMES = new Set(
  ['cluster_topic_table', 'opportunity_quadrant', 'topic_timeline']);
const DEFAULT_TOPIC_SOURCE_FIELD = 'tech_summary';
const exportPreview = { clusterSourceField: null };
const reportViewOptions = [];
"""
_STUBS = {
    "escHtml": "function escHtml(s) { return String(s == null ? '' : s); }",
    "fmtCell": "function fmtCell(v) { return v == null ? '' : String(v); }",
    "columnLabel": "function columnLabel(c, l) { return (l && l[c]) || c; }",
    "jsQuote": "function jsQuote(s) { return \"'\" + String(s) + \"'\"; }",
    "reportLabelForKey": "function reportLabelForKey(k) { return k; }",
    "el": "function el() { return null; }",
    "reportAssetUrl": "function reportAssetUrl() { return ''; }",
    "reportSourceTabsHtml": "function reportSourceTabsHtml() { return ''; }",
}


def _find_node() -> str | None:
    for cand in _NODE_CANDIDATES:
        path = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if path:
            return path
    return None


class ReportCardVariantSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = _find_node()
        if cls.node is None:
            raise unittest.SkipTest("node 不在 PATH，也不在 D:/vscode/node.js")
        html = INDEX.read_text(encoding="utf-8")
        start = html.index(_BLOCK_START)
        end = html.index(_BLOCK_END)
        cls.block = html[start:end]
        cls.prelude = _CONSTS + "\n".join(
            src for name, src in _STUBS.items()
            if f"function {name}(" not in cls.block)

    def _section(self) -> dict:
        """仿 IPC/CPC：兩個階層 variant，各自帶 rows；section 級 rows＝預設階。"""
        return {
            "title": "IPC 主要分類",
            "report_key": "ipc_main_distribution",
            "rows": [{"code": "A63B-069", "patent_count": 30}],
            "column_labels": {"code": "分類號", "patent_count": "件數"},
            "variants": [
                {"label": "4 階", "file": "ipc_l4.svg", "variant_key": "L4",
                 "rows": [{"code": "A63B", "patent_count": 44},
                          {"code": "F03G", "patent_count": 3}]},
                {"label": "5 階", "file": "ipc_l5.svg", "variant_key": "L5",
                 "rows": [{"code": "A63B-069", "patent_count": 30},
                          {"code": "A63B-021", "patent_count": 14},
                          {"code": "F03G-007", "patent_count": 3}]},
            ],
        }

    def _render(self, section: dict, pick: int | None) -> str:
        """跑真正的 reportSingleHtml，回傳整張卡的 HTML。"""
        set_pick = ("" if pick is None
                    else f"reportVariantPick[0] = {pick};\n")
        script = (
            self.prelude
            + self.block
            + set_pick
            + f"const out = reportSingleHtml({json.dumps(section)}, "
              "{sectionIndex: 0, label: 'IPC 主要分類'});\n"
            + "console.log(out);\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.mjs"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run([self.node, str(path)], capture_output=True,
                                  text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return proc.stdout

    def test_tab_pick_switches_table_to_that_level(self):
        """切到 4 階：表格要出現 A63B/44，且**不得**出現 5 階獨有的 A63B-021。"""
        html = self._render(self._section(), pick=0)
        self.assertIn("A63B</td>", html, "表格沒換成 4 階")
        self.assertIn(">44<", html)
        self.assertNotIn("A63B-021", html, "4 階表格混進 5 階的列")

    def test_second_tab_switches_table(self):
        html = self._render(self._section(), pick=1)
        self.assertIn("A63B-021", html, "表格沒換成 5 階")
        self.assertIn(">14<", html)

    def test_tabs_still_rendered_for_both_variants(self):
        """⚠ 收斂 variants 會讓分頁鈕消失——修 rows 不能把切換入口弄不見。"""
        html = self._render(self._section(), pick=0)
        self.assertIn("4 階", html)
        self.assertIn("5 階", html)
        self.assertIn("switchReportVariant(0, 1)", html)

    def test_variant_without_rows_falls_back(self):
        """單一 variant／variant 沒帶 rows 時退回 section rows，不得空表。"""
        section = self._section()
        section["variants"] = [{"label": "Bar", "file": "x.svg",
                                "variant_key": "default"}]
        html = self._render(section, pick=None)
        self.assertIn("A63B-069", html)
        self.assertNotIn("尚無資料", html)


if __name__ == "__main__":
    unittest.main()
