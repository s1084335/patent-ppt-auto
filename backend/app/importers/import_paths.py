"""匯入上傳的檔名驗證與 Web 副檔名白名單。

API 與 worker 共用同一份規則（副檔名白名單、path traversal 判斷），避免兩邊各寫一套而失守。

2026-07-23：上傳內容改存 DB（app_layer.import_blobs）不再落地到 imports root——Railway 上
backend 與 worker 是不同容器、檔案系統不共享。原本的 imports_root()／is_within_imports_root()／
remove_import_dir() 隨之無用而移除；檔名白名單與 traversal 驗證仍保留（上傳端擋副檔名、
worker 端依 original_filename 再驗一次）。
"""
from __future__ import annotations

from pathlib import Path


# Web 上傳與 worker 接受的副檔名白名單：**不含 .mdb**。Linux worker 常缺 pyodbc/Access
# driver，讓 .mdb 在上傳階段就被擋（422），而非匯入時才失敗；CLI importer 仍支援 .mdb。
WEB_IMPORT_SUFFIXES = (".xlsx", ".csv", ".txt", ".xml")


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
