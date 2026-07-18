"""schema_comments：dialect 中立內容 ＋ PG/SQL Server 雙 emitter 的單元測試。

驗證「同一份 COMMENTS 能發到兩種 DB」——這是可攜性（克服 SQL Server 相容）
的可驗收證據；不需連資料庫。
"""
from __future__ import annotations

import unittest

from backend.app.db import schema_comments as sc


class EmitParityTests(unittest.TestCase):
    """PG 與 MSSQL 兩軌對同一 dict 產出等量、格式正確的語句。"""

    def test_same_count_both_dialects(self):
        pg = sc.emit("postgresql")
        ms = sc.emit("mssql")
        self.assertEqual(len(pg), len(ms))
        # 語句數＝所有 __table__ 與欄位的總和
        expected = sum(len(cols) for cols in sc.COMMENTS.values())
        self.assertEqual(len(pg), expected)

    def test_pg_table_vs_column_vs_view(self):
        pg = "\n".join(sc.emit("postgresql"))
        self.assertIn("COMMENT ON TABLE app_layer.processing_jobs IS '", pg)
        self.assertIn('COMMENT ON COLUMN app_layer.processing_jobs."status" IS', pg)
        # view 必須用 COMMENT ON VIEW，不可用 TABLE
        self.assertIn("COMMENT ON VIEW derived_layer.v_unmapped_company_names IS", pg)
        self.assertNotIn("COMMENT ON TABLE derived_layer.v_unmapped_company_names", pg)

    def test_mssql_uses_extended_property_and_view_level(self):
        ms = "\n".join(sc.emit("mssql"))
        self.assertIn("sp_addextendedproperty", ms)
        self.assertIn("@name=N'MS_Description'", ms)
        self.assertIn("@level1type=N'VIEW', @level1name=N'v_unmapped_company_names'", ms)
        self.assertIn("@level2type=N'COLUMN'", ms)

    def test_chinese_column_names_present(self):
        pg = "\n".join(sc.emit("postgresql"))
        # 帶特殊字元的中文欄名要正確出現在 COMMENT ON COLUMN
        self.assertIn('"獨立項[KR,JP,US,CN,EP,IN]"', pg)
        self.assertIn('"申請人代碼"', pg)

    def test_single_quote_escaped_pg(self):
        stmt = sc._emit_pg("app_layer.x", "c", "it's a test")
        self.assertIn("it''s a test", stmt)

    def test_clear_matches_emit_count(self):
        self.assertEqual(len(sc.emit_clear("postgresql")), len(sc.emit("postgresql")))
        self.assertIn(" IS NULL", "\n".join(sc.emit_clear("postgresql")))
        self.assertIn("sp_dropextendedproperty", "\n".join(sc.emit_clear("mssql")))

    def test_unknown_dialect_raises(self):
        with self.assertRaises(ValueError):
            sc.emit("oracle")

    def test_include_filters_out_nonexistent_objects(self):
        """include 過濾器可略過不存在的物件（供 migration 只註解當下已存在的）。"""
        # 只允許 processing_jobs 這張表 → 其他表/欄全被略過
        only_pj = lambda q, c: q == "app_layer.processing_jobs"  # noqa: E731
        stmts = "\n".join(sc.emit("postgresql", include=only_pj))
        self.assertIn("app_layer.processing_jobs", stmts)
        self.assertNotIn("derived_layer.company_aliases", stmts)
        self.assertNotIn("core_layer.patents", stmts)
        # 全部略過 → 空清單
        self.assertEqual(sc.emit("postgresql", include=lambda q, c: False), [])
        # emit_clear 同樣受 include 約束、與 emit 條數一致
        self.assertEqual(
            len(sc.emit("postgresql", include=only_pj)),
            len(sc.emit_clear("postgresql", include=only_pj)),
        )

    def test_include_can_drop_specific_columns(self):
        """include 可用欄位層級過濾（table 註解保留、某欄略過）。"""
        drop_status = lambda q, c: c != "status"  # noqa: E731
        stmts = "\n".join(sc.emit("postgresql", include=drop_status))
        self.assertIn("COMMENT ON TABLE app_layer.processing_jobs", stmts)
        self.assertNotIn('processing_jobs."status"', stmts)

    def test_content_avoids_engine_mechanism_words(self):
        """註解內容應描述語意、不寫引擎機制字眼（確保 SQL Server 也精確）。"""
        banned = ("部分索引", "generated", "JSONB", "STORED", "CHECK 約束")
        for qualified, cols in sc.COMMENTS.items():
            for name, text in cols.items():
                for word in banned:
                    self.assertNotIn(
                        word, text, f"{qualified}.{name} 含機制字眼「{word}」：{text}"
                    )


if __name__ == "__main__":
    unittest.main()
