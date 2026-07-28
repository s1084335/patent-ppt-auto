"""`公司名稱` 舊欄整條移除，全線收斂四欄（2026-07-28 使用者定案）。

## 使用者原話

- 「資料庫欄位要改成目標的，依序就是 申請人代碼、公司中文名稱、正規化名稱、別稱，就這些」
- 「沒有公司名稱，我沒有排這一欄吧?」
- 「這輪一起移除」
- 「對照檔也改四欄」
- 「優先順序是顯示中文、沒中文才正規化，沒正規化才原值」

## 為何非移不可

0040 拆四欄時保留 `公司名稱` 供對照，結果它同時是**寫入端**（主路徑仍同步寫）
與**讀取端 fallback**（5 處），等於同一語意存在兩個落點——本日第 18 次同型結構。
留著不寫入也不行：schema、查詢、COALESCE 裡都還看得到它，下一個人會再填回去。

## 目標結構（就這四欄）

| 欄 | 語意 |
|---|---|
| 申請人代碼   | WIPS 查來的真代碼（英文兩碼＋數字），歸戶依據 |
| 公司中文名稱 | 中文正式名 |
| 正規化名稱   | **英文正式名** |
| 別稱         | 各種雜亂寫法，一列一個 |

## 顯示順位（使用者定，本檔鎖住）

**中文 → 正規化 → 原值**。原值＝專利本身的 `標準化申請人`／`申請人` 等原始欄，
不是舊的 `公司名稱`。

## 對照檔（xlsx/csv）同步改四欄

`REQUIRED_COLUMNS` 是**外部檔表頭**不是 DB 欄，本輪一併改成四欄口徑，
中文與英文分兩格填，匯入不再靠字元類別猜（與 0040 第②點同一理由）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 掃描範圍：所有會碰 company_aliases 的模組。
# ⚠ 不含 alembic/versions —— migration 必須提到舊欄名才能 drop 它。
TARGET_FILES = (
    "backend/app/derived/company_alias_importer.py",
    "backend/app/derived/refresh_report_patent_base.py",
    "backend/app/api/company_aliases.py",
    "backend/app/db/schema_comments.py",
    "backend/app/worker/ai_company_zh_name_runner.py",
)

# 舊欄名，但要排除「公司中文名稱」這個包含它的新欄名。
_LEGACY_RE = re.compile(r'公司名稱(?!)')


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """回傳「非註解、非 docstring」的行，避免說明文字造成假失敗。

    ⚠ 本檔前身的教訓：直接 grep 檔案會被沿革註解餵飽——移除舊欄後註解裡
    仍會提到它（說明為何移除），那是應該存在的。只掃真正的程式碼。
    """
    import ast

    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 收集所有 docstring 與純字串 expression 的行號區間
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            doc_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in doc_lines:
            continue
        code = line.split("#", 1)[0]  # 去行內註解
        if code.strip():
            out.append((i, code))
    return out


class LegacyColumnGoneTests(unittest.TestCase):
    """`公司名稱` 不得再出現在任何程式碼（註解除外）。"""

    def test_no_legacy_column_in_code(self):
        for rel in TARGET_FILES:
            path = PROJECT_ROOT / rel
            if not path.exists():
                continue
            hits = [
                (i, code.strip())
                for i, code in _code_lines(path)
                # 排除新欄名「公司中文名稱」——它字面上含「公司」與「名稱」但不含
                # 連續的「公司名稱」；用 replace 先挖掉新欄名再找舊欄名最穩。
                if "公司名稱" in code.replace("公司中文名稱", "")
            ]
            with self.subTest(file=rel):
                self.assertEqual(hits, [], f"{rel} 仍有舊欄 `公司名稱`：{hits}")


class RequiredColumnsTests(unittest.TestCase):
    """對照檔表頭同步改四欄（使用者：「對照檔也改四欄」）。"""

    def test_required_columns_are_four(self):
        from backend.app.derived.company_alias_importer import REQUIRED_COLUMNS

        self.assertEqual(
            tuple(REQUIRED_COLUMNS),
            ("申請人代碼", "公司中文名稱", "正規化名稱", "別稱"),
            "對照檔表頭要與 DB 四欄一致（順序照使用者指定）",
        )

    def test_import_maps_both_name_columns(self):
        """匯入要把兩個名稱欄分別讀進來，不再合成單一 company_name。"""
        from backend.app.derived.company_alias_importer import normalize_alias_rows

        rows = normalize_alias_rows([{
            "申請人代碼": "TW1234567",
            "公司中文名稱": "喬山健康科技",
            "正規化名稱": "Chi Hua Fitness Co., Ltd.",
            "別稱": "CHI HUA FITNESS CO LTD",
        }])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["zh_name"], "喬山健康科技")
        self.assertEqual(row["normalized_name"], "Chi Hua Fitness Co., Ltd.")
        self.assertEqual(row["alias_name"], "CHI HUA FITNESS CO LTD")

    def test_row_without_any_name_is_dropped(self):
        """兩個名稱欄都空＝這列沒有意義，跳過（原本的 company_name 必填語意）。"""
        from backend.app.derived.company_alias_importer import normalize_alias_rows

        rows = normalize_alias_rows([{
            "申請人代碼": "TW1234567",
            "公司中文名稱": "",
            "正規化名稱": "",
            "別稱": "SOMETHING",
        }])
        self.assertEqual(rows, [], "兩個名稱欄皆空的列不得寫入")

    def test_only_zh_name_is_enough(self):
        """只填中文名要能匯入（拆欄後兩欄各自可空）。"""
        from backend.app.derived.company_alias_importer import normalize_alias_rows

        rows = normalize_alias_rows([{
            "申請人代碼": "TW1234567",
            "公司中文名稱": "喬山健康科技",
            "正規化名稱": "",
            "別稱": "CHI HUA",
        }])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["zh_name"], "喬山健康科技")
        self.assertIsNone(rows[0]["normalized_name"])


class DisplayOrderTests(unittest.TestCase):
    """顯示順位：中文 → 正規化 → 原值（使用者定）。"""

    def test_refresh_sql_display_order(self):
        """`refresh_report_patent_base` 的收斂 COALESCE 要照三段順位。"""
        sql = (PROJECT_ROOT / "backend/app/derived/refresh_report_patent_base.py").read_text(
            encoding="utf-8")

        # 取 code_alias_names 這段 CTE：從宣告起到下一個 CTE 宣告為止。
        # ⚠ 不用 `sql.index("),", start)` 找結尾——CTE 內部本來就有括號，
        # 那個字面隨改動會消失或提早命中（本測試初版即因此 ValueError）。
        start = sql.index("code_alias_names AS (")
        nxt = sql.index("_code_names AS (", start + 1)
        block = sql[start:nxt]

        zh = block.index("公司中文名稱")
        norm = block.index("正規化名稱")
        self.assertLess(zh, norm, "中文名必須排在正規化名稱之前")
        self.assertNotIn("公司名稱", block.replace("公司中文名稱", ""),
                         "收斂 CTE 不得再有舊欄")

    def test_applicant_display_falls_back_to_raw(self):
        """申請人顯示名的最後一段是專利原值，不是舊欄。"""
        sql = (PROJECT_ROOT / "backend/app/derived/refresh_report_patent_base.py").read_text(
            encoding="utf-8")
        line = next(l for l in sql.splitlines() if "AS applicant_display_name" in l)
        self.assertNotIn("公司名稱", line.replace("公司中文名稱", ""),
                         "顯示 COALESCE 仍讀舊欄")
        self.assertIn("標準化申請人", line, "原值 fallback 不得被移除")


class MigrationTests(unittest.TestCase):
    """0041 要真的 drop 欄位，且 downgrade 對稱。"""

    def test_migration_exists_and_drops(self):
        path = PROJECT_ROOT / "alembic/versions/0041_drop_legacy_company_name.py"
        self.assertTrue(path.exists(), "缺 0041 migration")
        src = path.read_text(encoding="utf-8")
        self.assertIn("drop_column", src)
        self.assertIn("add_column", src, "downgrade 要能還原欄位")

    def test_migration_chain(self):
        path = PROJECT_ROOT / "alembic/versions/0041_drop_legacy_company_name.py"
        src = path.read_text(encoding="utf-8")
        self.assertIn('down_revision = "0040_company_name_split"', src)


if __name__ == "__main__":
    unittest.main()
