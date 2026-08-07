"""前端 inline JS 語法守門。

🔴 2026-08-07 實機事故：插入的 onclick 字串跳脫在編輯管線中被吃掉一個
反斜線，整個 <script> 解析失敗——所有面板卡在初始「載入中…」，全站癱瘓。
Python 測試看不到 JS 語法錯，必須用 JS 引擎 parse 一次才守得住。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

_NODE_CANDIDATES = ("node", r"D:\vscode\node.js\node.exe")


def _find_node() -> str | None:
    for cand in _NODE_CANDIDATES:
        path = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if path:
            return path
    return None


class InlineJsSyntaxTests(unittest.TestCase):
    def test_inline_script_parses(self):
        node = _find_node()
        if node is None:
            self.skipTest("node 不可用——本機請裝於 D:/vscode/node.js（見工具規則）")
        html = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
                / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertTrue(scripts, "index.html 沒有 inline script？")
        js = max(scripts, key=len)
        tmp = Path(__file__).parent / "_inline_syntax_check.js"
        try:
            tmp.write_text(js, encoding="utf-8")
            proc = subprocess.run([node, "--check", str(tmp)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0,
                             f"inline JS 語法錯誤（全站會癱瘓）：\n{proc.stderr[:800]}")
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
