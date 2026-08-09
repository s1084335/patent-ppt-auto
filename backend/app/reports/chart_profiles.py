"""web／PPT 雙 rendering profile（P3，openspec separate-web-and-ppt-chart-profiles）。

同一 chart identity、同一份資料與色彩語意，**只允許尺寸／DPI／字級／邊距不同**。

⚠ 不建立第二套 chart engine（Non-goals 明列）：兩套必然漂移——同一張圖在網頁與
PPT 說不同的話，是本專案已重複踩過四次的「同一份知識兩處落點」。做法是同一支
renderer 依 profile 調整**呈現參數**，資料、排序、配色一律共用。

⚠ 缺少或版本不符的 PPT profile 一律 fail loud：不得退回舊圖、不得讓 CLI 自選圖
（CLI 只能用使用者選的那張的 PPT 版本）。
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from backend.app.reports.chart_sizing import PPT as _PPT
from backend.app.reports.chart_sizing import WEB as _WEB

# 🔴 尺寸與字級的**唯一定義處是 `chart_sizing`**（2026-08-03 既有架構，
# 註解已寫明「WEB 欄位已備好，日後在渲染端分流」）——本模組只做名稱對應與
# 呈現層衍生值，**不自帶第二份數字**（自帶就是同一份知識兩個落點）。
_SIZING_BY_PROFILE = {"web": _WEB, "ppt": _PPT}

PROFILES: dict[str, dict[str, Any]] = {
    name: {
        "width_px": sizing.canvas_width,
        "min_font_pt": sizing.note_target_pt,
        "scale": round(sizing.canvas_width / _PPT.canvas_width, 3),
        "dpi": 144 if name == "ppt" else 96,
        "margin_px": 32 if name == "ppt" else 24,
        "label": "簡報" if name == "ppt" else "網頁",
    }
    for name, sizing in _SIZING_BY_PROFILE.items()
}

# 🔴 預設 ppt：既有系統是單一輸出、以 PPT 約束為準（圖會被縮進圖框）。
# web profile 是**新增能力、顯式啟用**——預設改 web 會讓既有圖表全部換尺寸
# （2026-08-07 實測 13 支測試紅）。不拆舊路徑的同一條原則。
DEFAULT_PROFILE = "ppt"

# web profile 的檔名＝既有檔名加 `.web` 中綴；PPT profile 沿用既有檔名。
_WEB_INFIX = ".web"
_WEB_NAME_RE = re.compile(r"^(?P<stem>.+)\.web\.svg$")

# 目前作用中的 profile（renderer 依它取呈現參數）。
_active_profile = DEFAULT_PROFILE


class ChartProfileError(RuntimeError):
    """profile 解析失敗（identity 不存在、缺 PPT 版本、版本不符）。"""


def active_profile() -> dict[str, Any]:
    """目前作用中的呈現參數；renderer 只讀這裡，不各自判斷 web/ppt。"""
    return PROFILES[_active_profile]


def active_sizing():
    """目前 profile 的 ChartSizing（chart_runner 取畫布與字級目標用）。"""
    return _SIZING_BY_PROFILE[_active_profile]


def active_profile_name() -> str:
    return _active_profile


@contextmanager
def profile_context(profile: str):
    """在此區塊內以指定 profile 產圖（同一支 renderer，換的只有呈現參數）。"""
    global _active_profile
    if profile not in PROFILES:
        raise ChartProfileError(f"未知 profile {profile!r}；只有 {sorted(PROFILES)}")
    previous = _active_profile
    _active_profile = profile
    try:
        yield PROFILES[profile]
    finally:
        _active_profile = previous


def profile_filename(base_name: str, profile: str) -> str:
    """既有圖檔名 ＋ profile → 該 profile 的檔名。

    🔴 2026-08-09 契約回寫（原為 `report_key__variant.profile.svg`）：

    1. `annual_trend.svg` **同時**是 `application_trend` 與 `publication_trend`
       兩個 report_key 的圖——「一檔一 identity」的命名模型表達不了。
    2. 既有檔名與 report_key 本來就不同名（`country_distribution` 的圖叫
       `jurisdiction_distribution.svg`），改名會波及 artifact_manifest、
       ChartIndex、build_ppt 與所有既有報表版本。

    ⇒ **PPT profile 沿用既有檔名**（既有一切不動），**web profile 加 `.web`
    中綴**。identity→path 的對應改由 manifest 維護，不寫進檔名。
    """
    if profile not in PROFILES:
        raise ChartProfileError(f"未知 profile {profile!r}")
    name = Path(base_name).name
    # ⚠ 非 SVG（分群主題表等 HTML 產物）沒有 profile 之分，原樣回傳——
    # 切字串會產出 `cluster_topic_ta.web.svg` 這種壞檔名。
    if profile == "ppt" or not name.endswith(".svg"):
        return name
    return f"{name[:-len('.svg')]}{_WEB_INFIX}.svg"


def resolve_web_asset(file_name: str, exists: "Callable[[str], bool]") -> str:
    """網頁報表要顯示的圖檔名：有 web profile 就用它，沒有就用原檔。

    ⚠ 退回**不是**可有可無的寬容：`.web.svg` 是 2026-08-09 才開始產的，在那
    之前的每一個報表版本都只有一份 PPT 尺寸的圖。不退回＝舊版本網頁全空。

    ⚠ 與 `resolve_ppt_asset` 的 fail-loud 態度**刻意不同**：PPT 那邊拿錯圖會
    讓簡報悄悄用到別版資料（比產不出來更糟），這邊最壞只是網頁看到 PPT 尺寸
    的圖——都看得到內容，不需要為此讓整頁掛掉。
    """
    if not file_name.endswith(".svg"):
        return file_name
    web_name = profile_filename(file_name, "web")
    return web_name if exists(web_name) else file_name


def parse_profile_filename(name: str) -> tuple[str, str]:
    """檔名 → (既有圖檔名, profile)。非 `.svg` 一律 fail loud。"""
    plain = Path(name).name
    if not plain.endswith(".svg"):
        raise ChartProfileError(f"檔名 {name!r} 不是 SVG 圖檔")
    match = _WEB_NAME_RE.match(plain)
    if match is not None:
        return f"{match.group('stem')}.svg", "web"
    return plain, "ppt"


# ⚠ `build_profile_manifest` 在 **chart_runner**：identity 要靠「檔名 →
# report_names」對照表反查，那張表是 chart_runner 的；放這裡會反向相依。

def resolve_ppt_asset(
    identity: str,
    manifest: dict[str, Any],
    expect_version: str | None = None,
) -> dict[str, Any]:
    """使用者在網頁選的圖 → 交給組版／CLI 的同 identity PPT profile。

    ⚠ 缺 PPT profile、identity 不存在或版本不符一律 raise——退回舊圖會讓簡報
    悄悄用到別版資料，比產不出來更糟。
    """
    entry = (manifest.get("charts") or {}).get(identity)
    if entry is None:
        raise ChartProfileError(
            f"chart identity {identity!r} 不在本版 manifest；不得改用其他圖替代")
    if expect_version is not None and entry.get("version") != expect_version:
        raise ChartProfileError(
            f"{identity} 的 profile 版本 {entry.get('version')!r} 與預期 "
            f"{expect_version!r} 不符——過期圖不得混用")
    asset = (entry.get("profiles") or {}).get("ppt")
    if asset is None:
        raise ChartProfileError(
            f"{identity} 缺 PPT profile；請重產該版本圖表，不得退回網頁版或舊圖")
    return asset
