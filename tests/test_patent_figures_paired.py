"""專利代表圖一對多保存（core_layer.patent_figures）契約測試（拋棄式測試 DB，不碰 patent_ppt）。

對應規格：`.agents/context/patent-figures-design.md`「中期方案規格」。

涵蓋三段契約：
1. migration 0031：建 patent_figures（3 欄＋PK＋CASCADE），downgrade 只刪表、**不得清空**主表快取。
2. 匯入端一對多：同專利多階段（A/B）兩張圖皆入庫；主表 "主附圖" 快取恆為 rank 最大者
   （B 審定公告 > A 早期公開），不依 Excel 列序、不依字母序。
3. 通用性六項：無圖不爆、部分缺圖、同 (patent_id, kind) 多圖記警告、未知 kind rank=0
   不覆蓋已知階段快取、kind 缺值仍入庫、批次寫入不 N+1。
"""
from __future__ import annotations

import io
import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage


TEST_DB = "patent_ppt_figpair"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIGURE_COLUMN = "主附圖"


def _kw(dbname: str) -> dict:
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.environ["PGPASSWORD"]
    return kw


def _rw(dbname: str) -> dict:
    """連線含 search_path 讓 importer 的裸表名可用。"""
    kw = _kw(dbname)
    kw["options"] = "-c search_path=raw_layer,core_layer,app_layer,public"
    return kw


def _alembic_cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


# 1x1 灰階 PNG 的最小合法位元組（不依賴 Pillow）；seed 讓每張圖 bytes 互異，可精確驗證落點。
_PNG_HEAD = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e9b55"
)


def _png_bytes(seed: int) -> bytes:
    """產生內容互異的小張測試 PNG；seed 只影響 IDAT 內容，仍是合法 PNG。"""
    import struct
    import zlib

    raw = zlib.compress(bytes([0, seed & 0xFF]))
    idat = struct.pack(">I", len(raw)) + b"IDAT" + raw
    idat += struct.pack(">I", zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return _PNG_HEAD + idat + iend


def _build_xlsx(path: Path, rows: list[dict], images: list[tuple[int, bytes]]) -> None:
    """造測試 xlsx：rows 為資料列（可含「文献种类」），images 為 (0-based anchor row, 圖 bytes)。

    表頭固定含 WIPS 必要欄（申请号/标题/申请日）＋主附图＋文献种类；圖片以浮動物件錨在指定列，
    與真檔一致（儲存格文字值為空白）。「文献种类」允許 None 以模擬缺值。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "download"
    headers = ["主附图", "申请号", "标题", "申请日", "国家代码", "文献种类"]
    ws.append(headers)
    for row in rows:
        ws.append([
            " ",
            row["申请号"],
            row["标题"],
            row.get("申请日", "2020-01-01"),
            "TW",
            row.get("文献种类"),
        ])
    for anchor_row, blob in images:
        img = XLImage(io.BytesIO(blob))
        # openpyxl anchor 以 "A{1-based 列}" 表示；anchor_row 為 0-based。
        img.anchor = f"A{anchor_row + 1}"
        ws.add_image(img)
    wb.save(path)


class PatentFiguresMigrationTests(unittest.TestCase):
    """0031 migration 契約：建表 3 欄＋PK＋CASCADE；downgrade 只刪表、不清主表快取。"""

    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        command.upgrade(_alembic_cfg(), "head")

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    def test_patent_figures_schema(self):
        """3 欄、型別正確、PK 為 (patent_id, document_kind)。"""
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            columns = dict(
                conn.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema='core_layer' AND table_name='patent_figures'"
                ).fetchall()
            )
            pk = conn.execute(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = 'core_layer.patent_figures'::regclass AND i.indisprimary
                ORDER BY a.attname
                """
            ).fetchall()
        self.assertEqual(
            columns,
            {"patent_id": "bigint", "document_kind": "text", "content": "bytea"},
        )
        self.assertEqual([r[0] for r in pk], ["document_kind", "patent_id"])

    def test_delete_patent_cascades_figures(self):
        """ON DELETE CASCADE：刪專利連帶刪圖，不留孤兒列。"""
        with psycopg.connect(**_rw(TEST_DB), autocommit=True) as conn:
            pid = conn.execute(
                'INSERT INTO core_layer.patents ("申請號") VALUES (%s) RETURNING id',
                ("TW-CASCADE-1",),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO core_layer.patent_figures (patent_id, document_kind, content) "
                "VALUES (%s, %s, %s)",
                (pid, "A", _png_bytes(1)),
            )
            conn.execute("DELETE FROM core_layer.patents WHERE id = %s", (pid,))
            left = conn.execute(
                "SELECT count(*) FROM core_layer.patent_figures WHERE patent_id = %s", (pid,)
            ).fetchone()[0]
        self.assertEqual(left, 0)

    def test_downgrade_drops_table_without_wiping_main_cache(self):
        """downgrade 只還原本次變更（刪表），**不得**清空主表 "主附圖"（超出範圍的破壞）。"""
        cfg = _alembic_cfg()
        with psycopg.connect(**_rw(TEST_DB), autocommit=True) as conn:
            pid = conn.execute(
                f'INSERT INTO core_layer.patents ("申請號", "{FIGURE_COLUMN}") '
                "VALUES (%s, %s) RETURNING id",
                ("TW-DOWN-1", _png_bytes(7)),
            ).fetchone()[0]
        command.downgrade(cfg, "0030_company_alias_code_lookup")
        try:
            with psycopg.connect(**_kw(TEST_DB)) as conn:
                table = conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema='core_layer' "
                    "AND table_name='patent_figures'"
                ).fetchone()
                cached = conn.execute(
                    f'SELECT "{FIGURE_COLUMN}" FROM core_layer.patents WHERE id = %s', (pid,)
                ).fetchone()[0]
            self.assertIsNone(table, "downgrade 後 patent_figures 應已移除")
            self.assertIsNotNone(cached, "downgrade 不得清空主表既有主附圖")
            self.assertEqual(bytes(cached), _png_bytes(7))
        finally:
            command.upgrade(cfg, "head")


class PatentFiguresImportTests(unittest.TestCase):
    """匯入端一對多寫入與通用性六項。"""

    DB = TEST_DB + "_imp"

    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = cls.DB
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{cls.DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{cls.DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        command.upgrade(_alembic_cfg(), "head")

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{cls.DB}" WITH (FORCE)')
        except Exception:
            pass

    def setUp(self):
        self._tmp = Path(os.environ.get("TEMP", "/tmp")) / "figpair_tests"
        self._tmp.mkdir(parents=True, exist_ok=True)
        with psycopg.connect(**_rw(self.DB), autocommit=True) as conn:
            conn.execute(
                "TRUNCATE core_layer.patents, raw_layer.raw_records RESTART IDENTITY CASCADE"
            )

    def _rows(self) -> dict[tuple[str, str], bytes]:
        """讀回 {(申請號, document_kind): 圖 bytes}（patent_figures 全表）。"""
        with psycopg.connect(**_kw(self.DB)) as conn:
            rows = conn.execute(
                'SELECT p."申請號", f.document_kind, f.content '
                "FROM core_layer.patent_figures f "
                "JOIN core_layer.patents p ON p.id = f.patent_id"
            ).fetchall()
        return {(r[0], r[1]): bytes(r[2]) for r in rows}

    def _cache(self) -> dict[str, bytes | None]:
        """讀回 {申請號: 主表快取 bytes}。"""
        with psycopg.connect(**_kw(self.DB)) as conn:
            rows = conn.execute(
                f'SELECT "申請號", "{FIGURE_COLUMN}" FROM core_layer.patents ORDER BY id'
            ).fetchall()
        return {r[0]: (bytes(r[1]) if r[1] is not None else None) for r in rows}

    # --- (a) 無內嵌圖 ---
    def test_a_file_without_images_imports_fine(self):
        """無內嵌圖的 xlsx 正常匯入不爆；patent_figures 無列、主表快取 NULL。"""
        from backend.app.importers.wips_importer import import_wips_file

        path = self._tmp / "no_images.xlsx"
        _build_xlsx(path, [{"申请号": "TW-N-001", "标题": "無圖", "文献种类": "A"}], [])
        summary = import_wips_file(path)
        self.assertEqual(summary["inserted"], 1)
        self.assertEqual(summary["figures"], 0)
        self.assertEqual(self._rows(), {})
        self.assertIsNone(self._cache()["TW-N-001"])

    # --- (b) 部分列缺圖 ---
    def test_b_partial_images_only_written_for_rows_with_figures(self):
        """部分列缺圖：只寫有圖的列，其餘兩表皆無值。"""
        from backend.app.importers.wips_importer import import_wips_file

        blob = _png_bytes(33)
        path = self._tmp / "partial.xlsx"
        _build_xlsx(
            path,
            [
                {"申请号": "TW-P-001", "标题": "無圖", "文献种类": "A"},
                {"申请号": "TW-P-002", "标题": "有圖", "文献种类": "A"},
                {"申请号": "TW-P-003", "标题": "無圖2", "文献种类": "A"},
            ],
            [(2, blob)],
        )
        summary = import_wips_file(path)
        self.assertEqual(summary["figures"], 1)
        self.assertEqual(self._rows(), {("TW-P-002", "A"): blob})
        cache = self._cache()
        self.assertIsNone(cache["TW-P-001"])
        self.assertEqual(cache["TW-P-002"], blob)
        self.assertIsNone(cache["TW-P-003"])

    # --- 核心：一對多 + 最新版快取 ---
    def test_multi_stage_keeps_both_and_caches_latest(self):
        """同專利 A/B 兩階段：兩張皆入庫；主表快取取 rank 最大者（B），與 Excel 列序無關。

        A 列在後（列序較晚），若沿短期版「後到為準」快取會是 A——必須是 B。
        """
        from backend.app.importers.wips_importer import import_wips_file

        fig_b, fig_a = _png_bytes(101), _png_bytes(102)
        path = self._tmp / "multi_stage.xlsx"
        _build_xlsx(
            path,
            [
                {"申请号": "TW-M-001", "标题": "公告版", "文献种类": "B"},
                {"申请号": "TW-M-001", "标题": "公開版", "文献种类": "A"},
            ],
            [(1, fig_b), (2, fig_a)],
        )
        summary = import_wips_file(path)
        rows = self._rows()
        self.assertEqual(rows.get(("TW-M-001", "B")), fig_b)
        self.assertEqual(rows.get(("TW-M-001", "A")), fig_a)
        self.assertEqual(summary["figures"], 2)
        self.assertEqual(self._cache()["TW-M-001"], fig_b, "主表快取必須是 rank 最大的 B 版")

    def test_kind_rank_not_alphabetical(self):
        """階段序不得靠字母序：U（新型公開，未知 rank）不得因 'U' > 'B' 而勝過 B。"""
        from backend.app.importers.wips_importer import import_wips_file

        fig_b, fig_u = _png_bytes(111), _png_bytes(112)
        path = self._tmp / "rank_not_alpha.xlsx"
        _build_xlsx(
            path,
            [
                {"申请号": "TW-R-001", "标题": "公告", "文献种类": "B"},
                {"申请号": "TW-R-001", "标题": "未知階段", "文献种类": "U"},
            ],
            [(1, fig_b), (2, fig_u)],
        )
        import_wips_file(path)
        rows = self._rows()
        self.assertEqual(rows.get(("TW-R-001", "U")), fig_u, "未知 kind 仍須入庫保存")
        self.assertEqual(
            self._cache()["TW-R-001"], fig_b, "未知 kind（rank=0）不得覆蓋已知階段快取"
        )

    # --- (c) 同 (patent_id, kind) 多圖 ---
    def test_c_duplicate_patent_kind_warns_not_silent(self):
        """同 (patent_id, kind) 出現多張圖：取一張且必須留下 warning，不靜默丟棄。"""
        from backend.app.importers.wips_importer import import_wips_file

        first, second = _png_bytes(44), _png_bytes(55)
        path = self._tmp / "dup_kind.xlsx"
        _build_xlsx(
            path,
            [
                {"申请号": "TW-D-001", "标题": "同階段一", "文献种类": "A"},
                {"申请号": "TW-D-001", "标题": "同階段二", "文献种类": "A"},
            ],
            [(1, first), (2, second)],
        )
        summary = import_wips_file(path)
        rows = self._rows()
        self.assertEqual(len(rows), 1, "同 (patent_id, kind) 只留一列（PK 保證）")
        warnings = summary.get("figure_warnings") or []
        self.assertTrue(warnings, "同 (patent_id, kind) 多圖必須記 warning")
        self.assertTrue(
            any("TW-D-001" in str(w) or "A" in str(w) for w in warnings),
            f"warning 應可辨識衝突對象，實得 {warnings}",
        )

    def test_c2_multiple_images_on_same_excel_row_warns(self):
        """同一 Excel 列偵測到多張圖：取第一張並記警告（沿短期版契約，不得迴歸）。"""
        from backend.app.importers.wips_importer import import_wips_file

        first, second = _png_bytes(66), _png_bytes(77)
        path = self._tmp / "multi_on_row.xlsx"
        _build_xlsx(path, [{"申请号": "TW-S-001", "标题": "多圖", "文献种类": "A"}], [(1, first), (1, second)])
        summary = import_wips_file(path)
        self.assertEqual(self._rows()[("TW-S-001", "A")], first)
        warnings = summary.get("figure_warnings") or []
        self.assertTrue(warnings, "同列多圖必須留下警告")
        self.assertTrue(any("2" in str(w) for w in warnings))

    # --- (d) 未知 kind ---
    def test_d_unknown_kind_stored_but_not_cached_over_known(self):
        """未知 kind → rank=0 入庫保存；已有已知階段快取時不得覆蓋，並記 warning。"""
        from backend.app.importers.wips_importer import import_wips_file

        fig_a, fig_x = _png_bytes(121), _png_bytes(122)
        path = self._tmp / "unknown_kind.xlsx"
        _build_xlsx(
            path,
            [
                {"申请号": "TW-U-001", "标题": "公開", "文献种类": "A"},
                {"申请号": "TW-U-001", "标题": "怪階段", "文献种类": "Z9"},
            ],
            [(1, fig_a), (2, fig_x)],
        )
        summary = import_wips_file(path)
        rows = self._rows()
        self.assertEqual(rows.get(("TW-U-001", "Z9")), fig_x, "未知 kind 必須入庫")
        self.assertEqual(self._cache()["TW-U-001"], fig_a, "未知 kind 不得覆蓋已知階段快取")
        warnings = summary.get("figure_warnings") or []
        self.assertTrue(any("Z9" in str(w) for w in warnings), f"未知 kind 應記 warning，實得 {warnings}")

    def test_d2_unknown_kind_alone_still_caches(self):
        """全檔只有未知 kind：仍須寫快取（沒有更好的候選），不因 rank=0 而讓前端無圖。"""
        from backend.app.importers.wips_importer import import_wips_file

        fig = _png_bytes(131)
        path = self._tmp / "unknown_only.xlsx"
        _build_xlsx(path, [{"申请号": "TW-U-002", "标题": "只有怪階段", "文献种类": "Z9"}], [(1, fig)])
        import_wips_file(path)
        self.assertEqual(self._rows().get(("TW-U-002", "Z9")), fig)
        self.assertEqual(self._cache()["TW-U-002"], fig)

    # --- (e) kind 缺值 ---
    def test_e_missing_kind_still_stores_figure(self):
        """document_kind 缺值：圖仍入庫（kind 落 UNKNOWN），不因缺欄丟圖，並記 warning。"""
        from backend.app.importers.wips_importer import import_wips_file

        fig = _png_bytes(141)
        path = self._tmp / "missing_kind.xlsx"
        _build_xlsx(path, [{"申请号": "TW-K-001", "标题": "缺種類", "文献种类": None}], [(1, fig)])
        summary = import_wips_file(path)
        rows = self._rows()
        self.assertEqual(len(rows), 1, "kind 缺值不得丟圖")
        (applied_number, kind), content = next(iter(rows.items()))
        self.assertEqual(applied_number, "TW-K-001")
        self.assertEqual(content, fig)
        self.assertTrue(kind, "document_kind 為 NOT NULL，缺值須以佔位值落庫")
        self.assertEqual(self._cache()["TW-K-001"], fig)
        self.assertTrue(
            summary.get("figure_warnings"), "kind 缺值應記 warning 讓使用者可見"
        )

    # --- (f) 批次寫入 ---
    def test_f_batch_write_not_n_plus_1(self):
        """效率契約：三列三圖時 update_patent_figures 只被呼叫一次，且底層走 executemany。"""
        from backend.app.importers import wips_importer

        blobs = [_png_bytes(c) for c in (60, 70, 80)]
        path = self._tmp / "batch.xlsx"
        _build_xlsx(
            path,
            [{"申请号": f"TW-E-00{i}", "标题": f"批次{i}", "文献种类": "A"} for i in (1, 2, 3)],
            [(i, blob) for i, blob in enumerate(blobs, start=1)],
        )
        calls: list[int] = []
        original = wips_importer.update_patent_figures

        def spy(cur, triplets):
            calls.append(len(triplets))
            # 攔截 cursor：確認整批只發一次 executemany、零次逐筆 execute（N+1 偵測）。
            counters = {"execute": 0, "executemany": 0}

            class _CountingCursor:
                def execute(self, *args, **kwargs):
                    counters["execute"] += 1
                    return cur.execute(*args, **kwargs)

                def executemany(self, *args, **kwargs):
                    counters["executemany"] += 1
                    return cur.executemany(*args, **kwargs)

                def __getattr__(self, name):
                    return getattr(cur, name)

            result = original(_CountingCursor(), triplets)
            spy.counters = counters
            return result

        wips_importer.update_patent_figures = spy
        try:
            wips_importer.import_wips_file(path)
        finally:
            wips_importer.update_patent_figures = original
        self.assertEqual(len(calls), 1, "圖片寫入應為單次批次呼叫")
        self.assertEqual(calls[0], 3)
        counters = getattr(spy, "counters", {})
        self.assertLessEqual(
            counters.get("execute", 0), 2, f"不得逐筆 execute（N+1），實得 {counters}"
        )
        self.assertGreaterEqual(counters.get("executemany", 0), 1, "應使用 executemany 批送")
        rows = self._rows()
        for i, blob in enumerate(blobs, start=1):
            self.assertEqual(rows[(f"TW-E-00{i}", "A")], blob)

    def test_reimport_same_kind_upserts_not_duplicates(self):
        """重匯同階段：PK 天然去重，走 upsert 覆蓋，不新增列。"""
        from backend.app.importers.wips_importer import import_wips_file

        old, new = _png_bytes(151), _png_bytes(152)
        first_path = self._tmp / "reimport_1.xlsx"
        second_path = self._tmp / "reimport_2.xlsx"
        _build_xlsx(first_path, [{"申请号": "TW-I-001", "标题": "初版", "文献种类": "B"}], [(1, old)])
        # 標題不同 → file_hash 不同，不會被整檔冪等擋掉。
        _build_xlsx(second_path, [{"申请号": "TW-I-001", "标题": "改版", "文献种类": "B"}], [(1, new)])
        import_wips_file(first_path)
        import_wips_file(second_path)
        rows = self._rows()
        self.assertEqual(len(rows), 1, "同 (patent_id, kind) 重匯不得長出第二列")
        self.assertEqual(rows[("TW-I-001", "B")], new, "重匯同階段應以新值覆蓋")
        self.assertEqual(self._cache()["TW-I-001"], new)


if __name__ == "__main__":
    unittest.main()
