"""行動掃描的落點與洩漏閘門（tasks §9.6／§9.6b）。

## 使用者裁決（2026-08-19）

> 「成立／不成立／證據不足這種事系統內部判定，不要讓這種措辭外洩到報告去。」

⚠ 這**取代**了我原本寫的 9.6.6（「不成立／證據不足進附錄或摺疊區」）——
那等於把內部判定語言直接印給讀者看。同型問題本專案處理過一次：deepen 4.1／4.2
把 `read_me`／`chart_rule` 從封面移除，並對「本簡報怎麼讀」「**待驗證**」「**降級**」
建黑名單。「證據不足」與「降級」是同一類詞：**流程狀態不是分析結論**。

## 分工

| 給誰看 | 放哪 | 內容 |
|---|---|---|
| 閘門與稽核 | 工作目錄的掃描檔（非渲染） | 全部候選、判定、理由 |
| 報告讀者 | 投影片 | **只有成立的行動**，用結論語言 |

⚠ 「掃過」這件事必須留得下證據——掃了卻完全不顯示，使用者無從判斷掃得對不對。
理由成立，只是**落點在工作目錄不在投影片**。
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
SKILL = ROOT / "skills" / "html-report-to-deck"
SCRIPTS = SKILL / "scripts"
PY = sys.executable


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TOPIC_ROW = {
    "topic_code": "T001", "label": "拉繩滑雪模擬機構",
    "patent_count": "10", "applicant_count": "9", "max_share": "20",
    "status": "申請成長", "pending_count": "3",
}


class ScanFileIsWrittenTests(unittest.TestCase):
    """§9.6b-6：掃描檔隨產出一併保存，供「為什麼沒建議 X」查得到。"""

    def _run_intake(self) -> Path:
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
        return out

    def test_scan_file_exists(self):
        out = self._run_intake()
        self.assertTrue((out / "action_scan.json").is_file(),
                        "掃描檔沒產出——使用者查不到「為什麼沒建議 X」")

    def test_scan_covers_every_candidate(self):
        from backend.app.reports.action_space import ACTION_POOL

        scan = json.loads((self._run_intake() / "action_scan.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(scan["covered"], f"{len(ACTION_POOL)}/{len(ACTION_POOL)}")
        for topic, verdicts in scan["verdicts"].items():
            with self.subTest(topic=topic):
                self.assertEqual(set(verdicts), set(ACTION_POOL))

    def test_known_gaps_are_carried_into_the_scan(self):
        """⚠ 未涵蓋清單要跟著走——留在 Python 裡等於使用者看不到。"""
        scan = json.loads((self._run_intake() / "action_scan.json")
                          .read_text(encoding="utf-8"))
        self.assertTrue(scan["known_gaps"])


class VerdictWordingMustNotLeakTests(unittest.TestCase):
    """🔴 §9.6b：判定措辭是系統內部語言，不得出現在會被渲染的文字裡。"""

    def _check(self, page_line: str):
        from tests.test_deck_caliber_page import _minimal_content

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        content = _minimal_content()
        content["pages"] = [{"title": "頁", "takeaway": "t", "charts": [],
                             "lines": [page_line], "tag": None}]
        path = work / "content.json"
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [PY, str(SCRIPTS / "check_content.py"), str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_insufficient_evidence_wording_is_blocked(self):
        proc = self._check("本方向證據不足，暫不建議。")
        self.assertEqual(proc.returncode, 1, "「證據不足」洩漏到投影片沒被擋")
        self.assertIn("證據不足", proc.stdout)

    def test_holds_wording_is_blocked(self):
        proc = self._check("此行動判定為成立。")
        self.assertEqual(proc.returncode, 1)

    def test_coverage_wording_is_blocked(self):
        """⚠ 對帳字串（covered N/M）也是內部語言。"""
        proc = self._check("行動空間 covered 10/10。")
        self.assertEqual(proc.returncode, 1)

    def test_normal_wording_passes(self):
        """⚠ 反面要驗：黑名單過寬會擋掉正常句子，逼 CLI 亂改到過為止。"""
        proc = self._check("拉繩滑雪的申請成長，值得優先投入。")
        self.assertNotIn("內部", proc.stdout)


class BlacklistIsFiniteTests(unittest.TestCase):
    """⚠ 有限清單，不做模式比對（沿 deepen 4.2 的作法）。"""

    def test_blacklist_is_an_explicit_list(self):
        cc = _load("check_content")
        self.assertIn("證據不足", cc.BLOCKED_SLIDE_TERMS)
        self.assertIn("待驗證", cc.BLOCKED_SLIDE_TERMS,
                      "既有黑名單項被覆蓋掉了——新增不該取代舊的")


if __name__ == "__main__":
    unittest.main()
