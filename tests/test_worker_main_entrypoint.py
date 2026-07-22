"""worker Railway 相容入口測試。

Railway 的 worker 服務曾設定為 `python -m backend.app.worker.main`；正式入口已改成
`backend.app.worker.runner`，但部署平台可能仍保留舊 Start Command。這裡確保舊入口
不會讓容器 crash，並會導向正式 runner。
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock


class WorkerMainEntrypointTests(unittest.TestCase):
    """驗證舊 worker module entrypoint 仍可安全啟動正式 runner。"""

    def test_main_without_command_defaults_to_runner_serve(self) -> None:
        """Railway 舊 Start Command 沒傳 command 時，預設轉成長駐 worker serve。"""
        from backend.app.worker import main as worker_main

        with mock.patch.object(sys, "argv", ["python", "-m", "backend.app.worker.main"]):
            with mock.patch("backend.app.worker.runner.main") as runner_main:
                worker_main.main()
                self.assertEqual(sys.argv, ["python", "-m", "backend.app.worker.main", "serve"])

        runner_main.assert_called_once_with()

    def test_main_with_command_preserves_explicit_args(self) -> None:
        """若部署已改成顯式 run-once/serve，shim 不改寫使用者提供的參數。"""
        from backend.app.worker import main as worker_main

        argv = ["python", "-m", "backend.app.worker.main", "run-once", "--worker-id", "w1"]
        with mock.patch.object(sys, "argv", argv.copy()):
            with mock.patch("backend.app.worker.runner.main") as runner_main:
                worker_main.main()
                self.assertEqual(sys.argv, argv)

        runner_main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
