"""Playwright 環境準備的**唯一定義處**（tasks 2.2d）。

## 兩件事，分開處理

**套件**由 `pyproject` 宣告（`playwright>=1.62.0`）——`uv sync` 就裝得起來、
版本釘得住。⚠ chromium 版本是 `getBBox` 量測的變因（design 4c-1 實測過），
不釘版等於量測基準會隨機器漂。

**瀏覽器本體**（150–400 MB）不進 `pyproject`，由 `PLAYWRIGHT_BROWSERS_PATH`
指向既有安裝——開發機沿用 `D:\\vscode\\playwright\\browsers`，不重複下載。

## 為什麼要有這個模組

`fit_render_charts` 與 `shoot_pages` 原本各寫一份相同的路徑解析。
改一處不會同步另一處，而症狀是「其中一支腳本在產線找不到瀏覽器」、另一支正常
——很難聯想到是同一件事。
"""
from __future__ import annotations

import os
from pathlib import Path

#: Playwright 瀏覽器的安裝根（其下應有 `browsers/`）。
#: 產線用 `PLAYWRIGHT_HOME` 指到自己的位置。
DEFAULT_PLAYWRIGHT_HOME = r"D:\vscode\playwright"


def playwright_home() -> Path:
    """解析瀏覽器安裝根目錄。"""
    return Path(os.environ.get("PLAYWRIGHT_HOME", DEFAULT_PLAYWRIGHT_HOME))


def ensure_playwright() -> Path:
    """備妥 browsers 路徑，回傳安裝根目錄。

    ⚠ 在 `from playwright...` **之前**呼叫。
    ⚠ `PLAYWRIGHT_BROWSERS_PATH` 用 `setdefault`：呼叫端已設就尊重它。
      但**不要在 shell 另設**——`setdefault` 不會覆蓋你設的值，設到上一層就會噴
      `Executable doesn't exist`（2026-08-11 踩過，見 pitfalls）。要換位置改設
      `PLAYWRIGHT_HOME`。
    """
    home = playwright_home()
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(home / "browsers"))
    return home
