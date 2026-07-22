"""Railway 部署入口契約測試。

同一個 Docker image 會同時給 backend 與 worker 使用；正式部署靠 APP_ROLE
決定啟動哪個程序，backend 必須讀平台注入的 PORT，worker 則不需要對外 port。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock


class DeployEntrypointTests(unittest.TestCase):
    """確認部署入口不寫死本機 port，也不把 worker 當成 web server 啟動。"""

    def test_backend_role_uses_platform_port(self) -> None:
        """APP_ROLE=backend 時，uvicorn 必須使用 Railway 注入的 PORT。"""
        from backend.app import deploy

        env = {"APP_ROLE": "backend", "PORT": "9123"}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(deploy, "_run_process") as run_process:
                deploy.main()

        run_process.assert_called_once_with(
            [
                "python",
                "-m",
                "uvicorn",
                "backend.app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "9123",
            ]
        )

    def test_worker_role_uses_worker_main(self) -> None:
        """APP_ROLE=worker 時，啟動正式 worker shim，不吃 PORT。"""
        from backend.app import deploy

        env = {"APP_ROLE": "worker", "PORT": "9123"}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(deploy, "_run_process") as run_process:
                deploy.main()

        run_process.assert_called_once_with(
            ["python", "-m", "backend.app.worker.main"]
        )

    def test_unknown_role_fails_fast(self) -> None:
        """APP_ROLE 拼錯時要提早失敗，避免 Railway 靜默跑錯服務。"""
        from backend.app import deploy

        with mock.patch.dict(os.environ, {"APP_ROLE": "other"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "unsupported APP_ROLE"):
                deploy.main()


if __name__ == "__main__":
    unittest.main()
