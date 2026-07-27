"""文獻備註改手動觸發（2026-07-27 使用者定）。

使用者原話：「文獻備註改成手動按鈕啟動，空值要寫，非空第二次按就不被寫入新的文獻備註」
＋「文獻備註啟動取消掉」（＝移除匯入後的自動觸發）。

## 為何改手動
實機踩到：AI 第一次跑失敗（CLI 回覆多一句開場白，見 9g），而**同一檔案再匯入會被
去重擋掉**（`inserted 0`），於是那批專利再也不會被自動觸發——**53 筆永遠缺備註、
沒有任何補救入口**。改手動鈕後可隨時重跑補齊。

## 只補空值
`skip_existing=True`（runner 既有預設）已排除主表已有備註者：
    AND (NOT %(skip_existing)s OR NULLIF(BTRIM(p."文獻備註"), '') IS NULL)
故「第二次按不覆蓋既有」本就成立——本測試把它鎖住，避免日後被改成覆蓋。

端點：POST /workspaces/{id}/patent-notes（全庫亦可，workspace_id 傳 None 代表全庫）。
"""
from __future__ import annotations

import ast
import unittest
import warnings
from pathlib import Path
from unittest import mock

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
    module=r"fastapi\.testclient",
)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.api import workspaces as workspaces_api  # noqa: E402
from backend.app.main import app  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HANDLERS = PROJECT_ROOT / "backend" / "app" / "worker" / "handlers.py"
client = TestClient(app)
WS = 940501


class PatentNoteAutoTriggerRemovedTests(unittest.TestCase):
    """匯入完成後不得再自動排 ai:patent_note。"""

    def test_import_does_not_enqueue_patent_note(self):
        """以 AST 檢查匯入完成處理函式的呼叫集合，不做字串比對（避免命中註解）。"""
        tree = ast.parse(HANDLERS.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            called = {
                c.func.id for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            }
            if "_enqueue_patent_note" in called:
                offenders.append(node.name)
        self.assertEqual(
            offenders, [],
            f"{offenders} 仍自動觸發 ai:patent_note；2026-07-27 已改為手動按鈕觸發"
            "——匯入去重會讓失敗批次永遠補不到備註")


class PatentNoteManualEndpointTests(unittest.TestCase):
    """POST /workspaces/{id}/patent-notes：手動觸發。"""

    def test_trigger_creates_job(self):
        """建 ai:patent_note job，帶 workspace_id 與 skip_existing=True。"""
        fake = mock.MagicMock(return_value=777)
        with mock.patch.object(workspaces_api, "create_job", fake):
            resp = client.post(f"/api/v1/workspaces/{WS}/patent-notes")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"workspace_id": WS, "job_id": 777})
        args, kwargs = fake.call_args
        self.assertEqual(args[0], "ai:patent_note")
        self.assertEqual(args[1]["workspace_id"], WS)
        self.assertIs(
            args[1].get("skip_existing"), True,
            "必須帶 skip_existing=True——第二次按不得覆蓋既有備註")

    def test_payload_never_forces_overwrite(self):
        """端點不提供覆蓋選項：已確認的備註不該被 AI 重寫。"""
        fake = mock.MagicMock(return_value=1)
        with mock.patch.object(workspaces_api, "create_job", fake):
            client.post(f"/api/v1/workspaces/{WS}/patent-notes")
        payload = fake.call_args[0][1]
        self.assertNotIn(
            False, [payload.get("skip_existing")],
            "payload 不得出現 skip_existing=False")


class SkipExistingContractTests(unittest.TestCase):
    """runner 的「只補空值」是預設行為，鎖住不被改掉。"""

    def test_skip_existing_defaults_true(self):
        import inspect

        from backend.app.worker import ai_patent_note_runner as r

        for fn_name in ("run_patent_note",):
            sig = inspect.signature(getattr(r, fn_name))
            self.assertIs(
                sig.parameters["skip_existing"].default, True,
                f"{fn_name} 的 skip_existing 預設應為 True（只補空值、不覆蓋）")

    def test_query_excludes_existing_notes(self):
        """SQL 條件必須把已有備註者排除在外。"""
        src = (PROJECT_ROOT / "backend" / "app" / "worker"
               / "ai_patent_note_runner.py").read_text(encoding="utf-8")
        self.assertIn(
            "NOT %(skip_existing)s OR NULLIF(BTRIM", src,
            "缺少『已有備註就跳過』的查詢條件")


if __name__ == "__main__":
    unittest.main()
