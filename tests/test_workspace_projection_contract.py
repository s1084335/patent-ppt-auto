"""workspace 投影欄位契約（純字串檢查，不需 DB）。

與 `test_workspace_queries.py` 分檔的理由：後者 `setUpModule` 需要本機 admin DB
建拋棄式庫，DB 不可達時整組 skip；但投影契約是「SQL 有沒有帶這欄」的靜態檢查，
不該被 DB 可用性擋掉——這正是本次 bug 沒被既有測試攔下的原因。
"""
from __future__ import annotations

import unittest

from backend.app.app_layer import workspace_queries


class WorkspaceProjectionContractTests(unittest.TestCase):
    """前端過濾全庫所依賴的欄位必須被投影出來。

    動因（2026-07-26 實測）：匯入後 Workspace 下拉出現兩個全庫。前端
    `loadWorkspaces` 以 `all.filter(w => !w.is_global)` 濾掉全庫（另以哨兵置頂），
    但 `_WORKSPACE_FIELDS` 未投影 `is_global`，API 回應無此鍵 → 前端讀到 undefined
    → `!undefined` 為 true → 全庫通過過濾，被當成一般 workspace 再列一次。
    """

    def test_projection_includes_is_global(self):
        """is_global 必須在投影中：前端據此把全庫排除出一般清單。"""
        self.assertIn("is_global", workspace_queries._WORKSPACE_FIELDS)

    def test_projection_includes_frontend_required_fields(self):
        """前端下拉渲染實際會讀的欄位都要在投影裡，缺一個就顯示異常。"""
        for field in ("workspace_id", "workspace_name", "patent_count", "is_composed"):
            self.assertIn(field, workspace_queries._WORKSPACE_FIELDS, f"投影缺 {field}")

    def test_list_and_detail_share_projection(self):
        """list 與 detail 共用同一組投影，避免兩端結構分歧。"""
        self.assertIn(workspace_queries._WORKSPACE_FIELDS, workspace_queries._LIST_SQL)
        self.assertIn(workspace_queries._WORKSPACE_FIELDS, workspace_queries._DETAIL_SQL)


if __name__ == "__main__":
    unittest.main()
