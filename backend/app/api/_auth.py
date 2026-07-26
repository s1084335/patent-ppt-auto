"""FastAPI 端點的共用 bearer token 依賴（沿用 MCP `_auth.BearerTokenMiddleware` 模式）。

與 MCP 側的差別只在載體：MCP 是整個 http app 外包一層 ASGI middleware，
這裡是 FastAPI dependency，可逐 router／逐端點掛，方便日後擴及其他端點
而不必一次改動全部前端呼叫。token 比對規則與 MCP 側一致。

未設定 token 的策略：**opt-in**（2026-07-26 使用者定案，推翻原 fail closed）。
`PATENT_API_TOKEN` 未設或為空白時**放行**；設了才強制驗證（不符回 401）。

變更理由：前端 AI 助手不再要求使用者手填金鑰，但送出功能與 ai:narrative 任務要保留。
使用者在知悉風險後明示選擇不設保護。

⚠ **已知風險（使用者已確認接受）**：未設 token 時，任何取得服務網址的人都能呼叫
AI 任務端點——建立任務（消耗 Companion 端的 Claude 額度）、讀取他人任務結果。
服務若對公網可達（如 Lightning public URL），網址並非密碼，等同無保護。

原策略（fail closed，未設回 503）是為 Railway 公網部署而設。token 機制本身保留：
要重新開啟保護時，只需在部署環境設 `PATENT_API_TOKEN`，不需改回程式碼——
搬公司內網後若要恢復保護，補一行環境變數即可。
"""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException


TOKEN_ENV_VAR = "PATENT_API_TOKEN"


def _expected_token() -> str | None:
    """讀取設定的 API token；未設或全空白視同未設定。"""
    raw = os.getenv(TOKEN_ENV_VAR, "")
    token = raw.strip()
    return token or None


def require_api_token(authorization: str = Header(default="")) -> None:
    """設了 `PATENT_API_TOKEN` 就驗證 `Authorization: Bearer <token>`（不符回 401）；
    未設定則放行。

    以 FastAPI dependency 形式提供，掛法：
    `APIRouter(..., dependencies=[Depends(require_api_token)])` 或逐端點掛。

    ⚠ 未設定即放行是使用者定案的部署選擇（見模組 docstring），不是疏漏——
    修改此行為前請先確認部署環境與使用者意向，勿逕自改回 fail closed。
    """
    expected = _expected_token()
    if expected is None:
        # 未設定＝不啟用保護。端點對任何呼叫者開放，風險見模組 docstring。
        return

    provided = authorization.strip()
    # 固定 Bearer 前綴，不接受裸 token；比對用 compare_digest 避免時序側信道。
    if not provided.startswith("Bearer ") or not hmac.compare_digest(
        provided[len("Bearer ") :], expected
    ):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
