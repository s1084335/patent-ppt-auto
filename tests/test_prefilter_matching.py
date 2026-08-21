"""初階篩選：確定性比對（切片 C，PRE-003／PRE-004）。

## 🔴 本檔存在的唯一理由：鎖住「前綴詞界」

比對方式有三種，**子字串與完整詞界各自都會出錯，而且方向相反**：

| 方式 | `ion` | `mow` | 問題 |
|---|---|---|---|
| 子字串 `ILIKE '%ion%'` | 265 命中 | 187 | 🔴 `combustion`／`composition` 全中——**大量誤剔除** |
| 完整詞界 `\\mion\\M` | 0 | 11 | 🔴 `mower`／`mowing` 不中——**漏掉該剔除的** |
| **前綴詞界 `~* '\\m詞'`** | **0** | **177** | ✅ 兩端都對 |

⇒ 本檔用**自造語料**把這個行為釘死。⚠ 刻意不依賴正式庫的實測值
（265／177／64）：那些數字會隨資料變動，而本檔要驗的是**比對規則**不是資料。
正式庫的實測留給 C.6 驗收。

## ⚠ 為什麼不用 `LIKE 'term%'`

比對要落在**單字的開頭**，不是**欄位的開頭**。`LIKE 'mow%'` 只在
`title` 剛好以 mow 起頭時命中，`"Lawn mower blade"` 會漏。
`~* '\\mmow'` 的 `\\m` 是「詞首」錨點，才是要的語意。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb

TEST_DB = "patent_ppt_prefilter_match"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 獨立項欄名帶國別後綴且是中文——猜欄名會漏掉它（切片 0 實際踩過）。
CLAIM_COL = "獨立項[KR,JP,US,CN,EP,IN]"

#: 自造語料：每一列都是為了分辨三種比對方式而設計的。
#: (patent_id, title, abstract, 獨立項)
CORPUS = [
    # ── ion：子字串會中、前綴不會中 ──
    (1001, "Internal combustion engine", None, None),
    (1002, "Chemical composition analysis", None, None),
    (1003, None, "The ionization chamber", None),      # ion 開頭 → 前綴要中
    # ── mow：前綴要中詞形變化，完整詞界會漏 ──
    (1004, "Lawn mower blade assembly", None, None),
    (1005, None, "A method of mowing turf", None),
    (1006, None, None, "The mower deck comprises"),
    (1007, "MOW control unit", None, None),            # 大小寫不敏感
    # ── blade：三個欄位各一，驗欄位涵蓋 ──
    (1008, "Blade sharpening tool", None, None),
    (1009, None, "the blade rotates", None),
    (1010, None, None, "a cutting blade mounted"),
    # ── 三欄皆空：PRE-003 要能列出數量 ──
    (1011, None, None, None),
    (1012, "", "", ""),
]


def _kw(dbname: str) -> dict:
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    pwd = os.getenv("PGPASSWORD")
    if pwd:
        kw["password"] = pwd
    return kw


def _alembic_cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


def _reset_pool() -> None:
    """見 test_prefilter_keywords 同名函式：不重設就會連上正式庫。"""
    from backend.app.db import connection

    if getattr(connection, "_pool", None) is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def _assert_pool_targets_test_db() -> None:
    from backend.app.db.connection import get_pool

    with get_pool().connection() as c:
        with c.cursor() as cur:
            cur.execute("SELECT current_database()")
            actual = cur.fetchone()[0]
    if actual != TEST_DB:
        raise AssertionError(
            f"連線池指向 {actual!r}，不是本檔的測試庫 {TEST_DB!r}")


class PrefilterMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 先連、後改 env（見 test_prefilter_keywords 模組 docstring）。
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True,
                                 connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")

        cls._prev = {k: os.environ.get(k)
                     for k in ("PGHOST", "PGPORT", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = _kw("postgres")["host"]
        os.environ["PGPORT"] = str(_kw("postgres")["port"])
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        _reset_pool()
        command.upgrade(_alembic_cfg(), "head")
        _assert_pool_targets_test_db()

    @classmethod
    def tearDownClass(cls):
        _reset_pool()
        for key, value in getattr(cls, "_prev", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True,
                                 connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:  # noqa: BLE001
            pass

    def setUp(self):
        self.conn = psycopg.connect(**_kw(TEST_DB), autocommit=True)
        self.addCleanup(self.conn.close)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM derived_layer.workspace_negative_keywords")
            cur.execute("DELETE FROM derived_layer.report_patent_base")
            cur.execute("DELETE FROM app_layer.workspaces")
            cur.execute("DELETE FROM core_layer.patents")
            cur.execute(
                "INSERT INTO app_layer.workspaces "
                "(workspace_id, workspace_name, is_global, patent_ids_json) "
                "VALUES (901, 'WS-A', false, %s)",
                (Jsonb([pid for pid, *_ in CORPUS]),))
            cur.executemany(
                "INSERT INTO core_layer.patents (id) VALUES (%s)",
                [(pid,) for pid, *_ in CORPUS])
            cur.executemany(
                f'INSERT INTO derived_layer.report_patent_base '
                f'(patent_id, title, abstract, "{CLAIM_COL}") VALUES (%s,%s,%s,%s)',
                CORPUS)

    # ── C.1 前綴詞界的三個鎖 ─────────────────────────────
    def test_ion_matches_only_word_start(self):
        """🔴 `ion` 不得命中 combustion／composition（子字串會，前綴不會）。"""
        from backend.app.prefilter import matching

        hits = matching.match_patent_ids(["ion"], conn=self.conn)
        self.assertEqual(
            hits["ion"], [1003],
            "ion 命中了 combustion／composition——比對被改回子字串了")

    def test_mow_matches_word_forms(self):
        """🔴 `mow` 要命中 mower／mowing／MOW（完整詞界會漏）。"""
        from backend.app.prefilter import matching

        hits = matching.match_patent_ids(["mow"], conn=self.conn)
        self.assertEqual(
            hits["mow"], [1004, 1005, 1006, 1007],
            "mow 沒命中詞形變化——比對被改成完整詞界了")

    def test_blade_matches_across_all_three_fields(self):
        """三個比對欄位都要涵蓋（title／abstract／獨立項）。"""
        from backend.app.prefilter import matching

        hits = matching.match_patent_ids(["blade"], conn=self.conn)
        self.assertEqual(
            sorted(hits["blade"]), [1004, 1008, 1009, 1010],
            "blade 沒有涵蓋三個欄位")

    def test_matching_is_case_insensitive(self):
        from backend.app.prefilter import matching

        upper = matching.match_patent_ids(["MOW"], conn=self.conn)
        lower = matching.match_patent_ids(["mow"], conn=self.conn)
        self.assertEqual(upper["MOW"], lower["mow"])

    # ── C.2 三欄皆空 ────────────────────────────────────
    def test_blank_rows_never_match(self):
        from backend.app.prefilter import matching

        hits = matching.match_patent_ids(["mow", "blade", "ion"], conn=self.conn)
        matched = {pid for ids in hits.values() for pid in ids}
        self.assertNotIn(1011, matched, "三欄皆 NULL 的專利被命中了")
        self.assertNotIn(1012, matched, "三欄皆空字串的專利被命中了")

    def test_blank_field_count_is_listable(self):
        """PRE-003：三個比對欄位皆空者，數量要能列出。

        ⚠ 這不是統計裝飾：這些專利**永遠不會被任何關鍵字命中**，
        使用者要知道「有幾件根本沒東西可比」，否則會誤以為它們都通過了篩選。
        """
        from backend.app.prefilter import matching

        blanks = matching.blank_field_patent_ids(conn=self.conn)
        self.assertEqual(sorted(blanks), [1011, 1012])

    # ── C.4 命中預覽 ────────────────────────────────────
    def test_preview_lists_every_keyword_including_zero(self):
        """🔴 PRE-004：零命中要顯示 0，不得省略該列。

        ⚠ 省略的後果：使用者以為那個關鍵字還沒算完，或以為自己沒輸入過。
        「算過了，結果是 0」與「沒算」必須分得開。
        """
        from backend.app.prefilter import keywords as kw
        from backend.app.prefilter import matching

        for term, terms in (("割草", ["mow"]), ("刀片", ["blade"]),
                            ("離子", ["ion"]), ("完全沒有", ["zzzznotfound"])):
            row = kw.create_keyword(901, term, conn=self.conn)
            kw.update_keyword(row["keyword_id"], match_terms=terms,
                              terms_confirmed=True, conn=self.conn)

        preview = matching.preview_counts(901, conn=self.conn)
        by_term = {p["original_term"]: p["patent_count"] for p in preview}
        self.assertEqual(len(preview), 4, "有關鍵字被省略了")
        self.assertEqual(by_term["割草"], 4)
        self.assertEqual(by_term["刀片"], 4)
        self.assertEqual(by_term["離子"], 1)
        self.assertEqual(by_term["完全沒有"], 0, "零命中被省略或變成 None")

    def test_preview_excludes_unconfirmed(self):
        """未確認的關鍵字不進預覽——它還不會產生任何命中。"""
        from backend.app.prefilter import keywords as kw
        from backend.app.prefilter import matching

        row = kw.create_keyword(901, "割草", conn=self.conn)
        kw.update_keyword(row["keyword_id"], match_terms=["mow"], conn=self.conn)
        preview = matching.preview_counts(901, conn=self.conn)
        self.assertEqual(preview, [], "未確認的關鍵字出現在預覽裡")

    def test_preview_is_workspace_scoped(self):
        from backend.app.prefilter import keywords as kw
        from backend.app.prefilter import matching

        row = kw.create_keyword(901, "割草", conn=self.conn)
        kw.update_keyword(row["keyword_id"], match_terms=["mow"],
                          terms_confirmed=True, conn=self.conn)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_layer.workspaces "
                "(workspace_id, workspace_name, is_global) VALUES (902,'B',false)")
        self.assertEqual(matching.preview_counts(902, conn=self.conn), [])

    # ── C.5 正規化與跳脫收斂到單一函式 ──────────────────
    def test_regex_special_chars_are_escaped(self):
        """🔴 比對詞含正規式特殊字元時不得炸、也不得誤命中。

        ⚠ 使用者輸入 `c++` 或 `(a)` 是常態。不跳脫的後果是
        `psycopg.errors.InvalidRegularExpression`——而且是**執行篩選時才炸**，
        不是輸入時。
        """
        from backend.app.prefilter import matching

        for bad in ("c++", "(a)", "a|b", "a*", "[x]", "a.b"):
            with self.subTest(term=bad):
                hits = matching.match_patent_ids([bad], conn=self.conn)
                self.assertIn(bad, hits)

    def test_normalization_has_single_definition(self):
        """C.5：正規化與跳脫只能有一個函式，不得兩處各自處理。"""
        import inspect

        from backend.app.prefilter import matching

        src = inspect.getsource(matching)
        self.assertIn("def normalize_term", src)
        # 跳脫只能出現在 normalize_term 裡
        outside = src.replace(
            inspect.getsource(matching.normalize_term), "")
        self.assertNotIn(
            "re.escape", outside,
            "normalize_term 之外還有跳脫邏輯——兩處各自處理特殊字元會漂移")

    # ── 成員清單走唯一來源（使用者 2026-08-21：不要寫死在某些專利上）──
    def test_membership_uses_existing_single_source(self):
        """🔴 成員清單必須走 `display_member_patent_ids`，不得自己讀 patent_ids_json。

        ⚠ 兩層理由：
        ① 同一份知識只能有一個定義處——成員判定日後若改（例如組合 workspace 的
          展開規則），自己讀那份就會靜默落後。
        ② 初篩之後還會有其他型態的 workspace（組合、案件比對、全庫），
          該函式已經一致處理它們；寫死讀 json 等於把機制綁在單一型態上。
        """
        import inspect

        from backend.app.prefilter import matching

        src = inspect.getsource(matching)
        self.assertIn("display_member_patent_ids", src,
                      "沒有沿用既有的成員唯一來源")
        # ⚠ 只驗**實際查詢**，不驗字面出現：解釋「為什麼不自己讀」的註解
        #   本來就會提到這個欄名（第一版斷言太粗，被自己的註解絆倒）。
        sql_lines = [
            ln.strip() for ln in src.splitlines()
            if "patent_ids_json" in ln
            and not ln.lstrip().startswith("#")
            and ("SELECT" in ln.upper() or "FROM" in ln.upper())
        ]
        self.assertEqual(
            sql_lines, [],
            f"自己查了 patent_ids_json——成員判定又多了一份定義：{sql_lines}")

    def test_works_for_any_workspace_shape(self):
        """機制不綁 workspace 型態：換一個成員完全不同的 workspace 照樣算得出來。"""
        from backend.app.prefilter import keywords as kw
        from backend.app.prefilter import matching

        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_layer.workspaces "
                "(workspace_id, workspace_name, is_global, patent_ids_json) "
                "VALUES (904, 'WS-D', false, %s)", (Jsonb([1008, 1009]),))
        row = kw.create_keyword(904, "刀片", conn=self.conn)
        kw.update_keyword(row["keyword_id"], match_terms=["blade"],
                          terms_confirmed=True, conn=self.conn)

        preview = matching.preview_counts(904, conn=self.conn)
        self.assertEqual(preview[0]["patent_count"], 2,
                         "換一個 workspace 就算不出來——機制綁在特定成員上了")
        self.assertEqual(preview[0]["patent_ids"], [1008, 1009])

    def test_empty_terms_produce_no_query(self):
        """沒有比對詞時回空 dict，不得組出會命中全部的 SQL。"""
        from backend.app.prefilter import matching

        self.assertEqual(matching.match_patent_ids([], conn=self.conn), {})


if __name__ == "__main__":
    unittest.main()
