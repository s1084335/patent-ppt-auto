"""ai:irrelevant_filter 改手動觸發契約測試（2026-07-27 使用者定案）。

驗證：
1. clustering finalize 完成後**不再**自動 enqueue ai:irrelevant_filter
   （2026-07-24 的自動接續定案於 2026-07-27 撤回，改由使用者按鈕觸發）。
2. finalize 仍自動 enqueue ai:topic_label（主題命名保持自動，未一併撤回）。

為何改手動：AI 判讀只是輔助，使用者要在看過分群結果後自行決定何時篩選；
自動觸發會在每次 finalize 後無條件耗用一次 Claude CLI 額度，且判讀結果需人工
逐筆裁決（保留／確定），沒人看時排了也沒用。
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HANDLERS = PROJECT_ROOT / "backend" / "app" / "worker" / "handlers.py"

sys.path.insert(0, str(PROJECT_ROOT))


class IrrelevantFilterManualOnlyTests(unittest.TestCase):
    def test_finalize_does_not_call_irrelevant_filter_enqueue(self):
        """handle_clustering_finalize 的函式體不得呼叫 _enqueue_irrelevant_filter。

        以 AST 解析函式體內的呼叫名稱，不做字串比對——避免命中註解或說明文字。
        """
        tree = ast.parse(HANDLERS.read_text(encoding="utf-8"))
        target = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "handle_clustering_finalize"
        )
        called = {
            node.func.id
            for node in ast.walk(target)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn(
            "_enqueue_irrelevant_filter", called,
            "finalize 仍自動觸發 ai:irrelevant_filter；2026-07-27 已改為手動按鈕觸發")
        self.assertIn(
            "_enqueue_topic_label", called,
            "finalize 應仍自動觸發 ai:topic_label（主題命名未改手動）")

    def test_finalize_enqueues_only_topic_label(self):
        """實跑 handle_clustering_finalize：只應建立 ai:topic_label 一筆 job。"""
        from backend.app.worker import handlers

        summary = {
            "run_id": 7,
            "workspace_id": 3,
            "source_field": "wips_independent_claims",
            "topics": [{"topic_code": "T001"}],
        }
        created: list[str] = []

        class _FakeContext:
            def heartbeat(self, *args, **kwargs):
                return None

            def keepalive(self, *args, **kwargs):
                class _Nop:
                    def __enter__(self_inner):
                        return None

                    def __exit__(self_inner, *exc):
                        return False

                return _Nop()

        def _fake_create_job(job_type, payload, **kwargs):
            created.append(job_type)
            return 1

        with patch.object(handlers, "finalize_top_level", return_value=summary), \
                patch("backend.app.db.job_repository.create_job", side_effect=_fake_create_job):
            handlers.handle_clustering_finalize(
                {"run_id": 7, "candidate_id": 1}, _FakeContext())

        self.assertNotIn("ai:irrelevant_filter", created)
        self.assertEqual(created, ["ai:topic_label"])


if __name__ == "__main__":
    unittest.main()
