"""0049 SSE 事件補 metadata 的 migration 契約（complete-sse-data-refresh task 2.1）。

## 為什麼要這個 migration

`fd301dee99c3` 的 `notify_run_change` 只送 `{kind, run_id, status, progress, stage}`
——前端收到事件**無從判斷該刷新哪個資料區塊**（缺 run_type）也無從做 workspace
隔離（缺 workspace_id）、無從去重（缺 event_id）。design.md「事件契約」定稿：
終結事件另帶 `completed_at`。

## 契約要點

- `pg_notify` 於 COMMIT 遞送（PostgreSQL 語意）——「succeeded 只在 persistence
  成功後發布」由此天然成立，migration 不需也不得改動發布時機。
- 只 `CREATE OR REPLACE FUNCTION`，**不動 trigger**（`trg_workflow_runs_notify`
  仍指向同名函式，replace 即生效）。
- downgrade 必須把函式還原成 fd301 版（不帶新欄），不得 DROP 函式了事
  ——trigger 還掛著，DROP 會讓所有 workflow_runs UPDATE 直接炸。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / (
    "0049_sse_event_metadata.py")


class Migration0049ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert MIGRATION.exists(), f"缺 migration 檔：{MIGRATION.name}"
        cls.src = MIGRATION.read_text(encoding="utf-8")
        split = cls.src.index("def downgrade")
        cls.up = cls.src[:split]
        cls.down = cls.src[split:]

    def test_chain_links_to_0048(self):
        self.assertRegex(
            self.src, r'down_revision\s*=\s*["\']0048_topic_assignment_source["\']')

    def test_upgrade_adds_event_metadata_keys(self):
        """事件 payload 必含 run_type／workspace_id／event_id——前端 mapping 的輸入。"""
        for key in ("'run_type'", "'workspace_id'", "'event_id'"):
            self.assertIn(key, self.up, f"upgrade 的 json_build_object 缺 {key}")

    def test_upgrade_adds_completed_at_for_terminal_only(self):
        """completed_at 僅終結狀態帶——進度事件帶完成時間是語意錯誤。"""
        self.assertIn("'completed_at'", self.up)
        self.assertRegex(
            self.up, r"succeeded.*failed.*cancelled|IN \('succeeded'",
            "completed_at 需以終結狀態集合守門")

    def test_upgrade_replaces_function_without_touching_trigger(self):
        self.assertIn("CREATE OR REPLACE FUNCTION app_layer.notify_run_change", self.up)
        self.assertNotIn("CREATE TRIGGER", self.up,
                         "不得重建 trigger——replace function 即生效")
        self.assertNotIn("DROP TRIGGER", self.up)

    def test_downgrade_restores_fd301_payload(self):
        """downgrade＝還原 fd301 函式本體；DROP 函式會讓掛著的 trigger 炸掉。"""
        self.assertIn("CREATE OR REPLACE FUNCTION app_layer.notify_run_change", self.down)
        self.assertNotIn("DROP FUNCTION", self.down)
        for key in ("'run_type'", "'event_id'", "'completed_at'"):
            self.assertNotIn(key, self.down, f"downgrade 不得殘留新欄 {key}")

    def test_event_id_is_run_and_status(self):
        """event_id＝run_id:status（終結事件去重鍵，design.md 定稿）。"""
        self.assertRegex(
            self.up.replace(" ", ""),
            r"NEW\.run_id\|\|':'\|\|NEW\.status",
            "event_id 應由 run_id 與 status 組成")


if __name__ == "__main__":
    unittest.main()
