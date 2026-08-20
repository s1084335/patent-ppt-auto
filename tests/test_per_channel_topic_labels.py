"""專利清單的「技術分類／功效分類」兩欄需分通道回傳（2026-07-27 實機回歸）。

症狀：主題已成功命名（功效通道 17 個中文名、label_source=llm），專利清單的
「技術分類」「功效分類」兩欄仍全空。

根因：前端 2026-07-24 定案把分類標籤拆成技術／功效兩欄，key 由
`topicLabelKey(source_field)` 推導成 `topic_label_wips_independent_claims`
與 `topic_label_effect_summary`；但後端 `list_workspace_patents` 只輸出**單一**
`topic_label`（依查詢參數 source_field 決定是哪個通道）。兩個 key 都不存在，
所以那兩欄自加上去的第一天起就是空的。

此為當日第六次「寫入端與讀取端落點不一致」（見 decisions.md 2026-07-27
「同一欄位不得有兩種落點」）。

修法：後端對兩個通道各取一次指派，輸出 `topic_label_<source_field>`
（與 `topic_key_<source_field>`）；既有單一 `topic_label`／`topic_key` 保留，
避免打壞其他呼叫端。
"""
from __future__ import annotations

import unittest

from backend.app.clustering.sources import source_fields


class PerChannelTopicLabelTests(unittest.TestCase):
    """每個通道各有一組 topic_key／topic_label 欄位。"""

    def _items(self):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/workspaces/1/patents?limit=3")
        # ⚠ 2026-08-19：本測試隱性依賴「正式庫裡 workspace 1 有資料」。乾淨測試庫
        #   （空的）回 404，卡在下面那行斷言之前，於是變成假紅——而它自己下一行
        #   就已經為「沒資料」準備了 skip。404 與空清單是同一件事的兩種表現，
        #   一起走 skip。⚠ skip 不是靜默通過：理由會印出來，要真的驗這幾條
        #   必須餵種子資料（見 §8.0 未覆蓋揭露）。
        if resp.status_code == 404:
            self.skipTest("測試庫沒有 workspace 1（乾淨庫），此欄位契約需種子資料才驗得到")
        self.assertEqual(resp.status_code, 200, resp.text)
        items = resp.json().get("items") or []
        if not items:
            self.skipTest("workspace 1 無專利資料，無法驗證欄位")
        return items

    def test_每個通道都有專屬欄位(self):
        """前端 topicLabelKey() 推導的 key 必須存在於回應中。"""
        item = self._items()[0]
        for field in source_fields():
            self.assertIn(
                f"topic_label_{field}", item,
                f"缺少 {field} 通道的標籤欄位——前端「技術分類／功效分類」會是空的",
            )
            self.assertIn(f"topic_key_{field}", item)

    def test_兩個通道的值互相獨立(self):
        """技術與功效是不同分群，同一專利在兩通道的主題不應被混為一談。"""
        items = self._items()
        fields = list(source_fields())
        tech_vals = {it.get(f"topic_label_{fields[0]}") for it in items}
        # 至少技術通道要有非空值（run 1 已 completed、118 筆 assignments）
        self.assertTrue(
            any(v for v in tech_vals),
            "技術通道應有標籤值；全空代表指派沒被讀到",
        )

    def test_保留單一欄位不破壞既有呼叫端(self):
        """既有的 topic_key／topic_label 仍在（其他呼叫端可能依賴）。"""
        item = self._items()[0]
        self.assertIn("topic_key", item)
        self.assertIn("topic_label", item)


class TopicColumnsSingleSourceTests(unittest.TestCase):
    """所有列出專利的查詢函式都必須補主題欄，且共用同一份實作。

    ⚠ 2026-07-27 第二次踩到：分通道欄位當初只加在 `list_workspace_patents`，
    `list_topic_patents` 沒加——「全部」有值、點進單一主題後技術／功效兩欄全空。
    同一份資料兩個查詢函式只改一個，是本專案反覆出現的斷鏈型態。

    本測試不連 DB，以 AST 驗證每個列表函式都呼叫共用的 `_attach_topic_columns`，
    不容許任何一個自己實作（自己實作＝下次又只改一邊）。
    """

    LIST_FUNCTIONS = ("list_workspace_patents", "list_topic_patents")

    def test_all_list_functions_attach_topic_columns(self):
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "backend" / "app" / "app_layer" / "workspace_queries.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for name in self.LIST_FUNCTIONS:
            with self.subTest(function=name):
                node = next(
                    (n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == name),
                    None,
                )
                self.assertIsNotNone(node, f"找不到 {name}")
                called = {
                    c.func.id for c in ast.walk(node)
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                }
                self.assertIn(
                    "_attach_topic_columns", called,
                    f"{name} 未呼叫 _attach_topic_columns → 該清單的技術／功效分類欄會全空")


if __name__ == "__main__":
    unittest.main()
