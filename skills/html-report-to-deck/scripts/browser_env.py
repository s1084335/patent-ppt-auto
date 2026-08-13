"""Playwright 環境解析的**唯一定義處**（tasks 2.2d）。

## 為什麼要收斂

`fit_render_charts` 與 `shoot_pages` 原本各寫一份相同的三行：
    _PW = Path(os.environ.get("PLAYWRIGHT_HOME", r"D:\\vscode\\playwright"))
    sys.path.insert(0, str(_PW / "lib"))
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PW / "browsers"))
改一處不會同步另一處——而症狀是「其中一支腳本在產線找不到瀏覽器」，
另一支卻正常，很難聯想到是同一件事。

## 為什麼不進 `pyproject`

design 4-0b 曾建議把 playwright 列入 `pyproject`。實作時重新評估：
vendored ＋ `PLAYWRIGHT_HOME` 覆寫**本來就是可攜的**——產線只要把變數指到
它自己的安裝位置即可，不必在專案 venv 再裝一份套件與數百 MB 的 browsers。
真正的問題是「路徑解析有兩個落點」，那由本模組解決。

⚠ `PLAYWRIGHT_BROWSERS_PATH` 用 `setdefault`：呼叫端若已自行設定就尊重它。
⚠ 但**不要在 shell 另設**——`setdefault` 不會覆蓋你設的值，設到上一層就會噴
`Executable doesn't exist`（2026-08-11 踩過，見 pitfalls）。要換位置改設
`PLAYWRIGHT_HOME`。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: 開發機的既有安裝位置。產線用 `PLAYWRIGHT_HOME` 指到自己的。
DEFAULT_PLAYWRIGHT_HOME = r"D:\vscode\playwright"


def playwright_home() -> Path:
    """解析 Playwright 安裝根目錄。"""
    return Path(os.environ.get("PLAYWRIGHT_HOME", DEFAULT_PLAYWRIGHT_HOME))


def ensure_playwright() -> Path:
    """備妥 import path 與 browsers 路徑，回傳安裝根目錄。

    ⚠ 在 `from playwright...` **之前**呼叫——套件本身就在 vendored 的 lib 底下。
    """
    home = playwright_home()
    lib = str(home / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(home / "browsers"))
    return home
