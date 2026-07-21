"""app_layer 併表（0021）後的純邏輯 helper：與 DB 無關的正規化與版本規則。

對應 0021 目標表：
- workspaces.patent_ids_json 必須排序、去重（normalize_patent_ids）。
- workflow_outputs 重跑建立新 version、不覆蓋舊輸出（next_output_version）。

本模組只放不連 DB 的純函式，供後續 workspace／workflow service 呼叫。
"""
from __future__ import annotations

from collections.abc import Iterable


def normalize_patent_ids(patent_ids: Iterable[object]) -> list[int]:
    """把 patent_id 集合正規化成排序、去重的正整數陣列。

    供 workspaces.patent_ids_json 使用：字串與整數視為同一 id 去重；非正整數或無法轉 int
    的值視為輸入錯誤（raise ValueError），避免髒資料寫入 workspace。
    """
    unique: set[int] = set()
    for value in patent_ids:
        # bool 是 int 子類、float 會被 int() 靜默截斷（1.5→1），兩者都不可當 patent_id，
        # 必須在 int() 前先擋掉，避免髒值被默默接受。
        if isinstance(value, bool):
            raise ValueError(f"patent_id must not be a bool: {value!r}")
        if isinstance(value, float):
            raise ValueError(f"patent_id must be an integer, not float: {value!r}")
        try:
            pid = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid patent_id: {value!r}") from exc
        if pid <= 0:
            raise ValueError(f"patent_id must be a positive integer: {value!r}")
        unique.add(pid)
    return sorted(unique)


def next_output_version(existing_versions: Iterable[object]) -> int:
    """回傳同一 (run_id, output_type) 的下一個輸出版本。

    等於現有最大版本 +1（沒有任何版本時為 1），確保重跑建立新 version 而不覆蓋舊輸出；
    不依現有版本數量、也不要求版本連續。
    """
    versions = [int(v) for v in existing_versions]
    if not versions:
        return 1
    return max(versions) + 1
