"""舊版本圖檔名的相容 resolver（unify-chart-source 後本模組僅剩此職責）。

## 沿革

- P3（separate-web-and-ppt-chart-profiles）曾在此維護 web／ppt 雙 profile：
  雙輪渲染、`.web` 中綴檔名、profile_manifest、resolve_ppt_asset。
- 2026-08-10 PPT 交付線移除後，PPT 側消費者（chart_bundle／build_ppt）消滅，
  resolve_ppt_asset 全庫零呼叫、profile_manifest 零讀者。
- 🔴 2026-08-12 使用者定案（unify-chart-source）：**每張圖只產一份 SVG**
  （WEB 尺寸、既有原檔名），HTML 原樣顯示、簡報端（deck）自行 refit 字級
  ——「一方產生、消費端適配」。雙 profile 機制全數退場，本模組縮編為兩件事：
  ①供應唯一 sizing（＝chart_sizing.WEB）②舊版本目錄的圖檔名 resolver。

⚠ 尺寸與字級的唯一定義處是 `chart_sizing`（chart_runner 直接綁 WEB）；
本模組不再供應 sizing——active_sizing 轉手層已依刪除測試原則併掉。
"""
from __future__ import annotations

from collections.abc import Callable

# 舊版本目錄的 web 中綴（本模組已不再產生這種檔名，只在讀取端相容）。
_WEB_INFIX = ".web"


def resolve_web_asset(file_name: str, exists: Callable[[str], bool]) -> str:
    """網頁報表要顯示的圖檔名——新舊版本零遷移的關鍵。

    | 版本 | 原檔名內容 | `.web.svg` | 回傳 |
    |---|---|---|---|
    | 舊（雙 profile 時代） | PPT 尺寸 | 有 | `.web.svg` |
    | 新（單一來源） | WEB 尺寸 | 無 | 原檔 |

    ⚠ 退回**不是**可有可無的寬容：兩個時代的版本目錄都要顯示正確的圖，
    且不重產舊版本。
    """
    if not file_name.endswith(".svg"):
        return file_name
    web_name = f"{file_name.removesuffix('.svg')}{_WEB_INFIX}.svg"
    return web_name if exists(web_name) else file_name
