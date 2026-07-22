"""Railway 舊 worker 入口相容層。

正式 worker 入口是 `backend.app.worker.runner`。保留這個 module 是為了讓已部署平台
若仍使用 `python -m backend.app.worker.main`，容器不會因找不到模組而 crash。
"""

from __future__ import annotations

import sys

from . import runner


def main() -> None:
    """把舊入口轉交正式 runner；未指定 command 時預設長駐 `serve`。"""
    if len(sys.argv) <= 3 and sys.argv[-1] == "backend.app.worker.main":
        sys.argv.append("serve")
    elif len(sys.argv) == 1:
        sys.argv.append("serve")
    runner.main()


if __name__ == "__main__":
    main()
