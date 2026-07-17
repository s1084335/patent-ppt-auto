"""MCP 工具層共用：JSON 序列化正規化。

MCP 工具回傳值最終會序列化成 JSON 給 client（Claude Code）。DB 查詢結果常見
Decimal／date／datetime，圖表結果含 Path——統一在這裡轉成 JSON 原生型別，
工具函式回傳前呼叫 json_safe() 一次即可，避免每支工具各自處理序列化例外。
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    """遞迴把值轉成 json.dumps 可直接序列化的型別。

    Decimal → float（報表數值是計數／平均，精度損失可忽略）；date/datetime →
    ISO 字串；Path → str；dataclass → dict；tuple/set → list；dict 鍵一律轉 str。
    未知型別 fallback str()，序列化不炸（fail-safe，不 fail-loud——工具結果
    寧可降級成字串也不要讓整個回覆失敗）。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    return str(value)
