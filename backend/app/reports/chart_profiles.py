"""web／PPT 雙 rendering profile（P3，openspec separate-web-and-ppt-chart-profiles）。

同一 chart identity、同一份資料與色彩語意，**只允許尺寸／DPI／字級／邊距不同**。

⚠ 不建立第二套 chart engine（Non-goals 明列）：兩套必然漂移——同一張圖在網頁與
PPT 說不同的話，是本專案已重複踩過四次的「同一份知識兩處落點」。做法是同一支
renderer 依 profile 調整**呈現參數**，資料、排序、配色一律共用。

⚠ 缺少或版本不符的 PPT profile 一律 fail loud：不得退回舊圖、不得讓 CLI 自選圖
（CLI 只能用使用者選的那張的 PPT 版本）。
"""
from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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

# 檔名格式：`{report_key}__{variant_key}.{profile}.svg`
_FILENAME_RE = re.compile(r"^(?P<report>.+?)__(?P<variant>.+?)\.(?P<profile>web|ppt)\.svg$")

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


def profile_filename(report_key: str, variant_key: str, profile: str) -> str:
    """chart identity ＋ profile → 檔名（identity 與 profile 都寫在名字裡）。"""
    if profile not in PROFILES:
        raise ChartProfileError(f"未知 profile {profile!r}")
    return f"{report_key}__{variant_key}.{profile}.svg"


def parse_profile_filename(name: str) -> tuple[str, str, str]:
    """檔名 → (report_key, variant_key, profile)；不合格式即 fail loud。"""
    match = _FILENAME_RE.match(Path(name).name)
    if match is None:
        raise ChartProfileError(f"檔名 {name!r} 不是 profile 圖檔格式")
    return match.group("report"), match.group("variant"), match.group("profile")


def build_profile_manifest(run_dir: Path, version: str) -> dict[str, Any]:
    """掃描版本目錄，建立 identity → 各 profile 的 manifest（含 checksum）。

    checksum 綁檔案內容：兩個 profile 的 checksum 必然不同（尺寸不同），
    配錯資料或拿到過期檔時對不上。
    """
    charts: dict[str, dict[str, Any]] = {}
    for path in sorted(run_dir.glob("*.svg")):
        try:
            report_key, variant_key, profile = parse_profile_filename(path.name)
        except ChartProfileError:
            continue                      # 非 profile 圖（既有單一版本圖）略過
        identity = f"{report_key}:{variant_key}"
        entry = charts.setdefault(identity, {"version": version, "profiles": {}})
        entry["profiles"][profile] = {
            "path": path.name,
            "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return {"version": version, "charts": charts}


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
