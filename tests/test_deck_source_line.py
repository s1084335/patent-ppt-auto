"""來源行（add-deck-delivery-line tasks 3b.1，design §7.1——機械）。

每頁角落「資料來源：<version>／<report_key>」，**引擎印，CLI 不參與**：
make_deck 自 work 目錄的 report.json（report_meta.source_file）蓋章進 content
的機械欄位 `_source_version`，頁型渲染時取用——CLI 的 content.json 裡
**沒有**這個欄位，有也會被蓋掉（不給 CLI 竄改來源的通道）。

⚠ 顯示時去掉恆定前綴 `report_trial_`（版本目錄名的固定字首，去掉不損
回溯性——手上有顯示值就能唯一還原目錄名），否則來源行 4in 起跳，
擠壓 footer 文字。
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
    # deck_layout 延遲 import svg_canvas（同目錄），scripts 要在 sys.path 上
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _page_text(svg_path: Path) -> str:
    root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
    return " ".join("".join(el.itertext())
                    for el in root.iter() if el.tag.split("}")[-1] == "text")


class SourceLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        cls.reg = _load("regression")

    def _build(self, version: str | None):
        from PIL import Image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        png_dir = root / "png"
        png_dir.mkdir()
        for name, (w, h) in self.reg.SHAPES.items():
            Image.new("RGB", (w * 3, h * 3), (255, 255, 255)).save(png_dir / f"{name}.png")
        content = self.reg._content()
        if version is not None:
            content["_source_version"] = version
        return self.dl.build_svg(content, png_dir, root / "svg")

    def test_every_page_carries_source_line(self):
        pages = self._build("report_trial_20990101_000000")
        for path in pages:
            with self.subTest(page=path.name):
                text = _page_text(path)
                self.assertIn("資料來源：20990101_000000", text,
                              "每頁（含封面）都要有來源行——design §7.1")

    def test_chart_pages_carry_report_key(self):
        pages = self._build("report_trial_20990101_000000")
        # regression 合成內容第 1 個圖表頁用 tall 圖
        chart_page = pages[2]
        self.assertIn("／tall", _page_text(chart_page))

    def test_without_version_no_source_line(self):
        """沒蓋章（開發側直跑舊素材）不印——不得印出「資料來源：None」。"""
        pages = self._build(None)
        self.assertNotIn("資料來源", _page_text(pages[0]))

    def test_make_deck_stamps_from_report_meta(self):
        """make_deck 蓋章：值來自 report.json 的 report_meta.source_file，
        CLI 的 content.json 就算自帶 `_source_version` 也會被蓋掉。"""
        make_deck_src = (SCRIPTS / "make_deck.py").read_text(encoding="utf-8")
        self.assertIn("_source_version", make_deck_src)
        self.assertIn("source_file", make_deck_src)


if __name__ == "__main__":
    unittest.main()
