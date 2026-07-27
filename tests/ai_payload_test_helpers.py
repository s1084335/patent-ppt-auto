"""測試用：從 CLI argv 讀回資料檔內容（模擬真實 CLI 以 Read 讀檔的行為）。

2026-07-27 起三支高風險 AI runner（topic_label／patent_note／irrelevant_filter）
的資料改走檔案而非命令列參數（Windows CreateProcess 上限 32,767，實測 topic_label
達 128,101 字元必爆）。各測試的 fake CLI 原本從 prompt 字串解析 id，改架構後讀不到，
故統一改為「與真實 CLI 同樣去讀 argv 內的資料檔」。

抽成共用 helper 而非三個測試檔各寫一份，理由同本輪重構：同一件事不要散落。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence


def read_payload_from_argv(argv: Sequence[str]) -> dict[str, Any]:
    """從 argv 找出資料檔路徑並讀回內容；找不到回空 dict。

    路徑可能含中文或空白，故逐行找「資料檔…：<path>」而不用 \\S+ 硬切。
    """
    for arg in argv:
        for line in str(arg).splitlines():
            if "資料檔" not in line or ".json" not in line:
                continue
            candidate = Path(line.split("：", 1)[-1].strip())
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
    return {}


def patent_ids_from_argv(argv: Sequence[str], *, key: str = "items") -> list[int]:
    """取資料檔內該批的 patent_id 清單（patent_note／irrelevant_filter 共用）。"""
    data = read_payload_from_argv(argv)
    return [int(item["patent_id"]) for item in data.get(key, []) if "patent_id" in item]


def topic_codes_from_argv(argv: Sequence[str]) -> list[str]:
    """取資料檔內該批的 topic_code 清單（topic_label 用）。"""
    data = read_payload_from_argv(argv)
    return [str(t["topic_code"]) for t in data.get("topics", []) if "topic_code" in t]
