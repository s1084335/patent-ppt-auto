"""案件比對 · 標的資料抽取（資料庫為唯一來源；本輪以一件專利模擬標的）。

欄位查證（information_schema，patent_ppt_understanding，2026-07-21 實查）：
- 文字欄自 core_layer.patents 取：title、abstract、"主權項"。
- PDF 連結自 core_layer.patents."文圖像文件(PDF)連結"（931/932 非空）。
  ⚠ 0046（2026-08-06）前它在 patent_attributes（一 raw_record 一列），本模組得自己
  寫「取 raw_record_id 最大者」；搬進 patents 後一專利一列，**選列規則消失**——
  這正是搬欄位的目的（原本 patent_queries／refresh 各寫一套選列，不保證一致）。
  "主附圖" 僅 1 筆有值不採用；
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

from backend.app.transforms.patent_numbers import DISPLAY_NUMBER_PRIORITY

# 專利號查找順序（第一個非空者作 patent_number）。
# 2026-08-04 治本收斂：從顯示鏈唯一定義處**推導**——比對標的只認公告號與轉換後
# （0020 定案，不含原值公開號／申請號），故過濾掉原值欄而非另抄一份順序。
PATENT_NUMBER_COLUMNS = [
    column for column in DISPLAY_NUMBER_PRIORITY
    if column not in ("未審查的公開號", "申請號")
]
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
            # 0046 起 PDF 連結就在 patents 主表，一專利一列、直接投影
            f'p."{COL_PDF_URL}" '
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
            "tables": ["core_layer.patents"],
            "text_fields": ["title", "abstract", "主權項"],
            "pdf_url_rule": "core_layer.patents 主表直取（0046 起一專利一列）",
        },
        # 本輪以一件專利模擬標的（真標的來源為使用者提供的產品資料，尚未接）
        "simulated": True,
    }
