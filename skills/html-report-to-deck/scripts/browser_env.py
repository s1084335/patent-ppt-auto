"""Playwright 環境準備的**唯一定義處**（tasks 2.2d）。

## 兩件事，都要做

**套件**由 `pyproject` 宣告（`playwright>=1.62.0`）——那才能 `uv sync` 就裝得起來、
版本釘得住。⚠ chromium 版本是 `getBBox` 量測的變因（design 4c-1 實測過），
不釘版等於量測基準會隨機器漂。

**瀏覽器本體**（150–400 MB）不進 `pyproject`，由 `PLAYWRIGHT_BROWSERS_PATH` 指向
既有安裝——開發機沿用 `D:\\vscode\\playwright\\browsers`，不重複下載。

## 為什麼要有這個模組

`fit_render_charts` 與 `shoot_pages` 原本各寫一份相同的三行路徑解析。
改一處不會同步另一處，而症狀是「其中一支腳本在產線找不到瀏覽器」、另一支正常
——很難聯想到是同一件事。

## ⚠ vendored 回退是**過渡**

`pyproject` 已宣告 playwright，但既有環境要跑過 `uv sync` 才會裝上。在那之前
`import playwright` 會失敗，故保留「找不到套件才插 vendored lib」的回退。
同時只有一條生效（有套件就不插），不是雙軌。
🔴 **環境完成 sync 後可移除 `_fallback_vendored_lib`**，並把本節一併刪掉。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

#: 開發機既有的 Playwright 安裝根（含 lib/ 與 browsers/）。
#: 產線用 `PLAYWRIGHT_HOME` 指到自己的位置。
DEFAULT_PLAYWRIGHT_HOME = r"D:\vscode\playwright"


def playwright_home() -> Path:
    """解析 Playwright 安裝根目錄。"""
    return Path(os.environ.get("PLAYWRIGHT_HOME", DEFAULT_PLAYWRIGHT_HOME))


def _fallback_vendored_lib(home: Path) -> bool:
    """⚠ 過渡措施：環境尚未 `uv sync` 時，改用 vendored 的套件。

    回傳是否真的插了路徑（供除錯與日後移除時確認還有沒有人依賴它）。
    """
    if importlib.util.find_spec("playwright") is not None:
        return False                      # 已有套件，不插——避免兩份並存
    lib = str(home / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    return True


def ensure_playwright() -> Path:
    """備妥 browsers 路徑（必要時回退 vendored 套件），回傳安裝根目錄。

    ⚠ 在 `from playwright...` **之前**呼叫。
    ⚠ `PLAYWRIGHT_BROWSERS_PATH` 用 `setdefault`：呼叫端已設就尊重它。
      但**不要在 shell 另設**——`setdefault` 不會覆蓋你設的值，設到上一層就會噴
      `Executable doesn't exist`（2026-08-11 踩過，見 pitfalls）。要換位置改設
      `PLAYWRIGHT_HOME`。
    """
    home = playwright_home()
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(home / "browsers"))
    _fallback_vendored_lib(home)
    return home
