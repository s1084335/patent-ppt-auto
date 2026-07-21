"""案件比對 · 說明書 PDF 下載（WIPS 直下載連結，內容雜湊快取）。

儲存規約（沿用 patent_images 資產目錄）：
  data/patent_assets/<safe_pn>/<sha256>/source.pdf
  sha256 = 下載內容雜湊；同 hash 目錄已存在即跳過重寫（快取命中 from_cache=True）。

拒收規則（不落檔）：
- 非 200 → PdfFetchError（訊息帶 status code）。
- 非 PDF magic bytes（%PDF-）→ PdfNotPdfError（WIPS 連結時效/權限失效常回 HTML 登入頁）。
- 傳輸層例外（timeout、連線失敗）→ 包成 PdfFetchError，保留原因。

HTTP 客戶端用 pyproject 既有依賴 httpx（mcp 相依，已在 lock）；http_get 可注入供單元測試
不打網路。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from backend.app.comparison.patent_images import safe_patent_component

PDF_MAGIC = b"%PDF-"
SOURCE_PDF_NAME = "source.pdf"
DEFAULT_BASE_DIR = "data/patent_assets"

# http_get 契約：(url, timeout) -> (status_code, content_bytes)
HttpGet = Callable[[str, float], tuple[int, bytes]]


class PdfFetchError(RuntimeError):
    """PDF 下載失敗（非 200、傳輸錯誤等）。"""


class PdfNotPdfError(PdfFetchError):
    """回應內容非 PDF（magic bytes 不符，常見為登入頁 HTML）。"""


def _default_http_get(url: str, timeout: float) -> tuple[int, bytes]:
    """真實 HTTP GET（httpx，跟隨轉址）。"""
    import httpx

    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    return resp.status_code, resp.content


def fetch_patent_pdf(url: str, patent_number: str, base_dir: str = DEFAULT_BASE_DIR,
                     timeout: float = 30.0, http_get: HttpGet | None = None) -> dict[str, Any]:
    """下載說明書 PDF 到內容雜湊目錄；回傳 {pdf_path, sha256, size_bytes, from_cache}。

    先下載並驗證，再算 sha256 決定落點；同 hash 已存在跳過重寫（重跑不覆蓋）。
    Raises:
        PdfFetchError: 非 200 或傳輸層錯誤。
        PdfNotPdfError: 內容非 PDF magic bytes。
    """
    getter = http_get or _default_http_get
    try:
        status, content = getter(url, timeout)
    except PdfFetchError:
        raise
    except Exception as exc:  # noqa: BLE001 — 傳輸層例外統一包成明確錯誤
        raise PdfFetchError(f"下載失敗（傳輸層）：{exc}") from exc

    if status != 200:
        raise PdfFetchError(f"下載失敗：HTTP {status}（url={url}）")
    if not content.startswith(PDF_MAGIC):
        head = content[:40]
        raise PdfNotPdfError(f"內容非 PDF（magic bytes 不符，開頭={head!r}）；"
                             "可能為連結時效或需登入的 HTML 頁")

    sha256 = hashlib.sha256(content).hexdigest()
    target_dir = Path(base_dir) / safe_patent_component(patent_number) / sha256
    pdf_path = target_dir / SOURCE_PDF_NAME
    from_cache = pdf_path.exists()  # 同 hash 已存在即跳過（內容雜湊保證一致）
    if not from_cache:
        target_dir.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(content)
    return {
        "pdf_path": str(pdf_path),
        "sha256": sha256,
        "size_bytes": len(content),
        "from_cache": from_cache,
    }
