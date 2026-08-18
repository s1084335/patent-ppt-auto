"""第二批不得覆蓋第一批未確認的建議（2026-08-18 使用者要求）。

分批之後多了一個以前不存在的狀況：**同時有多批的待審建議並存**。
第一批產出後使用者還沒確認，就跑了第二批——第二批若把待審區當成「本次結果」
整個重寫，第一批的查證成果（連同已經花掉的 CLI 時間）會靜默消失。

## 兩道防線，都要鎖住

1. 寫入時的 DELETE 必須同時限定**代碼與別稱**，只替換「同一個候選的舊建議」。
   ⚠ 只用代碼會把同一家公司底下其他候選的待審建議一起刪掉——而多個候選
   指向同一個既有公司正是 `map_existing` 的常態。
2. 候選查詢排除已有待審建議者，所以第一批的候選根本不會出現在第二批。
"""
from __future__ import annotations

import inspect
import re
import unittest


class IngestDeleteIsScopedToOneAliasTests(unittest.TestCase):
    """防線一：替換的粒度是「候選」，不是「公司」。"""

    @classmethod
    def setUpClass(cls):
        from backend.app.derived import company_alias_importer as importer

        cls.src = inspect.getsource(
            importer.ingest_company_normalization_suggestions)

    def _delete_statement(self) -> str:
        match = re.search(r"DELETE FROM derived_layer\.company_aliases(.*?)\"\"\"|"
                          r"'DELETE FROM derived_layer\.company_aliases '(.*?)\)",
                          self.src, re.S)
        self.assertIsNotNone(match, "找不到寫入前的 DELETE")
        return match.group(0)

    def test_delete_is_narrowed_by_alias_not_only_code(self):
        """🔴 核心：DELETE 必須帶 alias_lookup_key。"""
        stmt = self._delete_statement()
        self.assertIn("alias_lookup_key", stmt,
                      "DELETE 只用代碼——會刪掉同一家公司底下其他候選的待審建議")
        self.assertIn("申請人代碼", stmt, "DELETE 未限定代碼")

    def test_delete_is_narrowed_by_source_file(self):
        """⚠ 不得刪到別條 AI 線（例如中文名草稿）寫的 ai_suggested 列。"""
        stmt = self._delete_statement()
        self.assertIn("source_file", stmt,
                      "DELETE 沒有限定 source_file——會刪到其他 AI 線的草稿")


class PendingCandidatesLeaveThePoolTests(unittest.TestCase):
    """防線二：已有待審建議的候選不會被第二批重新查證。"""

    def test_candidate_sql_excludes_pending_suggestions(self):
        from backend.app.derived import company_alias_importer as importer

        sql = re.sub(r"\s+", " ", importer._COMPANY_NORMALIZATION_CANDIDATES_SQL)
        self.assertRegex(
            sql,
            r"(?i)NOT EXISTS[^)]*review_status = 'ai_suggested'",
            "候選查詢沒有排除待審建議——第二批會重查第一批已經有結果的候選，"
            "且寫入時會覆蓋掉那筆待審")

    def test_pending_exclusion_is_scoped_to_this_ai_line(self):
        """排除條件本身要限定 source_file，否則別條線的草稿會誤擋候選。"""
        from backend.app.derived import company_alias_importer as importer

        sql = re.sub(r"\s+", " ", importer._COMPANY_NORMALIZATION_CANDIDATES_SQL)
        self.assertIn("ai:company_normalization_suggestion", sql)


class OtherAiLinesDoNotWipePendingSuggestionsTests(unittest.TestCase):
    """防線三：別條 AI 線清自己的草稿時，不得掃掉正規化的待審建議。

    ⚠ 這是同一個「靜默覆蓋」問題的另一道門，而且比批次之間更嚴重：
    `company_aliases` 裡所有 AI 草稿共用 `review_status='ai_suggested'`，
    只用代碼刪就會跨線刪。具體情境——正規化建議「Acme Trading Ltd → UN164421」
    還沒確認，使用者去確認了 UN164421 的**中文名草稿**，那筆正規化建議就沒了，
    而且沒有任何訊息。分批之後待審建議停留的時間更長，撞上的機率更高。
    """

    def test_zh_name_confirm_clears_only_its_own_drafts(self):
        import inspect

        from backend.app.api import company_aliases as api

        src = inspect.getsource(api._clear_drafts)
        self.assertIn("source_file", src,
                      "中文名確認清草稿時沒有限定 source_file——"
                      "會連同該代碼的正規化待審建議一起刪掉")

    def test_zh_name_runner_clears_only_its_own_drafts(self):
        import inspect

        from backend.app.worker import ai_company_zh_name_runner as zh

        src = inspect.getsource(zh)
        # 抓整段 _DELETE_DRAFT_SQL 賦值，不要在中途停——條件寫在哪一行不該影響判定
        match = re.search(r"_DELETE_DRAFT_SQL\s*=\s*\((.*?)\n    \)", src, re.S)
        self.assertIsNotNone(match, "找不到中文名線的草稿 DELETE")
        stmt = match.group(1)
        self.assertIn("DELETE FROM derived_layer.company_aliases", stmt)
        self.assertIn("source_file", stmt,
                      "中文名線重跑時會刪掉該代碼的正規化待審建議")


class SecondBatchKeepsFirstBatchTests(unittest.TestCase):
    """行為層：跑第二批之後，第一批寫進去的建議仍在。"""

    def test_second_run_does_not_delete_other_candidates_rows(self):
        import json

        from tests.test_ai_company_normalization_suggestion import (
            FakeStore, _candidate, _target, _valid_result,
        )
        from backend.app.worker import (
            ai_company_normalization_suggestion_runner as runner,
        )

        class KeepingStore(FakeStore):
            """把 ingest 當成真的表：以 (code, lookup) 為鍵替換，其餘保留。"""

            def __init__(self, candidates, targets):
                super().__init__(candidates, targets)
                self.table: dict[tuple, dict] = {}

            def mark_asked(self, entries):
                return {"stamped": len(entries)}

            def ingest_suggestions(self, suggestions):
                for s in suggestions:
                    for raw in s.get("raw_names") or []:
                        self.table[(s["company_code"], raw)] = s
                return {"inserted": len(suggestions)}

        def reply(refs):
            payload = json.loads(_valid_result())
            payload["suggestions"][0]["candidate_refs"] = refs
            return json.dumps(payload, ensure_ascii=False)

        first = _candidate(ref="cand:first", raw_name="First Batch Co.")
        first["lookup_key"] = "first batch co."
        first["patent_count"] = 3
        store = KeepingStore([first], [_target()])
        runner.run_company_normalization_suggestions(
            store=store, cli_runner=lambda *_a, **_kw: reply(["cand:first"]))
        after_first = dict(store.table)
        self.assertTrue(after_first, "第一批沒有寫進任何建議，這個測試就沒有意義")

        second = _candidate(ref="cand:second", raw_name="Second Batch Co.")
        second["lookup_key"] = "second batch co."
        second["patent_count"] = 2
        store.candidates = [second]
        runner.run_company_normalization_suggestions(
            store=store, cli_runner=lambda *_a, **_kw: reply(["cand:second"]))

        for key, value in after_first.items():
            self.assertIn(key, store.table,
                          f"第一批的建議 {key} 被第二批覆蓋掉了")
        self.assertGreater(len(store.table), len(after_first),
                           "第二批沒有新增建議")


if __name__ == "__main__":
    unittest.main()
