"""整批專利的範圍描述——PRE-008 的判讀依據。

## 🔴 為什麼需要這一句話

PRE-008 要 AI 判斷「這件命中的專利，跟整批專利的範圍有沒有關係」。
但 workspace 只有名稱（`description` 欄在 0021 已移除），而
**「自走式割草機」五個字，AI 判斷不了「刀片結構算不算範圍內」**。

考慮過的替代來源與否決理由：
- workspace 名稱 → 太短，如上。
- 從母體專利反推（IPC 分布、常見詞） → 那是我自己想出來的東西，
  使用者要的是「用標題／摘要／獨立項比對範圍」，不是統計對照。
- 分群結果 → 🔴 初階篩選發生在**分群之前**，取不到（PRE-008 明文禁止依賴）。

⇒ 使用者 2026-08-21 裁決：**由使用者填一句**。負面關鍵字本來就是使用者
「知道自己不要什麼」的表達，範圍描述是同一件事的正面版本。

## 落點與長度上限

落 `app_layer.workspaces.settings_json`，**不需 migration**。

⚠ 0024 否決 `workflow_runs.request_json`、0027 否決把 PDF 塞 settings_json、
0035 否決把排除清單塞 settings_json，三者同一條判準：
**熱路徑欄位不放不定量資料**（settings_json 每次查 workspace 都整包拉回）。

一句話是定量小資料，不違反該判準——但只有在**長度真的有界**時才成立。
故 `MAX_SCOPE_LENGTH` 是護欄不是格式建議：沒有它，「一句話」會變成
貼一整份說明書，這欄就從定量變不定量，正好踩進被否決過的那條。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from backend.app.clustering.exclusions import _conn_ctx, is_global_workspace

#: settings_json 裡的鍵名。
SCOPE_KEY = "prefilter_scope"

#: 範圍描述長度上限。⚠ 見模組 docstring：這是熱路徑欄位的護欄。
#: 500 字約合三、四句中文，足夠描述一個技術範圍，遠短於任何說明書段落。
MAX_SCOPE_LENGTH = 500


def get_scope_description(workspace_id: int, *,
                          conn: Any | None = None) -> str:
    """回傳範圍描述；未設定回空字串。

    ⚠ 回 `""` 不回 `None`：呼叫端只要判真假即可，不必各自處理兩種「沒有」。
    """
    with _conn_ctx(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT settings_json ->> %s FROM app_layer.workspaces "
                "WHERE workspace_id = %s", (SCOPE_KEY, workspace_id))
            row = cur.fetchone()
    if not row:
        return ""
    return (row[0] or "").strip()


def set_scope_description(workspace_id: int, text: str, *,
                          conn: Any | None = None) -> str:
    """寫入範圍描述，回傳正規化後的值。空字串＝清除。

    Raises:
        ValueError: 全庫 workspace，或超過 `MAX_SCOPE_LENGTH`。
    """
    value = (text or "").strip()
    if len(value) > MAX_SCOPE_LENGTH:
        raise ValueError(
            f"範圍描述最長 {MAX_SCOPE_LENGTH} 字，收到 {len(value)} 字")

    with _conn_ctx(conn) as c:
        if is_global_workspace(workspace_id, conn=c):
            raise ValueError("全庫 workspace 不適用初階篩選")
        with c.cursor() as cur:
            # 🔴 合併寫入（`||`）不是整包覆蓋：`SET settings_json = %s` 會把
            # 別人存在裡面的鍵**靜默清掉**——不報錯，要等那個功能壞掉才發現。
            cur.execute(
                "UPDATE app_layer.workspaces "
                "SET settings_json = COALESCE(settings_json, '{}'::jsonb) || %s "
                "WHERE workspace_id = %s",
                (Jsonb({SCOPE_KEY: value}), workspace_id))
    return value
