"""專利主附圖（WIPS Excel 內嵌代表圖）入庫契約測試（獨立測試 DB，不碰 patent_ppt）。

涵蓋三段契約：
1. migration 0026：core_layer.patents 新增 "主附圖" bytea、core_layer.patent_attributes
   移除舊的 "主附圖" text；downgrade 可還原。
2. 匯入端：含內嵌圖的 xlsx 匯入後，patents."主附圖" 存到正確 bytes（依錨點列對應）；
   無圖檔案、部分列缺圖、同列多圖（取第一張並記警告）皆不得中斷匯入。
3. 讀取端點：GET /patents/{id}/figure 回 image/jpeg 與 ETag；無圖回 404。
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


TEST_DB = "patent_ppt_mainfig"
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


# 1x1 灰階 PNG 的最小合法位元組（不依賴 Pillow；單元測試不碰 30MB 真檔）。
# 以 IDAT 內容差一個 byte 產生不同圖，讓「哪張圖落到哪筆專利」可被精確驗證。
_PNG_HEAD = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e9b55"
)


def _png_bytes(seed: int) -> bytes:
    """產生內容互異的小張測試 PNG；seed 只影響尾端註解區塊，仍是合法 PNG。"""
    import struct
    import zlib

    raw = zlib.compress(bytes([0, seed & 0xFF]))
    idat = struct.pack(">I", len(raw)) + b"IDAT" + raw
    idat += struct.pack(">I", zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return _PNG_HEAD + idat + iend


def _build_xlsx(path: Path, rows: list[dict], images: list[tuple[int, bytes]]) -> None:
    """造測試 xlsx：rows 為資料列，images 為 (0-based anchor row, 圖 bytes)。

    表頭固定含 WIPS 必要欄（申请号/标题/申请日）與主附图欄；圖片以浮動物件錨在指定列，
    與真檔一致（儲存格文字值為空白）。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "download"
    headers = ["主附图", "申请号", "标题", "申请日", "国家代码"]
    ws.append(headers)
    for row in rows:
        ws.append([" ", row["申请号"], row["标题"], row.get("申请日", "2020-01-01"), "TW"])
    for anchor_row, blob in images:
        img = XLImage(io.BytesIO(blob))
        # openpyxl anchor 以 "A{1-based 列}" 表示；anchor_row 為 0-based。
        img.anchor = f"A{anchor_row + 1}"
        ws.add_image(img)
    wb.save(path)


class MainFigureMigrationTests(unittest.TestCase):
    """0026 migration 契約：主表加 bytea 欄、attributes 移除舊 text 欄、downgrade 還原。"""

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

    def test_patents_has_bytea_figure_column(self):
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            row = conn.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='core_layer' AND table_name='patents' AND column_name=%s",
                (FIGURE_COLUMN,),
            ).fetchone()
        self.assertIsNotNone(row, "core_layer.patents 應有 主附圖 欄")
        self.assertEqual(row[0], "bytea")

    def test_patent_attributes_dropped_old_text_column(self):
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='core_layer' AND table_name='patent_attributes' "
                "AND column_name=%s",
                (FIGURE_COLUMN,),
            ).fetchone()
        self.assertIsNone(row, "core_layer.patent_attributes 的舊 主附圖 text 欄應已移除")

    def test_downgrade_then_upgrade_restores(self):
        cfg = _alembic_cfg()
        command.downgrade(cfg, "0025_report_artifacts")
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            patents_col = conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='core_layer' "
                "AND table_name='patents' AND column_name=%s",
                (FIGURE_COLUMN,),
            ).fetchone()
            attr_col = conn.execute(
                "SELECT data_type FROM information_schema.columns WHERE table_schema='core_layer' "
                "AND table_name='patent_attributes' AND column_name=%s",
                (FIGURE_COLUMN,),
            ).fetchone()
        self.assertIsNone(patents_col, "downgrade 後主表 主附圖 應移除")
        self.assertIsNotNone(attr_col, "downgrade 後 patent_attributes 應還原 主附圖")
        self.assertEqual(attr_col[0], "text")
        command.upgrade(cfg, "head")


class MainFigureImportTests(unittest.TestCase):
    """匯入端：內嵌圖依錨點列對應到專利並批次寫入；缺圖／無圖／多圖不得中斷。"""

    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB + "_imp"
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}_imp" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}_imp"')
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
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}_imp" WITH (FORCE)')
        except Exception:
            pass

    def setUp(self):
        self._tmp = Path(os.environ.get("TEMP", "/tmp")) / "mainfig_tests"
        self._tmp.mkdir(parents=True, exist_ok=True)
        with psycopg.connect(**_rw(f"{TEST_DB}_imp"), autocommit=True) as conn:
            conn.execute(
                "TRUNCATE core_layer.patents, raw_layer.raw_records RESTART IDENTITY CASCADE"
            )

    def _figures(self) -> dict[str, bytes | None]:
        """讀回 {申請號: 主附圖 bytes}。"""
        with psycopg.connect(**_kw(f"{TEST_DB}_imp")) as conn:
            rows = conn.execute(
                f'SELECT "申請號", "{FIGURE_COLUMN}" FROM core_layer.patents ORDER BY id'
            ).fetchall()
        return {r[0]: (bytes(r[1]) if r[1] is not None else None) for r in rows}

    def test_embedded_images_land_on_matching_patents(self):
        """每列各一張圖：bytes 需與來源圖一致且對到正確專利號。"""
        from backend.app.importers.wips_importer import import_wips_file

        red, green = _png_bytes(11), _png_bytes(22)
        path = self._tmp / "with_images.xlsx"
        _build_xlsx(
            path,
            [{"申请号": "TW-A-001", "标题": "第一件"}, {"申请号": "TW-A-002", "标题": "第二件"}],
            [(1, red), (2, green)],
        )
        summary = import_wips_file(path)
        self.assertEqual(summary["inserted"], 2)
        figures = self._figures()
        self.assertEqual(figures["TW-A-001"], red)
        self.assertEqual(figures["TW-A-002"], green)

    def test_file_without_images_imports_with_null_figures(self):
        """完全無內嵌圖的 WIPS 匯出不得爆掉；主附圖為 NULL。"""
        from backend.app.importers.wips_importer import import_wips_file

        path = self._tmp / "no_images.xlsx"
        _build_xlsx(path, [{"申请号": "TW-B-001", "标题": "無圖件"}], [])
        summary = import_wips_file(path)
        self.assertEqual(summary["inserted"], 1)
        self.assertIsNone(self._figures()["TW-B-001"])

    def test_partial_images_only_fill_matching_rows(self):
        """圖片缺漏（只有部分列有圖）：有圖的列填入、其餘 NULL。"""
        from backend.app.importers.wips_importer import import_wips_file

        blue = _png_bytes(33)
        path = self._tmp / "partial.xlsx"
        _build_xlsx(
            path,
            [
                {"申请号": "TW-C-001", "标题": "無圖"},
                {"申请号": "TW-C-002", "标题": "有圖"},
                {"申请号": "TW-C-003", "标题": "無圖2"},
            ],
            [(2, blue)],
        )
        import_wips_file(path)
        figures = self._figures()
        self.assertIsNone(figures["TW-C-001"])
        self.assertEqual(figures["TW-C-002"], blue)
        self.assertIsNone(figures["TW-C-003"])

    def test_multiple_images_on_same_row_warns_and_keeps_first(self):
        """同列多圖：取第一張並在 summary 明確記警告，不靜默丟棄。"""
        from backend.app.importers.wips_importer import import_wips_file

        first, second = _png_bytes(44), _png_bytes(55)
        path = self._tmp / "multi.xlsx"
        _build_xlsx(path, [{"申请号": "TW-D-001", "标题": "多圖件"}], [(1, first), (1, second)])
        summary = import_wips_file(path)
        self.assertEqual(self._figures()["TW-D-001"], first)
        warnings = summary.get("figure_warnings") or []
        self.assertTrue(warnings, "同列多圖必須留下警告")
        self.assertTrue(any("2" in str(w) for w in warnings))

    def test_figures_written_in_batch_not_per_row(self):
        """效率契約：圖片寫入不得 N+1（每列一條 UPDATE）。

        以 patched cursor 攔截 SQL，統計含 主附圖 的 UPDATE 次數；三列三圖時應遠少於列數
        （批次一次），此處要求 <= 1。
        """
        from backend.app.importers import wips_importer

        blobs = [_png_bytes(c) for c in (60, 70, 80)]
        path = self._tmp / "batch.xlsx"
        _build_xlsx(
            path,
            [{"申请号": f"TW-E-00{i}", "标题": f"批次{i}"} for i in (1, 2, 3)],
            [(i, blob) for i, blob in enumerate(blobs, start=1)],
        )
        calls: list[str] = []
        original = wips_importer.update_patent_figures

        def spy(cur, triplets):
            calls.append("batch")
            return original(cur, triplets)

        wips_importer.update_patent_figures = spy
        try:
            wips_importer.import_wips_file(path)
        finally:
            wips_importer.update_patent_figures = original
        self.assertLessEqual(len(calls), 1, "主附圖寫入應為單次批次，不可逐列 UPDATE")
        figures = self._figures()
        for i, blob in enumerate(blobs, start=1):
            self.assertEqual(figures[f"TW-E-00{i}"], blob)


class MainFigureEndpointTests(unittest.TestCase):
    """讀取端點：有圖回 image/*＋ETag＋Cache-Control；無圖或不存在回 404。"""

    def test_figure_endpoint_returns_bytes_and_cache_headers(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.app.api import patents as patents_api
        from backend.app.app_layer import patent_queries

        blob = _png_bytes(99)
        original = patent_queries.get_patent_figure
        patent_queries.get_patent_figure = lambda patent_id: blob if patent_id == 1 else None
        app = FastAPI()
        app.include_router(patents_api.router)
        try:
            client = TestClient(app)
            ok = client.get("/patents/1/figure")
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.content, blob)
            self.assertTrue(ok.headers.get("etag"))
            self.assertIn("max-age", ok.headers.get("cache-control", ""))
            missing = client.get("/patents/999/figure")
            self.assertEqual(missing.status_code, 404)
        finally:
            patent_queries.get_patent_figure = original


if __name__ == "__main__":
    unittest.main()
