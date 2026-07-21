"""案件比對 · 標的資料抽取（資料庫為唯一來源；本輪以一件專利模擬標的）。

欄位查證（information_schema，patent_ppt_understanding，2026-07-21 實查）：
- 文字欄自 core_layer.patents 取：title、abstract、"主權項"。
- PDF 連結自 core_layer.patent_attributes."文圖像文件(PDF)連結"（931/932 非空）；
  同 patent 多列取 raw_record_id 最大者。"主附圖" 僅 1 筆有值不採用；
  "詳細查看連結(登入)" 需登入不採用。
- patent_number 依 0020 定案的專利號查找順序取第一個非空值：
  授權公告號 → 審查的公告號 → 未審查的公開號(轉換後) → 申請號(轉換後)。

契約：
- extract_target_from_db(patent_id) 回標的 dict（text 欄、pdf_url、來源註記、simulated=True）。
- patent 不存在拋 TargetSourceNotFoundError；title/abstract/主權項 全空拋 TargetSourceEmptyError
  （標的以文字欄為主，僅有 PDF 連結不足以建立標的）。
- 條列特徵不在本模組做（AI＋使用者確認的事）；存 run 走 ComparisonStore.save_target。
"""
from __future__ import annotations

from typing import Any

# 專利號查找順序（第一個非空者作 patent_number）
PATENT_NUMBER_COLUMNS = ["授權公告號", "審查的公告號", "未審查的公開號(轉換後)", "申請號(轉換後)"]
COL_PDF_URL = "文圖像文件(PDF)連結"


class TargetSourceError(ValueError):
    """標的資料抽取相關錯誤基底。"""


class TargetSourceNotFoundError(TargetSourceError):
    """指定 patent 不存在。"""


class TargetSourceEmptyError(TargetSourceError):
    """title、abstract、主權項 全空，無法建立文字標的。"""


def _connect(connect_kwargs: dict[str, Any] | None):
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**(connect_kwargs or get_connection_kwargs()))


def _clean(value: Any) -> str | None:
    """去空白；空字串／None 一律回 None，避免把空值當有效內容。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def extract_target_from_db(patent_id: int,
                           connect_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    """抽取單一 patent 組成模擬標的 dict（simulated=True）。

    Raises:
        TargetSourceNotFoundError: patent 不存在。
        TargetSourceEmptyError: title/abstract/主權項 全空。
    """
    number_cols = ", ".join(f'p."{c}"' for c in PATENT_NUMBER_COLUMNS)
    with _connect(connect_kwargs) as conn:
        row = conn.execute(
            f'SELECT p.title, p.abstract, p."主權項", {number_cols}, '
            # 同 patent 多列取 raw_record_id 最大者的 PDF 連結（定案規則）
            f'(SELECT a."{COL_PDF_URL}" FROM core_layer.patent_attributes a '
            " WHERE a.patent_id = p.id ORDER BY a.raw_record_id DESC LIMIT 1) "
            "FROM core_layer.patents p WHERE p.id = %s",
            (patent_id,),
        ).fetchone()
    if row is None:
        raise TargetSourceNotFoundError(f"patent {patent_id} 不存在")

    title, abstract, main_claim = (_clean(v) for v in row[:3])
    numbers = row[3:3 + len(PATENT_NUMBER_COLUMNS)]
    pdf_url = _clean(row[-1])
    if title is None and abstract is None and main_claim is None:
        raise TargetSourceEmptyError(
            f"patent {patent_id} 的 title/abstract/主權項 全空，無法建立文字標的")
    patent_number = next((n for n in (_clean(v) for v in numbers) if n), None)

    return {
        "patent_id": patent_id,
        "patent_number": patent_number,
        "title": title,
        "abstract": abstract,
        "main_claim": main_claim,
        "pdf_url": pdf_url,
        # 來源註記：讓下游知道欄位出處與 pdf_url 取值規則
        "source": {
            "tables": ["core_layer.patents", "core_layer.patent_attributes"],
            "text_fields": ["title", "abstract", "主權項"],
            "pdf_url_rule": "同 patent 多列取 raw_record_id 最大者",
        },
        # 本輪以一件專利模擬標的（真標的來源為使用者提供的產品資料，尚未接）
        "simulated": True,
    }
