"""FastAPI 端點的共用 bearer token 依賴（沿用 MCP `_auth.BearerTokenMiddleware` 模式）。

與 MCP 側的差別只在載體：MCP 是整個 http app 外包一層 ASGI middleware，
這裡是 FastAPI dependency，可逐 router／逐端點掛，方便日後擴及其他端點
而不必一次改動全部前端呼叫。token 比對規則與 MCP 側一致。

未設定 token 的策略：**fail closed**。
`PATENT_API_TOKEN` 未設或為空白時，受保護端點一律回 503 並在訊息中指名要設哪個
環境變數，而不是「未設就放行」。理由：本服務已部署到 Railway 公網可達，
「未設即放行」等於任何一次漏設環境變數就讓 AI 任務端點裸奔（任何人可建任務、
讀他人結果）；相對地本機開發只要在 `.env` 補一行就能用，成本遠低於公網裸奔的風險。
回 503（而非 401）是為了區分「伺服器沒配置好」與「你的 token 不對」，
讓部署者一眼看出是自己少設環境變數。
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
    """驗證 `Authorization: Bearer <PATENT_API_TOKEN>`；不符回 401、未設定回 503。

    以 FastAPI dependency 形式提供，掛法：
    `APIRouter(..., dependencies=[Depends(require_api_token)])` 或逐端點掛。
    """
    expected = _expected_token()
    if expected is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{TOKEN_ENV_VAR} is not configured; "
                "set it on the server before calling protected endpoints"
            ),
        )

    provided = authorization.strip()
    # 固定 Bearer 前綴，不接受裸 token；比對用 compare_digest 避免時序側信道。
    if not provided.startswith("Bearer ") or not hmac.compare_digest(
        provided[len("Bearer ") :], expected
    ):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
