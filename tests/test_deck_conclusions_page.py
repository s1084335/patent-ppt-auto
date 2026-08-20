"""綜合結論頁（tasks 3b.3，design §7.7）＋集中度指標（§7.6）。

## 形（2026-08-12 使用者裁決「綜合版」）

一頁、一主題一列、三欄——**發現**（機械）｜**研發意涵**（CLI）｜
**專利行動**（CLI，**有限動詞表**）。兩個受眾版本不做二選一：同一份發現，
對研發說意涵、對專利部說行動。「受眾」那題在此直接消失。

## 護欄

- 發現欄＝引擎欄位組裝（patent_count／applicant_count／max_share／status
  ——集中度引擎**已產**，§7.6 的機械面不需新算），intake 寫進
  `topic_facts.json`；CLI 逐字引用，同口徑閘門模式。
- 🔴 專利行動＝有限動詞表：佈局／追蹤／迴避設計／細讀比對／暫不投入。
  **表外即紅**——避免 AI 寫出「應立即提出申請」這類法律／商業承諾。
- `conclusions` 存在時**取代**建議頁（§7.10 頁數帳：取代不新增）。
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


TOPIC_ROW = {
    "topic_code": "T001", "label": "拉繩滑雪模擬機構",
    "patent_count": "10", "applicant_count": "9",
    "max_share": "20", "status": "申請成長",
}


class TopicFactsIntakeTests(unittest.TestCase):
    """intake 產主題事實（發現欄的機械來源）。"""

    def test_intake_emits_topic_facts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        vdir = root / "report_trial_20990101_000000"
        vdir.mkdir(parents=True)
        (vdir / "report_data.json").write_text(json.dumps({
            "parameters": {"version": vdir.name},
            "chart_rows": {"cluster_topic_table": [TOPIC_ROW]},
            "sections": [],
        }, ensure_ascii=False), encoding="utf-8")
        out = root / "work"
        proc = subprocess.run(
            [PY, str(SCRIPTS / "assemble_from_version.py"), str(vdir), str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        facts = json.loads((out / "topic_facts.json").read_text(encoding="utf-8"))
        by_topic = {f["topic"]: f for f in facts}
        fact = by_topic.get("拉繩滑雪模擬機構")
        self.assertIsNotNone(fact, facts)
        # 發現字串＝引擎欄位組裝，數值逐字保留
        self.assertIn("10件/9家", fact["finding"])
        self.assertIn("申請成長", fact["finding"])

    def test_concentration_labels(self):
        """§7.6 分辨：單一申請人＝集中持有；各一件＝分散待驗。"""
        dl = _load("assemble_from_version")
        solo = dl._topic_finding({"label": "甲", "patent_count": "6",
                                  "applicant_count": "1", "max_share": "100",
                                  "status": "持平"})
        spread = dl._topic_finding({"label": "乙", "patent_count": "4",
                                    "applicant_count": "4", "max_share": "25",
                                    "status": "持平"})
        self.assertIn("集中持有", solo)
        self.assertIn("分散待驗", spread)


class ConclusionsRenderTests(unittest.TestCase):
    """conclusions 宣告時渲染三欄結論頁，並取代建議頁（P2）。"""

    @classmethod
    def setUpClass(cls):
        cls.dl = _load("deck_layout")
        cls.reg = _load("regression")

    def _build(self, with_conclusions: bool):
        from PIL import Image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        png_dir = root / "png"
        png_dir.mkdir()
        for name, (w, h) in self.reg.SHAPES.items():
            Image.new("RGB", (w * 3, h * 3), (255, 255, 255)).save(png_dir / f"{name}.png")
        content = self.reg._content()
        if with_conclusions:
            content["conclusions"] = {
                "title": "綜合結論：往哪裡走、專利部做什麼",
                "takeaway": "同一份發現，研發與專利各取行動。",
                "rows": [
                    {"topic": "拉繩滑雪模擬機構",
                     "finding": "10件/9家｜最大持有 20%｜申請成長",
                     "reading": "投入者眾，構型自由度縮小中。",
                     "action": "細讀比對"},
                    {"topic": "馬達自鎖阻力機構",
                     "finding": "6件/2家｜最大持有 83%｜集中持有",
                     "reading": "單一玩家掌握，替代路線價值高。",
                     "action": "迴避設計"},
                ],
            }
        return self.dl.build_svg(content, png_dir, root / "svg")

    @staticmethod
    def _text(svg: Path) -> str:
        root = ET.fromstring(svg.read_text(encoding="utf-8"))
        return " ".join("".join(el.itertext())
                        for el in root.iter() if el.tag.split("}")[-1] == "text")

    def test_conclusions_page_replaces_rec(self):
        pages = self._build(True)
        p2 = self._text(pages[1])
        self.assertIn("綜合結論", p2)
        self.assertIn("拉繩滑雪模擬機構", p2)
        self.assertIn("細讀比對", p2)
        # 取代不是並存：建議頁的內容不得出現
        self.assertNotIn("建議", p2.split("綜合結論")[0])
        # 2026-08-18（§7d）：路線圖頁併入結論頁後移除，總頁數少一頁。
        # ⚠ 原本斷言 8（「取代建議頁，總頁數不變」）——那個「不變」指的是
        #   conclusions 取代 rec 不新增頁，仍然成立；變的是路線圖那一頁沒了。
        self.assertEqual(
            len(pages), 7,
            "conclusions 仍是取代 rec（不新增頁）；路線圖頁已於 §7d 移除")

    def test_conclusions_is_the_only_second_page(self):
        """⚠ 2026-08-19（§9.3）取代原 `test_without_conclusions_rec_page_stays`。

        原測試驗「沒宣告 conclusions 時建議頁還在」——那條路徑已移除：
        §9.2c 起「沒有分群主題就不產簡報」是硬檢查，conclusions 一定有內容，
        `_compose` 的 else 分支永遠走不到。留著等於在守一條不存在的行為。
        """
        import deck_layout

        self.assertFalse(hasattr(deck_layout, "slide_rec"))
        pages = self._build(True)
        self.assertIn("綜合結論", self._text(pages[1]))


class ConclusionsGateTests(unittest.TestCase):
    """check_content：動詞白名單＋發現欄逐字。"""

    def _check(self, rows: list[dict], facts: list[dict] | None = None):
        from tests.test_deck_caliber_page import _minimal_content

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        if facts is not None:
            (work / "topic_facts.json").write_text(
                json.dumps(facts, ensure_ascii=False), encoding="utf-8")
        content = _minimal_content()
        content["pages"] = [{"title": "頁", "takeaway": "t", "charts": [],
                             "lines": ["內容"], "tag": None}]
        content["conclusions"] = {"title": "綜合結論", "takeaway": "t", "rows": rows}
        cpath = work / "content.json"
        cpath.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [PY, str(SCRIPTS / "check_content.py"), str(cpath)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")

    # ⚠ 2026-08-19（§9.3）：`依據：` 紀律從 rec 移到結論列——結論頁的行動
    #   同樣是建議，那條紀律不能跟著 rec 一起消失。
    ROW_OK = {"topic": "甲", "finding": "6件/2家｜集中持有",
              "reading": "說明", "action": "追蹤",
              "evidence": "依據：CN 121754861"}

    def test_action_outside_verb_table_fails(self):
        proc = self._check([{**self.ROW_OK, "action": "立即提出申請"}])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("動詞表", proc.stdout)

    def test_finding_must_match_engine_fact(self):
        facts = [{"topic": "甲", "finding": "6件/2家｜集中持有"}]
        proc = self._check([{**self.ROW_OK, "finding": "約 6 件、集中"}], facts)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("發現", proc.stdout)

    def test_valid_rows_pass(self):
        facts = [{"topic": "甲", "finding": "6件/2家｜集中持有"}]
        proc = self._check([self.ROW_OK], facts)
        self.assertEqual(proc.returncode, 0, proc.stdout)


if __name__ == "__main__":
    unittest.main()
