"""補分候選判定（CLU-013）＋來源標記 migration（設計見 openspec change
add-technical-channel-ai-backfill；2026-08-07 使用者確認規格後動工）。

- 候選＝該通道無 embeddings（不在分群輸入母體）∧ 非設計案。
- 設計案判定唯一定義處＝transforms/patent_kind.is_design（document_kind='S'），
  MUST NOT 用 patent_type（P 28 件含全部 11 件設計案，用它會錯殺）。
- topic_assignments 加 assigned_source（NOT NULL DEFAULT 'geometric'）。
"""
from __future__ import annotations

import unittest


class CandidateRuleTests(unittest.TestCase):
    ROWS = [
        # B 組：無來源欄值、非設計 → 候選
        {"patent_id": 1, "source_text": None, "document_kind": "M"},
        {"patent_id": 2, "source_text": "  ", "document_kind": "A"},
        # 設計案：無值也不補
        {"patent_id": 3, "source_text": None, "document_kind": "S"},
        # 有來源欄值（已在分群母體）→ 不是候選
        {"patent_id": 4, "source_text": "An exercise apparatus ...", "document_kind": "M"},
        # patent_type 陷阱列：P 但 document_kind 非 S → 仍是候選
        {"patent_id": 5, "source_text": None, "document_kind": "M", "patent_type": "P"},
    ]

    def test_candidate_selection(self):
        from backend.app.clustering.backfill import backfill_candidates

        ids = [r["patent_id"] for r in backfill_candidates(self.ROWS)]
        self.assertEqual(ids, [1, 2, 5])

    def test_design_exclusion_uses_single_definition(self):
        """判定必須呼叫 transforms/patent_kind.is_design，不得另寫條件。"""
        import inspect

        from backend.app.clustering import backfill

        src = inspect.getsource(backfill)
        self.assertIn("is_design", src)
        self.assertNotIn("patent_type", src)

    def test_already_assigned_excluded(self):
        """已核准（已有該通道 current assignment）者不再列候選。"""
        from backend.app.clustering.backfill import backfill_candidates

        ids = [r["patent_id"] for r in backfill_candidates(
            self.ROWS, assigned_patent_ids={1})]
        self.assertEqual(ids, [2, 5])


class AssignedSourceMigrationTests(unittest.TestCase):
    def _module(self):
        import importlib.util
        from pathlib import Path

        path = Path("alembic/versions/0048_topic_assignment_source.py")
        self.assertTrue(path.exists(), "缺 migration 0048")
        spec = importlib.util.spec_from_file_location("m0048", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_upgrade_adds_assigned_source_with_default(self):
        import inspect

        mod = self._module()
        src = inspect.getsource(mod.upgrade)
        self.assertIn("assigned_source", src)
        self.assertIn("'geometric'", src)
        self.assertIn("nullable=False", src)

    def test_downgrade_drops_column(self):
        import inspect

        mod = self._module()
        src = inspect.getsource(mod.downgrade)
        self.assertIn("drop_column", src)
        self.assertIn("assigned_source", src)


if __name__ == "__main__":
    unittest.main()
