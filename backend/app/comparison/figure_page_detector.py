"""案件比對 · 圖式頁偵測（依可抽取文字量判定，供「只抽圖式頁」流程使用）。

原理：專利說明書 PDF 中，說明書與權利要求頁有大量文字層；圖式頁整頁是圖（線條、
標號），可抽取文字極少。逐頁取 page.get_text()，去空白後長度低於門檻者判為圖式頁。
此為確定性、通用判斷，不綁特定專利格式或固定頁碼。

門檻取較保守值：圖式頁常有零星圖號（如「FIG. 1」、元件標號數字），故非要求完全無字，
而是遠低於文字頁。回傳 1-based 頁碼，對齊 PatentImagePipeline 的 page_{n:03d} 命名。
"""
from __future__ import annotations

# 一頁可抽取文字（去空白）字元數低於此門檻，視為圖式頁。
# 依真實專利 PDF（data/raw/pdf測試.pdf）校準：圖式頁多為 42–153 字元（含較多元件標號的
# 圖式頁可達 150+），說明書頁動輒 800–6000 字元。取 200 分界，涵蓋標號較多的圖式頁，
# 又與文字頁保持數倍安全距離，避免漏判夾在圖式群中的圖式頁。
FIGURE_PAGE_TEXT_THRESHOLD = 200


def detect_figure_pages(pdf_path: str, threshold: int = FIGURE_PAGE_TEXT_THRESHOLD) -> list[int]:
    """回傳圖式頁的 1-based 頁碼清單。

    逐頁取可抽取文字，去除所有空白字元後長度 < threshold 判為圖式頁。整份皆文字頁
    時回空清單。需要 pymupdf；未安裝時由呼叫端負責（本函式直接 import）。
    """
    import pymupdf

    figure_pages: list[int] = []
    with pymupdf.open(pdf_path) as doc:
        for index, page in enumerate(doc):
            text = page.get_text() or ""
            # 去掉所有空白（含換行、tab）再計長度，避免版面空白灌水
            compact = "".join(text.split())
            if len(compact) < threshold:
                figure_pages.append(index + 1)  # 1-based
    return figure_pages
