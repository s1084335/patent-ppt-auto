"""圖形文法（tasks 3b.4，design §7.4）：參數化元件，組合活、渲染死。

## 契約

- 六型：`flow`（流程）／`cycle`（循環）／`contrast`（對比）／`hierarchy`（階層）
  ／`parallel`（並列）／`timeline`（時間線）。
- CLI 在 content.json 的頁 spec 宣告 `{"figure": {"type": "flow",
  "nodes": ["…", …], "title": "…"}}`——**只宣告不畫**；引擎確定性渲染，
  風格／字型／配色隨 deck 主題。
- 🔴 **不開後門**：不允許 CLI 自由畫 SVG 嵌圖——口一開，風格與品質只剩
  目視迴圈兜底，四輪不夠用。文法不足時以實例擴元件型，走 openspec 留痕。
- 閘門（check_content）：type 在文法內、節點數 ≤ 容量、節點文字 ≤ 字寬單位上限。
  撞版由渲染端裕度表（note ledger）把關——與其他頁面同一條線。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"
PY = sys.executable


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FIGS = {
    "flow": ["匯入", "分群", "報表", "簡報"],
    "cycle": ["量測", "組版", "目視", "修稿"],
    "contrast": ["集中持有：單一玩家", "分散待驗：多家各一件"],
    "hierarchy": ["阻力系統", "風阻", "磁阻", "複合"],
    "parallel": ["技術通道", "功效通道"],
    "timeline": ["2015 起步", "2020 加速", "2024 高峰"],
}


class FigureRenderTests(unittest.TestCase):
    """六型都要能渲染成頁面元素（SVG 端驗——與 pptx 共用 _compose）。"""

    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        cls.reg = _load("regression")

    def _build_with_figure(self, fig: dict):
        from PIL import Image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        png_dir = root / "png"
        png_dir.mkdir()
        for name, (w, h) in self.reg.SHAPES.items():
            Image.new("RGB", (w * 3, h * 3), (255, 255, 255)).save(png_dir / f"{name}.png")
        content = self.reg._content()
        content["pages"].append({
            "title": "圖形文法測試頁", "takeaway": "測六型渲染。",
            "charts": [], "lines": ["圖下方的說明文字。"], "tag": None,
            "figure": fig,
        })
        return self.dl.build_svg(content, png_dir, root / "svg")

    @staticmethod
    def _texts(svg: Path) -> str:
        root = ET.fromstring(svg.read_text(encoding="utf-8"))
        return " ".join("".join(el.itertext())
                        for el in root.iter() if el.tag.split("}")[-1] == "text")

    def test_all_six_types_render_nodes(self):
        for ftype, nodes in FIGS.items():
            with self.subTest(type=ftype):
                pages = self._build_with_figure({"type": ftype, "nodes": nodes})
                # 2026-08-18（§7d）：路線圖頁併入結論頁後被移除，附加頁成為最後一頁。
                # ⚠ 原本寫 `pages[-2]  # 附加頁在 roadmap 之前`——用相對位置定位頁面，
                #   頁面組成一變就指到別頁，而錯誤訊息只會說「缺節點」，看不出真因。
                fig_page = pages[-1]
                text = self._texts(fig_page)
                for node in nodes:
                    self.assertIn(node, text, f"{ftype} 缺節點 {node}")

    def test_unknown_type_raises(self):
        """文法外的 type 渲染端 fail loud——不靜默略過（版面少一塊沒人發現）。"""
        with self.assertRaises(self.dl.FigureGrammarError):
            self._build_with_figure({"type": "freeform_svg", "nodes": ["x"]})


class FigureGateTests(unittest.TestCase):
    """check_content：type 白名單、節點數容量、文字長度。"""

    def _check(self, fig: dict) -> subprocess.CompletedProcess:
        from tests.test_deck_caliber_page import _minimal_content

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        content = _minimal_content()
        content["pages"] = [{"title": "頁", "takeaway": "t", "charts": [],
                             "lines": ["內容"], "tag": None, "figure": fig}]
        cpath = work / "content.json"
        cpath.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [PY, str(SCRIPTS / "check_content.py"), str(cpath)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_valid_figure_passes(self):
        proc = self._check({"type": "flow", "nodes": ["甲", "乙", "丙"]})
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_type_outside_grammar_fails(self):
        proc = self._check({"type": "sankey", "nodes": ["甲"]})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("文法", proc.stdout)

    def test_too_many_nodes_fails(self):
        proc = self._check({"type": "flow", "nodes": [f"節點{i}" for i in range(12)]})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("節點", proc.stdout)

    def test_node_text_too_long_fails(self):
        proc = self._check({"type": "flow",
                            "nodes": ["這個節點的文字實在太長塞不進固定寬度的卡片裡面了"]})
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main()
