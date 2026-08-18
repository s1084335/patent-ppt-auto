"""跳過揭露的**傳輸段**：後端算得出、前端畫得出，中間要真的送得到。

## 為什麼有這一支

2026-08-18 Playwright 實機驗收發現：runner 算了 `skipped_invalid`、前端也寫了
`companyNormalizationSkippedHtml()`，但**使用者永遠看不到**——前端讀的是
`taskListCache`（來源 `/tasks`），而 `/tasks` 對每一筆 job 都回 `result: null`
（`job_repository.py`：結果落 `workflow_outputs`，需要時才由 `fetch_job_result` 讀回）。

⚠ 舊測試 `test_frontend_surfaces_skipped_count` 斷言的是 `"skipped_invalid" in html`
——函式在、字串在，資料到不了，照樣綠。**只斷言字串出現**是假性通過的固定型態。

## 為什麼改由建議端點帶，而不是讓 /tasks 帶 result

`renderTaskList` 只保留最近 `RECENT_DONE_KEEP` 筆 succeeded。就算 `/tasks` 帶了
result，跳過揭露也會在被其他 job 擠掉後**靜默消失**——同一個缺席型 bug 的第二次。
揭露的內容屬於「這批建議」，就該跟建議走同一條資料流（一方產生、一方消費）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "backend" / "app" / "static" / "index.html"
_NODE_CANDIDATES = ("node", r"D:\vscode\node.js\node.exe")


def _find_node() -> str | None:
    for cand in _NODE_CANDIDATES:
        path = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if path:
            return path
    return None


def _extract_function(src: str, name: str) -> str:
    """從 index.html 取出一個 function 的完整原始碼（大括號配對）。"""
    start = src.index(f"function {name}(")
    depth, i, opened = 0, start, False
    while i < len(src):
        if src[i] == "{":
            depth += 1
            opened = True
        elif src[i] == "}":
            depth -= 1
            if opened and depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError(f"找不到 {name} 的結尾")


class ApiCarriesSkipInfoTests(unittest.TestCase):
    """建議端點必須把最近一次 run 的跳過資訊一起帶出來。"""

    def _call(self, jobs, result):
        from backend.app.api import company_aliases as api

        # 端點是在函式內 import，patch 要下在來源模組上
        with mock.patch("backend.app.derived.company_alias_importer"
                        ".list_company_normalization_suggestions",
                        return_value={"items": [], "total": 0}), \
                mock.patch("backend.app.db.job_repository.list_jobs",
                           return_value=jobs) as list_jobs, \
                mock.patch("backend.app.db.job_repository.fetch_job_result",
                           return_value=result) as fetch:
            payload = api.list_company_normalization_review()
        return payload, list_jobs, fetch

    def test_response_carries_last_run_skips(self):
        """🔴 核心：使用者看得到「另有 N 筆被跳過」的唯一資料來源。"""
        job = mock.Mock(job_id=416, job_type="ai:company_normalization_suggestion",
                        status="succeeded")
        payload, _, fetch = self._call(
            [job],
            {"inserted": 1, "suggestion_count": 1, "skipped_invalid": 3,
             "skipped_details": [{"candidate_refs": ["cand:1"], "reason": "evidence …"}]})

        self.assertIn("last_run", payload, "端點沒有帶出最近一次 run 的資訊")
        last = payload["last_run"] or {}
        self.assertEqual(last.get("skipped_invalid"), 3)
        self.assertEqual(len(last.get("skipped_details") or []), 1)
        self.assertEqual(last.get("run_id"), 416)
        fetch.assert_called_once()

    def test_only_looks_at_normalization_jobs(self):
        """⚠ 不得抓到別型 job 的結果——那會把不相干的數字寫在這個面板上。"""
        _, list_jobs, _ = self._call([], None)
        kwargs = list_jobs.call_args.kwargs
        self.assertEqual(kwargs.get("job_type"), "ai:company_normalization_suggestion",
                         f"沒有依 job_type 過濾：{kwargs}")
        self.assertEqual(kwargs.get("status"), "succeeded",
                         "未完成的 run 不該拿來當揭露來源")

    def test_no_run_yet_is_not_an_error(self):
        """一次都還沒跑過時要正常回應，不是 500。"""
        payload, _, fetch = self._call([], None)
        self.assertIsNone(payload.get("last_run"))
        fetch.assert_not_called()


class FrontendReadsFromPayloadTests(unittest.TestCase):
    """前端要真的畫得出來——用 node 執行函式，不是斷言字串存在。"""

    @classmethod
    def setUpClass(cls):
        cls.node = _find_node()
        cls.src = INDEX.read_text(encoding="utf-8")

    def _render(self, setup_js: str) -> str:
        if self.node is None:
            self.skipTest("node 不在 PATH 也不在 D:/vscode/node.js")
        fn = _extract_function(self.src, "companyNormalizationSkippedHtml")
        js = (
            "function escHtml(s){return String(s==null?'':s)"
            ".replace(/&/g,'&amp;').replace(/</g,'&lt;');}\n"
            + setup_js + "\n" + fn + "\n"
            "process.stdout.write(String(companyNormalizationSkippedHtml() || ''));"
        )
        tmp = Path(__file__).parent / "_skip_transport_check.js"
        try:
            tmp.write_text(js, encoding="utf-8")
            proc = subprocess.run([self.node, str(tmp)], capture_output=True,
                                  text=True, encoding="utf-8", timeout=60)
            self.assertEqual(proc.returncode, 0,
                             f"函式執行失敗：\n{proc.stderr[:800]}")
            return proc.stdout
        finally:
            tmp.unlink(missing_ok=True)

    def test_renders_from_suggestion_payload(self):
        """🔴 核心：只給建議端點的 last_run，就必須畫得出來。

        ⚠ 這裡**刻意不設** taskListCache——舊實作靠它，而它永遠是 null，
        所以舊實作在這個測試下會回空字串。
        """
        html = self._render(
            "let companyNormalizationLastRun = {run_id: 416, skipped_invalid: 3,"
            " skipped_details: [{candidate_refs:['cand:1'], reason:'evidence 不足'}]};"
        )
        self.assertIn("3", html, "沒有把跳過筆數畫出來")
        self.assertIn("跳過", html)
        self.assertIn("cand:1", html, "沒有列出是哪一筆候選")

    def test_renders_nothing_when_no_skips(self):
        """沒有跳過就不要憑空長出一塊警告。"""
        html = self._render(
            "let companyNormalizationLastRun = {run_id: 416, skipped_invalid: 0,"
            " skipped_details: []};")
        self.assertEqual(html.strip(), "")

    def test_does_not_depend_on_task_list_cache(self):
        """⚠ 守住根因：任務清單只留最近 N 筆，靠它會讓揭露被擠掉後靜默消失。"""
        fn = _extract_function(self.src, "companyNormalizationSkippedHtml")
        self.assertNotIn("taskListCache", fn,
                         "又回去讀任務清單了——那條路 result 永遠是 null，"
                         "且 succeeded 只留最近幾筆")


if __name__ == "__main__":
    unittest.main()
