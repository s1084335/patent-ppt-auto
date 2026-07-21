"""匯入上傳的受控路徑、Web 白名單與安全刪檔 helper。

API 與 worker 共用同一份規則（imports root、副檔名白名單、path traversal 判斷、
只刪本次上傳目錄），避免兩邊各寫一套而失守。實際 root 由 settings.get_imports_root()
單一提供。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from backend.app import settings


# Web 上傳與 worker 接受的副檔名白名單：**不含 .mdb**。Linux worker 常缺 pyodbc/Access
# driver，讓 .mdb 在上傳階段就被擋（422），而非匯入時才失敗；CLI importer 仍支援 .mdb。
WEB_IMPORT_SUFFIXES = (".xlsx", ".csv", ".txt", ".xml")


def imports_root() -> Path:
    """匯入落地根目錄（已 resolve）；委派 settings.get_imports_root()。"""
    return settings.get_imports_root()


def is_within_imports_root(path: Path) -> bool:
    """path 解析後是否位於 imports root 之下（path traversal 防禦）。"""
    try:
        Path(path).resolve().relative_to(imports_root())
        return True
    except ValueError:
        return False


def validate_web_filename(filename: str) -> str:
    """驗證上傳原檔名並回傳可安全落地的 basename；不合法丟 ValueError（由 API 轉 422）。

    拒絕：空字串、含路徑分隔（/、\\）或 null、含 `..`、帶任何目錄成分（Path(name).name != name），
    以及副檔名不在 WEB_IMPORT_SUFFIXES（含 .mdb）。
    """
    name = (filename or "").strip()
    if not name:
        raise ValueError("filename is required")
    if any(token in name for token in ("/", "\\", "\x00")) or ".." in name or Path(name).name != name:
        raise ValueError("invalid filename (path traversal not allowed)")
    suffix = Path(name).suffix.lower()
    if suffix not in WEB_IMPORT_SUFFIXES:
        raise ValueError(
            f"unsupported import format: {suffix or '(none)'}; allowed={list(WEB_IMPORT_SUFFIXES)}"
        )
    return name


def remove_import_dir(dir_path: Path) -> None:
    """只刪位於 imports root 下、且非 root 本身的本次上傳目錄；其餘一律拒絕，不做任意刪路徑。"""
    root = imports_root()
    target = Path(dir_path).resolve()
    if target == root or not is_within_imports_root(target):
        return
    shutil.rmtree(target, ignore_errors=True)
