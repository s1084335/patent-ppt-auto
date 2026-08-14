"""公司名四欄拆分的寫入／顯示／AI runner 契約（2026-07-28 使用者定案）。

四點定案（不重新設計）：
① 報表顯示：一律中文，沒中文才退英文正式名（`公司中文名稱` → `正規化名稱` → 既有後續）
② 兩欄都可空、不加 CHECK
③ 既有資料不自動遷移（migration 只加欄）
④ AI runner：輸入讀 `正規化名稱`（空則退別稱原文）、輸出寫 `公司中文名稱`、
   待處理判斷改為「`公司中文名稱` 為空」；`keep_original` 時**中文欄留空**

⚠ 本檔測試一律鎖真實行為（實際呼叫函式、驗證產出的 SQL／payload 內容），
不做「字串出現在整份檔案」這種會被註解餵飽的斷言——本專案今日已因此假性通過 5 次。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ZH_COL = "公司中文名稱"
EN_COL = "正規化名稱"


class FakeCursor:
    """側錄 execute 的假 cursor：驗「真的送出了什麼 SQL 與參數」。"""

    def __init__(self, sink: list, fetch_map=None):
        self.sink = sink
        self.fetch_map = fetch_map or {}
        self._last = None

    def execute(self, sql, params=None):
        self.sink.append((sql, params))
        self._last = sql
        return self

    def fetchone(self):
        for needle, value in self.fetch_map.items():
            if self._last and needle in self._last:
                return value
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, sink: list, fetch_map=None):
        self.sink = sink
        self.fetch_map = fetch_map or {}
        self.committed = False

    def execute(self, sql, params=None):
        return FakeCursor(self.sink, self.fetch_map).execute(sql, params)

    def cursor(self, **kwargs):
        return FakeCursor(self.sink, self.fetch_map)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_connect(sink, fetch_map=None):
    """psycopg.connect → FakeConn（不碰 DB，但真的跑完整條寫入邏輯）。"""
    return mock.patch("psycopg.connect", return_value=FakeConn(sink, fetch_map))


class ApplyConfirmedFourColumnTests(unittest.TestCase):
    """唯一寫入路徑 apply_confirmed_display_names 要能寫四欄。

    ⚠ 硬性約束：寫入只有這一個點，四欄拆分擴充它的 mapping 形狀，
    不另開第二條寫入路徑。
    """

    def _apply(self, mapping, fetch_map=None):
        from backend.app.derived.company_alias_importer import apply_confirmed_display_names

        sink: list = []
        with _patch_connect(sink, fetch_map):
            result = apply_confirmed_display_names(mapping, "test_label")
        return sink, result

    def test_insert_writes_both_new_columns(self):
        """一組（中文＋英文皆填）→ INSERT 必須同時帶兩個新欄與對應值。"""
        sink, _ = self._apply({
            "C1": {"zh_name": "喬山健康科技", "normalized_name": "Chi Hua Fitness Co., Ltd.",
                   "aliases": ["CHI HUA FITNESS CO LTD"]},
        })
        inserts = [(s, p) for s, p in sink if "INSERT INTO" in s.upper()]
        self.assertTrue(inserts, "沒有任何 INSERT——四欄寫入未實作")
        sql, params = inserts[0]
        self.assertIn(ZH_COL, sql, f"INSERT 未帶 {ZH_COL} 欄")
        self.assertIn(EN_COL, sql, f"INSERT 未帶 {EN_COL} 欄")
        flat = list(params) if isinstance(params, (list, tuple)) else list(params.values())
        self.assertIn("喬山健康科技", flat, "中文名沒送進參數")
        self.assertIn("Chi Hua Fitness Co., Ltd.", flat, "英文正式名沒送進參數")

    def test_update_recanonicalize_sets_both_columns(self):
        """既有列 re-canonicalize（唯一寫入規則）也必須改寫兩個新欄。"""
        sink, _ = self._apply(
            {"C1": {"zh_name": "喬山健康科技", "normalized_name": "Chi Hua Fitness Co., Ltd.",
                    "aliases": ["CHI HUA"]}},
            fetch_map={"SELECT id": (77,)},
        )
        updates = [(s, p) for s, p in sink if s.strip().upper().startswith("UPDATE")]
        self.assertTrue(updates, "既有列未走 UPDATE re-canonicalize")
        sql, params = updates[0]
        self.assertIn(ZH_COL, sql, f"UPDATE 未改寫 {ZH_COL}")
        self.assertIn(EN_COL, sql, f"UPDATE 未改寫 {EN_COL}")

    def test_zh_only_group_leaves_en_empty(self):
        """只填中文名 → 英文欄留空（使用者第②點：兩欄都可空、不擋）。"""
        sink, _ = self._apply({"C1": {"zh_name": "喬山健康科技", "aliases": ["CHI HUA"]}})
        inserts = [(s, p) for s, p in sink if "INSERT INTO" in s.upper()]
        self.assertTrue(inserts)
        _, params = inserts[0]
        flat = list(params) if isinstance(params, (list, tuple)) else list(params.values())
        self.assertIn("喬山健康科技", flat)
        self.assertNotIn("", [v for v in flat if v == ""], "空字串不該當英文名寫入，應為 None")

    def test_en_only_group_writes_en(self):
        """只填英文正式名 → 中文欄留空、英文欄有值（顯示會退英文，符合第①點）。"""
        sink, _ = self._apply({
            "C1": {"normalized_name": "Mario Contenti S.r.l.", "aliases": ["MARIO CONTENTI"]},
        })
        inserts = [(s, p) for s, p in sink if "INSERT INTO" in s.upper()]
        self.assertTrue(inserts, "只填英文名時沒有寫入——不得被舊 canonical 必填擋掉")
        _, params = inserts[0]
        flat = list(params) if isinstance(params, (list, tuple)) else list(params.values())
        self.assertIn("Mario Contenti S.r.l.", flat)

    def test_alias_includes_both_official_names(self):
        """canonical 自身納入別稱的既有規則，拆欄後兩個正式名都要納入。

        沒有它，使用者填的中文正式名字面在專利表就命不中（既有行為的延續）。
        """
        sink, _ = self._apply({
            "C1": {"zh_name": "喬山健康科技", "normalized_name": "Chi Hua Fitness Co., Ltd.",
                   "aliases": []},
        })
        inserts = [(s, p) for s, p in sink if "INSERT INTO" in s.upper()]
        written_aliases = set()
        for _, params in inserts:
            flat = list(params) if isinstance(params, (list, tuple)) else list(params.values())
            written_aliases.update(str(v) for v in flat if v)
        self.assertIn("喬山健康科技", written_aliases)
        self.assertIn("Chi Hua Fitness Co., Ltd.", written_aliases)

    def test_legacy_canonical_key_still_accepted(self):
        """既有呼叫端（中文名確認端點）用 `canonical` 鍵，不得被拆欄打斷。"""
        sink, _ = self._apply({"C1": {"canonical": "喬山健康科技", "aliases": ["CHI HUA"]}})
        self.assertTrue([s for s, _ in sink if "INSERT INTO" in s.upper()],
                        "舊 canonical 鍵失效——既有呼叫端會靜默不寫入")


class DisplayCoalesceTests(unittest.TestCase):
    """第①點：報表顯示一律中文，沒中文才退英文正式名。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (PROJECT_ROOT / "backend" / "app" / "derived"
                   / "refresh_report_patent_base.py").read_text(encoding="utf-8")
        # 只看 SQL 常數本身，不看 Python 註解（避免被說明文字餵飽）
        cls.sql = "\n".join(
            line for line in cls.src.splitlines() if not line.strip().startswith("--"))

    def test_code_alias_names_prefers_zh_then_en(self):
        """代碼→顯示名的 CTE 必須 COALESCE(中文名, 英文正式名)。"""
        m = re.search(r"code_alias_names AS \((.*?)\n\),", self.sql, re.S)
        self.assertIsNotNone(m, "找不到 code_alias_names CTE")
        cte = m.group(1)
        self.assertIn(ZH_COL, cte, "顯示名 CTE 未取 公司中文名稱")
        self.assertIn(EN_COL, cte, "顯示名 CTE 未取 正規化名稱（沒中文時的退路）")
        self.assertLess(
            cte.index(ZH_COL), cte.index(EN_COL),
            "順位錯：中文名必須排在英文正式名之前（使用者第①點）")

    def test_alias_lateral_prefers_zh_then_en(self):
        """三條別稱 LATERAL 也要改順位，否則別稱路徑仍吐舊欄。"""
        laterals = re.findall(
            r"SELECT (COALESCE\(.*?)\n\s*FROM derived_layer\.company_aliases", self.sql)
        self.assertGreaterEqual(len(laterals), 3,
                                "三條別稱 LATERAL 未全部改成 COALESCE(中文, 英文, …)")
        for expr in laterals:
            self.assertIn(ZH_COL, expr)
            self.assertIn(EN_COL, expr)
            self.assertLess(expr.index(ZH_COL), expr.index(EN_COL))


class AiRunnerFourColumnTests(unittest.TestCase):
    """第④點：AI runner 輸入讀英文正式名、輸出寫中文欄、待處理判斷改欄。"""

    def test_pending_sql_keys_on_empty_zh_column(self):
        """待處理判斷由「公司名稱不含 CJK」改為「公司中文名稱為空」。"""
        from backend.app.worker.ai_company_zh_name_runner import CompanyZhNameStore

        sql = CompanyZhNameStore.PENDING_SQL
        self.assertIn(ZH_COL, sql, "PENDING_SQL 未依 公司中文名稱 判斷待處理")
        self.assertNotIn(
            "一-鿿", sql,
            "仍以 CJK 字元類別推測——使用者定案改為直接看中文欄是否為空")

    def test_pending_sql_reads_normalized_name_as_input(self):
        """輸入＝正規化名稱；該欄空時退用別稱原文。"""
        from backend.app.worker.ai_company_zh_name_runner import CompanyZhNameStore

        sql = CompanyZhNameStore.PENDING_SQL
        self.assertIn(EN_COL, sql, "PENDING_SQL 未讀 正規化名稱 當 AI 輸入")
        self.assertIn("別稱", sql, "正規化名稱為空時未退用別稱原文")

    def test_keep_original_leaves_zh_empty(self):
        """`keep_original`＝查無慣用中文名 → **中文欄留空**，不把英文塞進中文欄。

        鎖真實行為：跑完整條 run_company_zh_name，檢查交給 store 的草稿內容。
        """
        from backend.app.worker import ai_company_zh_name_runner as mod

        class FakeStore:
            def __init__(self):
                self.written = None

            def fetch_pending(self, *, limit=None):
                return [("C1", "Mario Contenti S.r.l.")]

            def write_drafts(self, drafts):
                self.written = list(drafts)
                return len(drafts)

        store = FakeStore()

        def fake_cli(argv, timeout):
            from backend.app.worker.cli_gateway import CliResult

            return CliResult(
                exit_code=0,
                stdout='{"names":[{"company_code":"C1","verdict":"keep_original"}]}',
                stderr="")

        with mock.patch.object(mod.pf, "write_payload_file", return_value=Path("x.json")), \
             mock.patch.object(mod.pf, "build_cli_command_with_payload", return_value=["x"]):
            mod.run_company_zh_name(cli_runner=fake_cli, store=store)

        self.assertEqual(len(store.written), 1)
        draft = store.written[0]
        self.assertEqual(draft["verdict"], "keep_original")
        self.assertFalse(
            (draft.get("zh_name") or "").strip(),
            "keep_original 仍把英文原文塞進中文名——違反使用者第④點")

    def test_draft_insert_targets_zh_column(self):
        """草稿列寫入 `公司中文名稱`（translated 時），不再寫舊 公司名稱 欄。"""
        from backend.app.worker.ai_company_zh_name_runner import CompanyZhNameStore

        sql = CompanyZhNameStore._INSERT_DRAFT_SQL
        self.assertIn(ZH_COL, sql, "草稿 INSERT 未寫 公司中文名稱")


if __name__ == "__main__":
    unittest.main()
