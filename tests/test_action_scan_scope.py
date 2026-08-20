"""行動掃描的資料接線與母體揭露（tasks §8.3 實測發現）。

## 這兩條是**真素材實測**逼出來的

第一次拿真報表跑掃描：13 個主題、**92 個「證據不足」、只有 6 個成立**。
看起來像「資料不夠」，實際是兩個接線問題：

1. **象限沒 join**：`quadrant` 在 `chart_rows["opportunity_quadrant_*"]` 的列裡，
   主題表列本身沒有。四條看象限的規則因此全回證據不足。
2. **母體沒縮**：五類技術狀態**只給技術通道**（2026-08-03 定案），
   對功效主題跑狀態規則每個都回 9 個證據不足——那是**雜訊不是訊號**，
   而雜訊會把真正的證據不足淹掉。

修完：證據不足 92 → 5、成立 6 → 9、每主題成立數 0–3（規則真的在判）。
剩的 5 個都是 `pending_count` 缺（§7e 之後才有的欄位，舊版報表沒有）
——那是**資料舊**不是系統判不出來，正好證明兩者分開是對的。

⚠ 沒有拿真素材跑，這兩個問題都不會出現：合成 fixture 是我照著規則寫的，
**它必然接得上**。這就是「假替身只補一半比完全沒有更難查」的另一面。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "html-report-to-deck" / "scripts"
PY = sys.executable

TECH = "wips_independent_claims"
EFFECT = "effect_summary"


def _report_data() -> dict:
    """兩通道各兩個主題，象限資料放在**另一個區塊**（與真實形狀一致）。"""
    def topic(label, sf, status, n, apps, share):
        return {"topic_code": label, "label": label, "source_field": sf,
                "status": status, "patent_count": n, "applicant_count": apps,
                "max_share": share}

    return {
        "parameters": {"version": "report_trial_20990101_000000"},
        "sections": [],
        "chart_rows": {
            "cluster_topic_table": [
                topic("技術甲", TECH, "成長", 10, 9, 20),
                topic("技術乙", TECH, "衰退", 3, 2, 80),
                # ⚠ 功效主題**沒有 status**（引擎只給技術通道）
                {"topic_code": "功效丙", "label": "功效丙", "source_field": EFFECT,
                 "patent_count": 8, "applicant_count": 4, "max_share": 30},
                {"topic_code": "功效丁", "label": "功效丁", "source_field": EFFECT,
                 "patent_count": 5, "applicant_count": 5, "max_share": 20},
            ],
            "opportunity_quadrant_tech": {
                "patent_count_median": 6, "applicant_count_median": 5,
                "rows": [{"label": "技術甲", "quadrant": "多方投入技術"},
                         {"label": "技術乙", "quadrant": "低件數·少申請人"}],
            },
        },
    }


class ScanJoinsQuadrantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        root = Path(tmp.name)
        vdir = root / "report_trial_20990101_000000"
        vdir.mkdir(parents=True)
        (vdir / "report_data.json").write_text(
            json.dumps(_report_data(), ensure_ascii=False), encoding="utf-8")
        out = root / "work"
        proc = subprocess.run(
            [PY, str(SCRIPTS / "assemble_from_version.py"), str(vdir), str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cls.scan = json.loads((out / "action_scan.json").read_text(encoding="utf-8"))

    def test_quadrant_rules_are_not_all_unknown(self):
        """🔴 象限有 join 才判得動。"""
        v = self.scan["verdicts"]["技術甲"]
        self.assertEqual(v["檢視請求項範圍重疊"], "成立",
                         "象限沒接進來——四條看象限的規則會全回證據不足")

    def test_scope_is_technical_topics_only(self):
        """🔴 母體＝技術主題；功效主題不進掃描。"""
        self.assertEqual(set(self.scan["verdicts"]), {"技術甲", "技術乙"},
                         "功效主題被掃進來了——狀態規則只會回一堆證據不足")

    def test_scope_is_disclosed_not_silent(self):
        """⚠ 縮小母體必須寫出來——靜默縮小是本專案母體三種病之一。"""
        scope = self.scan.get("scope")
        self.assertIsNotNone(scope, "沒有揭露掃描範圍")
        self.assertEqual(scope["scanned_topics"], 2)
        self.assertEqual(scope["all_topics"], 4)
        self.assertTrue(str(scope.get("why") or "").strip(), "沒寫為什麼縮小")

    def test_verdicts_differ_between_topics(self):
        """⚠ 兩個主題判出同一組結果＝規則沒有真的在判資料。"""
        a = self.scan["verdicts"]["技術甲"]
        b = self.scan["verdicts"]["技術乙"]
        self.assertNotEqual(a, b, "不同資料判出相同結果")

    def test_missing_field_is_still_unknown_not_failure(self):
        """⚠ `pending_count` 缺席（舊版報表沒有）要判證據不足，不是不成立。

        兩者給使用者的下一步不同：補資料 vs 不用管。
        """
        self.assertEqual(self.scan["verdicts"]["技術甲"]["追蹤他人審查中案件"],
                         "證據不足")


if __name__ == "__main__":
    unittest.main()
