"""預設 search_path 必須含 extensions（2026-07-27 實機：embeddings 全掛）。

實機症狀：新 workspace 匯入後按「分類」→ 兩通道都失敗
`ValueError: no patents with reusable embeddings were found for this scope`。
往上追是 `embeddings #4` 先掛：`UndefinedObject: type "vector" does not exist`。

根因：pgvector 在 Supabase 裝在 **extensions** schema。DB 端的預設 search_path
本來就含它（`"$user", public, extensions`），但 `get_connection_kwargs()` 的
`options` 預設值是 `-c search_path=core_layer,raw_layer,public`——**會覆蓋掉
DB 端的正確預設**，把 extensions 拿掉，於是 `vector` 型別找不到。

也就是「沒設 PGOPTIONS」反而比「不設 options」更糟：不是沿用 DB 預設，
而是主動改成一個缺 extensions 的值。容器啟動指令一旦漏帶 PGOPTIONS，
embeddings → 分群整條線就全掛，而錯誤訊息（no reusable embeddings）
指向的是下游，很難聯想到 search_path。

修法：預設值補 extensions，讓「忘了設環境變數」不再等於壞掉。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock


class SearchPathDefaultTests(unittest.TestCase):
    """不設 PGOPTIONS 時，預設 search_path 仍要含 extensions。"""

    def _options(self, env: dict) -> str:
        from backend.app.db.connection import get_connection_kwargs

        with mock.patch.dict(os.environ, env, clear=True):
            return str(get_connection_kwargs().get("options", ""))

    def test_default_includes_extensions_with_database_url(self):
        """走 DATABASE_URL 分支（容器部署用的路徑）。"""
        opts = self._options({"DATABASE_URL": "postgresql://u@h:5432/db"})
        self.assertIn(
            "extensions", opts,
            "預設 search_path 缺 extensions → pgvector 的 vector 型別找不到，"
            "embeddings 全掛（實機 job 4）")

    def test_default_includes_extensions_with_pg_vars(self):
        """走 PGHOST/PGDATABASE 分支（本機開發用的路徑）。"""
        opts = self._options({"PGHOST": "127.0.0.1", "PGDATABASE": "x"})
        self.assertIn("extensions", opts)

    def test_keeps_existing_schemas(self):
        """既有的三個 schema 不得被拿掉——它們是資料表所在。"""
        opts = self._options({"DATABASE_URL": "postgresql://u@h:5432/db"})
        for schema in ("core_layer", "raw_layer", "public"):
            with self.subTest(schema=schema):
                self.assertIn(schema, opts)

    def test_explicit_pgoptions_still_wins(self):
        """明確指定 PGOPTIONS 時以它為準（部署可覆寫）。"""
        opts = self._options({
            "DATABASE_URL": "postgresql://u@h:5432/db",
            "PGOPTIONS": "-c search_path=only_this",
        })
        self.assertEqual(opts, "-c search_path=only_this")


if __name__ == "__main__":
    unittest.main()
