"""variant 級 rows 在**非分群** section 也要生效（2026-08-17）。

⚠ 這支測試存在的原因是一個真實的漏驗：先前替 IPC/CPC 每階 variant 掛上 `rows`，
只驗到「Python 端有把 rows 塞進 variant」就宣告完成——但顯示層
`sectionForReportView` 的非分群分支根本不讀 `variant.rows`，切 tab 時表格
仍是同一份。**拿輸入當產出**，圖換了、表沒換。

所以這裡不掃原始碼字串，直接用 node 執行那個函式看回傳值：
判準是恆等式（切到某 variant，回傳的 rows 就必須是那個 variant 的 rows），
不是「原始碼裡有沒有出現 variant.rows」這種代理指標。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "backend/app/static/index.html"
_NODE_CANDIDATES = ("node", r"D:\vscode\node.js\node.exe")


def _find_node() -> str | None:
    for cand in _NODE_CANDIDATES:
        path = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if path:
            return path
    return None


def _extract(html: str, name: str) -> str:
    """取出整個具名函式（以下一個頂層 `function ` 當邊界）。"""
    start = html.index(f"function {name}(")
    nxt = html.find("\nfunction ", start + 1)
    return html[start:nxt if nxt != -1 else len(html)].rstrip()


class VariantRowsDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = _find_node()
        if cls.node is None:
            raise unittest.SkipTest("node 不在 PATH，也不在 D:/vscode/node.js")
        cls.html = INDEX.read_text(encoding="utf-8")

    def _run(self, section: dict, option: dict) -> dict:
        deps = "\n".join(
            _extract(self.html, fn) for fn in
            ("isClusterSection", "clusterVariantMatchesReport", "clusterVariantMatchesSource",
             "sectionForReportView"))
        script = (
            "const CLUSTER_REPORT_NAMES = new Set(['cluster_topic_table','opportunity_quadrant',"
            "'topic_timeline']);\n"
            f"{deps}\n"
            "function reportLabelForKey(k) { return k; }\n"
            f"const out = sectionForReportView({json.dumps(section)}, {json.dumps(option)}, null);\n"
            "console.log(JSON.stringify({rows: out.rows, labels: out.column_labels || null}));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.mjs"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run([self.node, str(path)], capture_output=True,
                                  text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def _ipc_section(self) -> dict:
        """仿 IPC/CPC section：section 級 rows＝預設階，兩個 variant 各自帶 rows。"""
        return {
            "title": "IPC 主要分類",
            "report_key": "ipc_main_distribution",
            "rows": [{"code": "A63B-069"}, {"code": "A63B-021"}, {"code": "F03G-007"}],
            "variants": [
                {"label": "4 階", "file": "ipc_l4.svg", "variant_key": "L4",
                 "rows": [{"code": "A63B"}, {"code": "F03G"}]},
                {"label": "5 階", "file": "ipc_l5.svg", "variant_key": "L5",
                 "rows": [{"code": "A63B-069"}, {"code": "A63B-021"}, {"code": "F03G-007"}]},
            ],
        }

    def test_non_cluster_variant_rows_are_used(self):
        """切到 4 階：表格必須變成該階的 2 列，不能還是預設階的 3 列。"""
        out = self._run(self._ipc_section(), {"variantIndex": 0, "label": "4 階"})
        self.assertEqual([r["code"] for r in out["rows"]], ["A63B", "F03G"])

    def test_non_cluster_second_variant_rows(self):
        out = self._run(self._ipc_section(), {"variantIndex": 1, "label": "5 階"})
        self.assertEqual([r["code"] for r in out["rows"]],
                         ["A63B-069", "A63B-021", "F03G-007"])

    def test_variant_without_rows_falls_back_to_section_rows(self):
        """沒帶 rows 的 variant 要退回 section rows——不得變成空表。"""
        section = self._ipc_section()
        section["variants"][0].pop("rows")
        out = self._run(section, {"variantIndex": 0, "label": "4 階"})
        self.assertEqual(len(out["rows"]), 3)

    def test_no_variant_index_keeps_section_rows(self):
        out = self._run(self._ipc_section(), {})
        self.assertEqual(len(out["rows"]), 3)

    def test_variant_column_labels_win_when_non_empty(self):
        """variant 自帶欄名時要蓋過 section 的；空物件不得蓋（表頭會退回英文 key）。"""
        section = self._ipc_section()
        section["column_labels"] = {"code": "分類號"}
        section["variants"][0]["column_labels"] = {"code": "IPC 4 階"}
        section["variants"][1]["column_labels"] = {}
        self.assertEqual(
            self._run(section, {"variantIndex": 0})["labels"], {"code": "IPC 4 階"})
        self.assertEqual(
            self._run(section, {"variantIndex": 1})["labels"], {"code": "分類號"})


if __name__ == "__main__":
    unittest.main()
