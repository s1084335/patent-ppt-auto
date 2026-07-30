r"""子行程必須被強制 UTF-8 輸出（2026-07-30 實機 job #132／#135／#137）。

## 一連串失敗其實是同一個原因

| job | 表面現象 |
|---|---|
| #132、#135 | `AttributeError: 'NoneType' object has no attribute 'splitlines'` |
| #137 | `build_ppt 未回報 pptx 路徑；輸出：stdout 為空` |

⚠ 全部都是 **`completed.stdout is None`**。早上加的 None 防護只是把裸 AttributeError
換成看得懂的訊息——**根因一直沒解決**，我卻當成修好了。

## 根因

`subprocess.run(..., capture_output=True, text=True, encoding="utf-8")`
在父行程沒有 `PYTHONIOENCODING` 時，子行程 `build_ppt.py` 的 stdout 走系統
codepage；父行程用 UTF-8 解碼含中文路徑（`D:\力山\專案\專利_ppt自動\...`）的
輸出時失敗，`stdout` 回 **None**。

⚠ 為何一直測不出來：Companion 由 `Start-Process -WindowStyle Hidden` 啟動，
繼承不到 `PYTHONIOENCODING`；而我每次手動重現都在指令前加了 `PYTHONIOENCODING=utf-8`
——**測試方法本身掩蓋了 bug**。實測對照：

    有 PYTHONIOENCODING：stdout_len=318，正常印出 pptx:／manifest:
    無 PYTHONIOENCODING：stdout_is_none=True，完全沒有輸出

## 定案

不能只靠父行程環境碰巧正確。呼叫子行程時**明確傳入** `PYTHONIOENCODING=utf-8`，
並用 `errors="replace"` 保底——寧可路徑出現替代字元，也不要整包輸出變 None
（None 會讓真正的失敗原因完全消失）。

⚠ 兩個 runner 都要修：`ai_report_ppt_runner`（PPT）與 `ai_narrative_runner`（解讀）。
"""
from __future__ import annotations

import inspect
import unittest


class SubprocessEnvTests(unittest.TestCase):
    """兩支 runner 呼叫子行程時都要強制 UTF-8。"""

    CASES = (
        ("backend.app.worker.ai_report_ppt_runner", "_default_build_ppt"),
        ("backend.app.worker.ai_narrative_runner", None),  # 模組層級搜尋
    )

    def _source(self, module_name: str, func_name: str | None) -> str:
        import importlib

        module = importlib.import_module(module_name)
        if func_name is None:
            return inspect.getsource(module)
        return inspect.getsource(getattr(module, func_name))

    def test_env_passed_to_subprocess(self):
        """🔴 subprocess.run 必須帶 env，且含 PYTHONIOENCODING。

        不傳 env＝完全繼承父行程；Companion 由隱藏視窗啟動時那裡沒有這個變數，
        子行程輸出就會解碼失敗回 None。
        """
        for module_name, func_name in self.CASES:
            with self.subTest(module=module_name):
                src = self._source(module_name, func_name)
                self.assertIn(
                    "PYTHONIOENCODING", src,
                    f"{module_name} 未強制子行程輸出編碼——"
                    "父行程沒有該變數時 stdout 會是 None")

    def test_decode_errors_not_strict(self):
        """🔴 解碼要用 replace，不得讓解碼失敗把整包輸出變成 None。

        ⚠ 這是保底：即使 PYTHONIOENCODING 因某種原因沒生效，
        也該拿到帶替代字元的字串（看得出發生什麼事），而不是 None（線索全失）。
        """
        for module_name, func_name in self.CASES:
            with self.subTest(module=module_name):
                src = self._source(module_name, func_name)
                self.assertIn(
                    'errors="replace"', src,
                    f"{module_name} 的 subprocess.run 未設 errors=\"replace\"，"
                    "解碼失敗時 stdout 會是 None 而非可讀字串")

    def test_none_guard_kept(self):
        """⚠ None 防護不得因為本次修正而移除——那是最後一道保險。"""
        for module_name, func_name in self.CASES:
            with self.subTest(module=module_name):
                src = self._source(module_name, func_name)
                self.assertRegex(
                    src, r'(stdout|completed\.stdout|result\.stdout)\s*or\s*""',
                    f"{module_name} 的 stdout None 防護被移除")


class SubprocessRealRunTests(unittest.TestCase):
    """🔴 實跑一個會印中文路徑的子行程，驗證不回 None。

    ⚠ 不呼叫 build_ppt.py（那要 30+ 秒與完整報表目錄）；用最小 python -c
    印一段中文，重現同一條解碼路徑。
    """

    def test_chinese_output_survives_without_parent_env(self):
        import os
        import subprocess
        import sys

        from backend.app.worker.ai_report_ppt_runner import _subprocess_text_env

        # 模擬 Companion：父行程沒有 PYTHONIOENCODING
        parent_env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
        env = {**parent_env, **_subprocess_text_env()}
        completed = subprocess.run(
            [sys.executable, "-c", "print('pptx: D:\\\\力山\\\\專案\\\\報告.pptx')"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=60,
        )
        self.assertIsNotNone(
            completed.stdout, "含中文路徑的子行程輸出仍回 None——修正未生效")
        self.assertIn("pptx:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
