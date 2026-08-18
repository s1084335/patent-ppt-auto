"""正規化候選排隊、分批與重問規則（queue-normalization-candidates）。

## 根因

候選是**即時算出來的**，不是資料列——「被問過」無處可蓋章，所以查不到證據的
候選每跑一次就再燒一次。實測 #411 有 7 個沒結論，#416 就原封不動重問那 7 個，
花 263 秒換到 1 筆。

## 兩個子句各管一件事，缺一不可

| 子句 | 保證 |
|---|---|
| `WHERE (未問過 OR 件數變多)` | 誰**有資格**進隊列（使用者裁決「乙」） |
| `ORDER BY last_asked_at NULLS FIRST` | 誰**先**被問——沒問過的一律排前面 |

⚠ 只有 ORDER BY 沒有 WHERE ＝「延後重問」而不是「不重問」，輪完一圈後那批
自然人會再燒一次。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_GLOB = "0054_*.py"
_NODE_CANDIDATES = ("node", r"D:\vscode\node.js\node.exe")


def _find_node() -> str | None:
    for cand in _NODE_CANDIDATES:
        path = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if path:
            return path
    return None


def _migration() -> Path:
    hits = sorted((ROOT / "alembic" / "versions").glob(MIGRATION_GLOB))
    assert hits, "缺 0054 migration——沒有蓋章表，排隊條件無處可掛"
    return hits[0]


class MigrationContractTests(unittest.TestCase):
    """建表語意：鍵是自然鍵、件數與結果都不可空。"""

    def _sql(self, func_name: str) -> str:
        import importlib.util

        path = _migration()
        spec = importlib.util.spec_from_file_location("mig0054", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sqls: list[str] = []
        fake = mock.MagicMock()
        fake.execute.side_effect = lambda sql, *a, **k: sqls.append(str(sql))
        with mock.patch.object(module, "op", fake):
            getattr(module, func_name)()
        text = "\n".join(sqls)
        return "\n".join(line.split("--", 1)[0] for line in text.splitlines())

    def test_creates_asked_table_keyed_by_lookup_key(self):
        sql = self._sql("upgrade")
        self.assertRegex(sql, r"(?is)CREATE TABLE.*company_normalization_asked")
        self.assertRegex(
            sql, r"(?i)lookup_key\s+TEXT\s+PRIMARY KEY",
            "鍵必須是自然鍵 lookup_key——用 candidate_ref 等於把 ref 的算法複製進 SQL")

    def test_required_columns_are_not_null(self):
        sql = self._sql("upgrade")
        self.assertRegex(sql, r"(?i)asked_patent_count\s+INTEGER\s+NOT NULL",
                         "件數可空的話，重新入列的比較會變成三值邏輯而靜默失效")
        self.assertRegex(sql, r"(?i)outcome\s+TEXT\s+NOT NULL")

    def test_does_not_redefine_name_normalization(self):
        """⚠ 本表只存候選查詢產出的值；再寫一次正規化運算式就會有兩份定義。"""
        sql = self._sql("upgrade")
        self.assertNotIn("regexp_replace", sql,
                         "migration 自己算了一次 lookup_key——兩份定義會各自演進")

    def test_downgrade_drops_table(self):
        self.assertRegex(self._sql("downgrade"),
                         r"(?is)DROP TABLE.*company_normalization_asked")


class CandidateQueueSqlTests(unittest.TestCase):
    """排隊與資格條件寫在候選 SQL 裡；這裡驗語意而不連 DB。"""

    @classmethod
    def setUpClass(cls):
        from backend.app.derived import company_alias_importer as importer

        cls.sql = importer._COMPANY_NORMALIZATION_CANDIDATES_SQL

    def test_joins_asked_table(self):
        self.assertIn("company_normalization_asked", self.sql,
                      "候選查詢沒有接上蓋章表，等於沒有排隊")

    def test_never_asked_sorts_first(self):
        """🔴 排隊不變式：這一個子句就是「全部輪過一遍才會有人被問第二次」。"""
        self.assertRegex(
            self.sql, r"(?is)ORDER BY[^;]*last_asked_at\s+(ASC\s+)?NULLS FIRST",
            "沒有 NULLS FIRST——問過的可能排到沒問過的前面")

    def test_eligibility_requires_new_patents(self):
        """🔴 使用者裁決「乙」：件數沒變就不重問。"""
        norm = re.sub(r"\s+", " ", self.sql)
        self.assertRegex(
            norm, r"(?i)asked_patent_count",
            "資格條件沒有比較件數——會退化成「延後重問」而不是「不重問」")

    def test_still_excludes_confirmed_and_suggested(self):
        """既有兩個排除條件不得被改動搞丟。"""
        self.assertIn("confirmed", self.sql)
        self.assertIn("ai_suggested", self.sql)

    def test_candidates_expose_lookup_key_for_stamping(self):
        """蓋章要原樣寫回查詢產出的鍵，不得在 Python 再算一次。"""
        from backend.app.derived import company_alias_importer as importer
        import inspect

        src = inspect.getsource(importer.list_company_normalization_candidates)
        self.assertIn("lookup_key", src, "候選沒有帶出 lookup_key，蓋章時只能重算")


class PromptDoesNotLeakInternalKeyTests(unittest.TestCase):
    """內部識別鍵不得進入提示。"""

    def _prompt(self):
        from backend.app.worker import ai_company_normalization_suggestion_runner as runner

        candidates = [{
            "candidate_ref": "cand:abc123",
            "lookup_key": "secret internal key",
            "raw_name": "Acme Co., Ltd.",
            "candidate_type": "company_or_person",
            "source_fields": ["申請人"],
            "patent_count": 3,
        }]
        targets = [{"target_ref": "tgt:1", "zh_name": "宏碁",
                    "normalized_name": "Acer", "company_code": "UN1"}]
        return runner.build_prompt(candidates, targets)

    def test_field_name_absent(self):
        self.assertNotIn("lookup_key", self._prompt(),
                         "欄名外洩——AI 可能在輸出裡引用它，受控輸入的邊界就破了")

    def test_value_absent(self):
        """⚠ 只斷言欄名不夠：值照樣可能被 dump 進去。"""
        self.assertNotIn("secret internal key", self._prompt(),
                         "欄名藏了但值還在——投影沒做，只是改了 key")

    def test_public_fields_still_present(self):
        prompt = self._prompt()
        for keep in ("cand:abc123", "Acme Co., Ltd.", "patent_count"):
            self.assertIn(keep, prompt, f"投影把該留的 {keep} 也砍掉了")


class FrontendShowsRemainingTests(unittest.TestCase):
    """剩餘數與失敗段要真的畫得出來——node 實際執行，不是斷言字串存在。"""

    @classmethod
    def setUpClass(cls):
        cls.node = _find_node()
        cls.src = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")

    def _extract(self, name: str) -> str:
        start = self.src.index(f"function {name}(")
        depth, i, opened = 0, start, False
        while i < len(self.src):
            if self.src[i] == "{":
                depth += 1
                opened = True
            elif self.src[i] == "}":
                depth -= 1
                if opened and depth == 0:
                    return self.src[start:i + 1]
            i += 1
        raise AssertionError(f"找不到 {name} 的結尾")

    def _render(self, setup_js: str, fn_name: str) -> str:
        if self.node is None:
            self.skipTest("node 不在 PATH 也不在 D:/vscode/node.js")
        js = ("function escHtml(s){return String(s==null?'':s)"
              ".replace(/&/g,'&amp;').replace(/</g,'&lt;');}\n"
              + setup_js + "\n" + self._extract(fn_name) + "\n"
              f"process.stdout.write(String({fn_name}() || ''));")
        tmp = Path(__file__).parent / "_queue_render_check.js"
        try:
            tmp.write_text(js, encoding="utf-8")
            proc = subprocess.run([self.node, str(tmp)], capture_output=True,
                                  text=True, encoding="utf-8", timeout=60)
            self.assertEqual(proc.returncode, 0, f"執行失敗：\n{proc.stderr[:800]}")
            return proc.stdout
        finally:
            tmp.unlink(missing_ok=True)

    def test_renders_remaining_count(self):
        """🔴 分批若不揭露剩餘量，使用者會把「這批做完」讀成「全部做完」。"""
        html = self._render(
            "let companyNormalizationQueue = {remaining: 34, never_asked: 30, recheck: 4};",
            "companyNormalizationQueueHtml")
        self.assertIn("34", html, "沒有把剩餘數畫出來")
        self.assertIn("30", html, "沒有區分從未查證的數量")
        self.assertIn("4", html, "沒有區分待重查的數量")

    def test_renders_nothing_when_queue_empty(self):
        html = self._render(
            "let companyNormalizationQueue = {remaining: 0, never_asked: 0, recheck: 0};",
            "companyNormalizationQueueHtml")
        self.assertEqual(html.strip(), "")

    def test_renders_failed_chunks(self):
        html = self._render(
            "let companyNormalizationLastRun = {run_id: 9, skipped_invalid: 0,"
            " skipped_details: [], failed_chunks: [{index: 2, reason: 'unknown target_ref'}]};",
            "companyNormalizationFailedChunksHtml")
        self.assertIn("unknown target_ref", html, "失敗原因沒有顯示")


if __name__ == "__main__":
    unittest.main()
