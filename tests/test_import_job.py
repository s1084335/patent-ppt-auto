"""patent_import handler 完整性驗證、import_wips_file 重複檔/rollback、與統計語意測試。

全程不連真實 DB：handler 測試 mock import_wips_file；importer 重複檔/rollback 測試 mock
psycopg.connect；統計語意測試 mock find_existing_patent_id 與 cursor。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.importers import wips_importer
from backend.app.importers.wips_importer import file_sha256, import_wips_file
from backend.app.worker import handlers


def _write_min_csv(dir_path: Path) -> Path:
    """寫一個最小可解析的 WIPS CSV（含識別欄），讓 importer 走到 DB 段。"""
    p = dir_path / "min.csv"
    p.write_text("申请号,标题,申请日\nTW123456,測試,2020-01-01\n", encoding="utf-8")
    return p


def _mock_conn(*, fetchone_return=None, fetchone_side_effect=None):
    """組一個 with-context 相容的假 psycopg connection/cursor（__exit__ 不吞例外）。"""
    fake_cur = mock.MagicMock()
    if fetchone_side_effect is not None:
        fake_cur.fetchone.side_effect = fetchone_side_effect
    else:
        fake_cur.fetchone.return_value = fetchone_return

    fake_conn = mock.MagicMock()
    cur_cm = fake_conn.cursor.return_value
    cur_cm.__enter__.return_value = fake_cur
    cur_cm.__exit__.return_value = False

    connect_cm = mock.MagicMock()
    connect_cm.__enter__.return_value = fake_conn
    connect_cm.__exit__.return_value = False
    return connect_cm, fake_conn, fake_cur


class PatentImportHandlerTests(unittest.TestCase):
    """匯入前完整性驗證：位於 root、存在、白名單副檔名、hash 一致；失敗即 job failed。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"IMPORTS_ROOT": str(self._root)})
        self._env.start()
        # 在 root 下建一份合法上傳副本。
        self._upload_dir = self._root / "uuid123"
        self._upload_dir.mkdir(parents=True)
        self._file = _write_min_csv(self._upload_dir)
        self._hash = file_sha256(self._file)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _payload(self, **over):
        p = {"path": str(self._file), "file_hash": self._hash}
        p.update(over)
        return p

    def test_registered_in_dispatch(self):
        self.assertIn("patent_import", handlers.HANDLERS)

    def test_requires_path(self):
        with self.assertRaises(ValueError):
            handlers.handle_patent_import({}, mock.MagicMock())

    def test_rejects_path_escape(self):
        """path 不在 imports root → ValueError，不進 importer。"""
        outside = Path(tempfile.gettempdir()) / "escape.csv"
        with mock.patch.object(handlers, "import_wips_file") as m:
            with self.assertRaises(ValueError):
                handlers.handle_patent_import(self._payload(path=str(outside)), mock.MagicMock())
        m.assert_not_called()

    def test_rejects_missing_file(self):
        """path 在 root 內但檔案不存在 → ValueError。"""
        missing = self._root / "uuidX" / "nope.csv"
        with mock.patch.object(handlers, "import_wips_file") as m:
            with self.assertRaises(ValueError):
                handlers.handle_patent_import(self._payload(path=str(missing)), mock.MagicMock())
        m.assert_not_called()

    def test_rejects_hash_mismatch(self):
        """實際 SHA-256 不等於 payload.file_hash → ValueError。"""
        with mock.patch.object(handlers, "import_wips_file") as m:
            with self.assertRaises(ValueError):
                handlers.handle_patent_import(self._payload(file_hash="deadbeef"), mock.MagicMock())
        m.assert_not_called()

    def test_duplicate_removes_upload_dir(self):
        """重複檔 → 安全刪除本次上傳目錄。"""
        with mock.patch.object(handlers, "import_wips_file", return_value={"status": "skipped_duplicate_file"}):
            result = handlers.handle_patent_import(self._payload(), mock.MagicMock())
        self.assertEqual(result["status"], "skipped_duplicate_file")
        self.assertFalse(self._upload_dir.exists())

    def test_success_keeps_upload_dir(self):
        """成功匯入 → 保留來源檔（source_files.file_path 需追溯）。"""
        summary = {"status": "imported", "records": 1, "inserted": 1, "matched_existing": 0, "updated": 0, "skipped": 0}
        with mock.patch.object(handlers, "import_wips_file", return_value=summary):
            result = handlers.handle_patent_import(self._payload(), mock.MagicMock())
        self.assertEqual(result["status"], "imported")
        self.assertTrue(self._file.exists())


class ImportWipsFileTests(unittest.TestCase):
    def test_duplicate_file_skipped(self):
        """同 hash 檔已存在 → skipped_duplicate_file，skipped=records，且不 commit。"""
        with tempfile.TemporaryDirectory() as d:
            path = _write_min_csv(Path(d))
            connect_cm, fake_conn, _ = _mock_conn(fetchone_return=(1,))
            with mock.patch("psycopg.connect", return_value=connect_cm):
                summary = import_wips_file(path)
        self.assertEqual(summary["status"], "skipped_duplicate_file")
        self.assertEqual((summary["inserted"], summary["matched_existing"], summary["updated"]), (0, 0, 0))
        self.assertEqual(summary["skipped"], summary["records"])
        fake_conn.commit.assert_not_called()

    def test_error_rolls_back_no_commit(self):
        """匯入途中拋錯 → 例外外拋且整批未 commit（單一 transaction rollback）。"""
        with tempfile.TemporaryDirectory() as d:
            path = _write_min_csv(Path(d))
            connect_cm, fake_conn, _ = _mock_conn(fetchone_side_effect=[None, (1,), RuntimeError("db boom")])
            with mock.patch("psycopg.connect", return_value=connect_cm):
                with self.assertRaises(RuntimeError):
                    import_wips_file(path)
        fake_conn.commit.assert_not_called()


class UpsertStatsTests(unittest.TestCase):
    """統計語意：inserted / matched_existing / updated 落點正確，no-op 不算 updated。"""

    def _cur(self, *, rowcount=0, fetchone=None):
        cur = mock.MagicMock()
        cur.rowcount = rowcount
        cur.fetchone.return_value = fetchone
        return cur

    def test_insert_counts_inserted(self):
        stats = {"inserted": 0, "matched_existing": 0, "updated": 0}
        cur = self._cur(fetchone=(7,))
        with mock.patch.object(wips_importer, "find_existing_patent_id", return_value=None):
            pid = wips_importer.upsert_patent(cur, {}, raw_record_id=1, stats=stats)
        self.assertEqual(pid, 7)
        self.assertEqual(stats, {"inserted": 1, "matched_existing": 0, "updated": 0})

    def test_matched_no_op_not_updated(self):
        """識別號命中但 UPDATE guard 命中 0 列（無 NULL 可補）→ matched_existing 不算 updated。"""
        stats = {"inserted": 0, "matched_existing": 0, "updated": 0}
        cur = self._cur(rowcount=0)
        with mock.patch.object(wips_importer, "find_existing_patent_id", return_value=42):
            pid = wips_importer.upsert_patent(cur, {}, raw_record_id=1, stats=stats)
        self.assertEqual(pid, 42)
        self.assertEqual(stats, {"inserted": 0, "matched_existing": 1, "updated": 0})

    def test_null_fill_counts_updated(self):
        """既有欄由 NULL 補成非 NULL（rowcount>0）→ 才算 updated。"""
        stats = {"inserted": 0, "matched_existing": 0, "updated": 0}
        cur = self._cur(rowcount=1)
        with mock.patch.object(wips_importer, "find_existing_patent_id", return_value=42):
            pid = wips_importer.upsert_patent(cur, {}, raw_record_id=1, stats=stats)
        self.assertEqual(pid, 42)
        self.assertEqual(stats, {"inserted": 0, "matched_existing": 1, "updated": 1})


class FindExistingPatentIdTests(unittest.TestCase):
    """驗證實際專利號去重機制：依 授權公告號→審查公告號→未審查公開號(轉換後)→申請號(轉換後) 順序查找，不用 dedupe_key。"""

    def test_grant_number_hits_first(self):
        """有授權公告號時第一個查找即命中，回既有 patent_id。"""
        cur = mock.MagicMock()
        cur.fetchone.return_value = (55,)
        pid = wips_importer.find_existing_patent_id(cur, {"授權公告號": "TWI123"})
        self.assertEqual(pid, 55)
        self.assertIn('"授權公告號"', cur.execute.call_args_list[0][0][0])

    def test_falls_through_to_transformed_application_number(self):
        """無公告/公開號、僅有申請號(轉換後)時，落到申請號查找。"""
        cur = mock.MagicMock()
        cur.fetchone.return_value = (77,)
        pid = wips_importer.find_existing_patent_id(cur, {"申請號(轉換後)": "TW109123"})
        self.assertEqual(pid, 77)
        self.assertTrue(any("申請號(轉換後)" in c[0][0] for c in cur.execute.call_args_list))

    def test_no_identifier_returns_none(self):
        """無任何專利號 → 回 None（各自新建、不合併，靠 raw_record_id 追溯）。"""
        cur = mock.MagicMock()
        cur.fetchone.return_value = None
        self.assertIsNone(wips_importer.find_existing_patent_id(cur, {}))


if __name__ == "__main__":
    unittest.main()
