"""報表用法律狀態四桶。

`backend.app.mappings.legal_status.normalize_legal_status()` 是法律狀態原值
到英文標準桶的唯一來源；本模組只負責把該桶轉成報表呈現用中文欄位。
"""
from __future__ import annotations

from backend.app.mappings.legal_status import (
    STATUS_ALIVE,
    STATUS_DEAD,
    STATUS_PENDING,
    STATUS_UNKNOWN,
    normalize_legal_status,
)

BUCKET_GRANTED = "已授權"
BUCKET_PENDING = "審查中"
BUCKET_DEAD = "已失效"
BUCKET_UNKNOWN = "未知"

# 呈現契約：圖例序、矩陣欄序與報表欄序共用同一份 tuple。
STATUS_BUCKET_ORDER: tuple[str, ...] = (
    BUCKET_GRANTED,
    BUCKET_PENDING,
    BUCKET_DEAD,
    BUCKET_UNKNOWN,
)

_NORMALIZED_TO_REPORT_BUCKET: dict[str, str] = {
    STATUS_ALIVE: BUCKET_GRANTED,
    STATUS_PENDING: BUCKET_PENDING,
    STATUS_DEAD: BUCKET_DEAD,
    STATUS_UNKNOWN: BUCKET_UNKNOWN,
}


def status_bucket(value: object) -> str:
    """將 WIPS/TW legal_status 原字面收斂到報表中文四桶。"""
    return _NORMALIZED_TO_REPORT_BUCKET[normalize_legal_status(None if value is None else str(value))]
