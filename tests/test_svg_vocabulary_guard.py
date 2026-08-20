"""頁面 SVG 詞彙邊界的主動閘門（2026-08-14 使用者裁決：轉換器封存、邊界獨立接上）。

## 它取代了什麼

原本這條邊界由窄轉換器（svg_to_pptx）的 fail-loud 測試**間接**守著：
`test_every_page_survives_the_converter` 把兩端串起來。轉換器封存
（git 歷史保存，復活指標見 tasks 2.1b）後，那道間接防線消失——本檔改為
**直接掃產出**：全部頁型（含六型圖形文法、結論頁、口徑頁）的實際 SVG 輸出，
元素集合必須 ⊆ `svg_canvas.SVG_VOCABULARY`。

## 為什麼邊界值得單獨守

詞彙是「頁面 SVG 隨時可轉原生 PPTX」的前提（期權）。邊界一鬆不會報錯——
只有哪天要接轉換器時才發現 SVG 層長出它吃不下的東西。箭頭用字符、
分隔線用細 rect，都是這條邊界在起作用。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SvgVocabularyGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        cls.canvas = _load("svg_canvas")
        cls.reg = _load("regression")

    def _build_all_page_types(self) -> list[Path]:
        """組一份涵蓋**全部**頁型的 content：regression 八頁型＋結論頁＋六型圖形。

        ⚠ 新頁型加進系統時要一併加進這裡——漏加＝該頁型的輸出不受邊界保護。
        """
        from PIL import Image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        png_dir = root / "png"
        png_dir.mkdir()
        for name, (w, h) in self.reg.SHAPES.items():
            Image.new("RGB", (w * 3, h * 3), (255, 255, 255)).save(png_dir / f"{name}.png")
        content = self.reg._content()
        content["_source_version"] = "report_trial_20990101_000000"   # 來源行也要掃
        content["conclusions"] = {
            "title": "綜合結論", "takeaway": "t",
            "rows": [{"topic": "甲", "finding": "6件/2家｜集中持有",
                      "implication": "說明", "action": "追蹤"}]}
        for ftype, nodes in (("flow", ["甲", "乙", "丙"]),
                             ("cycle", ["量測", "組版", "目視"]),
                             ("contrast", ["集中", "分散"]),
                             ("hierarchy", ["根", "子一", "子二"]),
                             ("parallel", ["並甲", "並乙"]),
                             ("timeline", ["2015", "2020", "2024"])):
            content["pages"].append({
                "title": f"圖形文法：{ftype}", "takeaway": "詞彙掃描用。",
                "charts": [], "lines": ["說明。"], "tag": None,
                "figure": {"type": ftype, "nodes": nodes}})
        return self.dl.build_svg(content, png_dir, root / "svg")

    def test_all_output_within_vocabulary(self):
        pages = self._build_all_page_types()
        # §7d 移除路線圖頁後：7 頁型 + 6 圖形頁（原 8 + 6）。
        self.assertGreaterEqual(len(pages), 13)   # 7 頁型 + 6 圖形頁
        vocab = self.canvas.SVG_VOCABULARY
        for path in pages:
            root = ET.fromstring(path.read_text(encoding="utf-8"))
            used = {el.tag.split("}")[-1] for el in root.iter()}
            with self.subTest(page=path.name):
                self.assertLessEqual(
                    used, vocab,
                    f"頁面 SVG 用了詞彙外元素：{sorted(used - vocab)}——"
                    "擴詞彙要先復活窄轉換器並擴其承接能力（tasks 2.1b），"
                    "不得只在產出端放行")

    def test_vocabulary_is_narrow(self):
        """邊界本身不得被悄悄放寬——改這個集合是規格級決定。"""
        self.assertEqual(self.canvas.SVG_VOCABULARY,
                         frozenset({"svg", "rect", "text", "image"}))


if __name__ == "__main__":
    unittest.main()
