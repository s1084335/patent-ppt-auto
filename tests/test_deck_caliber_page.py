"""口徑頁（tasks 3b.2，design §7.5）＋ IPC/CPC 收頁契約（3b.2b，design §7.9）。

## 口徑頁的兩層分工（2026-08-12 使用者修正）

| 層 | 誰 | 內容 |
|---|---|---|
| 口徑事實包 | **引擎（intake 彙整）** | 每條口徑的**定義原文＋數值**——來源＝report_data 的 `population`（母體字串）與 `table_display.reader_guide`（判讀指引），都是引擎產的權威文字 |
| 編排 | **CLI** | 挑哪幾條上頁、排序、下標題、補白話說明 |

🔴 機械護欄：**定義文字與數字不得改寫**——閘門逐字比對。CLI 能選、能排、
能在後面**加**註解，不能重寫定義。⚠ 護欄的理由：口徑是後端唯一來源，
讓 CLI 重述＝同一份知識的第二落點；後端口徑改了、簡報還印舊說法，
而且不會有任何東西報錯。

## §7.9 契約（3b.2b：只補測試不改行為，防退化）

- 後端門檻 `CLASSIFICATION_MIN_DISTINCT_L4 = 3`（4 階 distinct < 3 → 不進簡報）
- plan_deck 對多階層圖**列候選要求判斷**（hints「收頁判斷」），
  🔴 不得用表格列數當機械門檻——實測踩過（12 列但 subclass 只有 2 種，判斷全錯）
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"
PY = sys.executable


def _run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(SCRIPTS / name), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


class CaliberFactsIntakeTests(unittest.TestCase):
    """intake 產事實包：population＋reader_guide 的權威文字逐字進 caliber_facts.json。"""

    def _fake_version(self, root: Path) -> Path:
        vdir = root / "report_trial_20990101_000000"
        vdir.mkdir(parents=True)
        (vdir / "report_data.json").write_text(json.dumps({
            "parameters": {"version": vdir.name},
            "population": {
                "application_trend": "母體 55/55 件",
                "publication_trend": "母體 44/55 件（11 件尚未授權公告）",
            },
            "table_display": {"reader_guide": [
                {"title": "共同申請", "body": "共同申請案在雙方各計一次，跨列相加會重複計算。"},
            ]},
            "sections": [
                {"report_key": "application_trend", "title": "專利申請趨勢",
                 "variants": [{"variant_key": "default"}]},
            ],
            "chart_rows": {},
        }, ensure_ascii=False), encoding="utf-8")
        (vdir / "version_meta.json").write_text(
            json.dumps({"workspace_name": "滑雪機", "version": vdir.name}),
            encoding="utf-8")
        return vdir

    def test_intake_emits_caliber_facts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        vdir = self._fake_version(root)
        out = root / "work"
        proc = _run_script("assemble_from_version.py", str(vdir), str(out))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        facts_path = out / "caliber_facts.json"
        self.assertTrue(facts_path.is_file(), "intake 要產 caliber_facts.json")
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        by_term = {f["term"]: f["text"] for f in facts}
        # 母體：term 用章節中文標題（sections title），text 逐字
        self.assertEqual(by_term.get("專利申請趨勢母體"), "母體 55/55 件")
        # reader_guide：逐字
        self.assertEqual(by_term.get("共同申請"),
                         "共同申請案在雙方各計一次，跨列相加會重複計算。")


class CaliberVerbatimGateTests(unittest.TestCase):
    """check_content 的逐字閘門：引用口徑的行，定義原文必須逐字出現。"""

    FACTS = [
        {"key": "population:application_trend", "term": "專利申請趨勢母體",
         "text": "母體 55/55 件"},
        {"key": "guide:0", "term": "共同申請",
         "text": "共同申請案在雙方各計一次，跨列相加會重複計算。"},
    ]

    def _check(self, lines: list[str]) -> subprocess.CompletedProcess:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        (work / "caliber_facts.json").write_text(
            json.dumps(self.FACTS, ensure_ascii=False), encoding="utf-8")
        content = _minimal_content()
        content["pages"].append({
            "title": "資料口徑：這份報告的數字怎麼讀",
            "takeaway": "先讀口徑，後面的圖才不會被誤讀。",
            "charts": [], "layout": "label",
            "lines": lines, "tag": None,
        })
        cpath = work / "content.json"
        cpath.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return _run_script("check_content.py", str(cpath))

    def test_verbatim_with_annotation_passes(self):
        """逐字＋後綴白話註解＝合法（CLI 能加註不能改寫）。"""
        proc = self._check([
            "專利申請趨勢母體｜母體 55/55 件——全部案件都計入趨勢。",
            "共同申請｜共同申請案在雙方各計一次，跨列相加會重複計算。",
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_rewritten_definition_fails(self):
        """改寫（語意同、字不同）必須紅——這是「CLI 不改圖」紀律的文字版。"""
        proc = self._check([
            "共同申請｜共同申請的案件雙方都會各算一次，直接相加會重複。",
        ])
        self.assertEqual(proc.returncode, 1, "改寫定義竟然過了閘門")
        self.assertIn("逐字", proc.stdout)

    def test_wrong_number_fails(self):
        proc = self._check(["專利申請趨勢母體｜母體 54/55 件"])
        self.assertEqual(proc.returncode, 1)

    def test_unused_facts_are_fine(self):
        """CLI 挑選是合法判斷——沒上頁的口徑不報錯。"""
        proc = self._check([
            "專利申請趨勢母體｜母體 55/55 件",
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout)


def _minimal_content() -> dict:
    """過 REQUIRED 檢查的最小 content（欄位值不求語意，只求形狀）。"""
    return {
        "footer": "測試｜非 FTO", "eyebrow": "測試", "deck_title": "口徑閘門測試",
        "subtitle": "測試", "meta": ["m1", "m2"], "stats": [["55", "件"]],
        "stats_note": "備註", "boundary": "非 FTO 判斷",
        "rec_title": "建議", "rec_takeaway": "測試",
        "recommendations": [
            {"title": "甲", "tag": "t", "lines": ["依據：測試事實", "a"], "color": "cyan"},
            {"title": "乙", "tag": "t", "lines": ["依據：測試事實", "a"], "color": "blue"},
            {"title": "丙", "tag": "t", "lines": ["依據：測試事實", "a"], "color": "amber"},
            {"title": "丁", "tag": "t", "lines": ["依據：測試事實", "a"], "color": "rose"},
        ],
        "pages": [],
        "roadmap_title": "路線", "roadmap_takeaway": "測試",
        "roadmap": [{"label": "短期", "title": "x", "items": ["y"], "color": "cyan"}],
        "limits_title": "限制", "limits": ["l1"],
    }


class IpcCpcContractTests(unittest.TestCase):
    """§7.9 防退化契約（3b.2b：不改行為，只鎖現狀）。"""

    def test_backend_threshold_constant(self):
        from backend.app.reports import chart_runner

        self.assertEqual(chart_runner.CLASSIFICATION_MIN_DISTINCT_L4, 3,
                         "4 階 distinct 門檻（2026-08-05 定案）不得漂移")

    def test_plan_deck_requires_judgment_not_row_proxy(self):
        src = (SCRIPTS / "plan_deck.py").read_text(encoding="utf-8")
        # 多階層 → 列候選、要求判斷
        self.assertIn("收頁判斷", src)
        # 🔴 踩過的坑必須留著紀錄——防「為求自動化」把門檻加回去
        self.assertIn("與其留一個猜錯的門檻", src)


if __name__ == "__main__":
    unittest.main()
