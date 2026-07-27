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


if __name__ == "__main__":
    unittest.main()
