"""patent_import handler 完整性驗證、import_wips_file 重複檔/rollback、與統計語意測試。

全程不連真實 DB：handler 測試 mock import_wips_file 與 import_blob_store；importer
重複檔/rollback 測試 mock psycopg.connect；統計語意測試 mock find_existing_patent_id 與 cursor。
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.importers import wips_importer
from backend.app.importers.wips_importer import import_wips_file
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
    """匯入前完整性驗證：blob_id／file_hash 必填、白名單副檔名、hash 一致；失敗即 job failed。

    2026-07-23 起來源檔由 DB blob 取得（backend 與 worker 在 Railway 是不同容器、檔案系統
    不共享），payload 帶 blob_id 而非 path；blob 取回時驗 SHA-256，匯入完即刪 blob。
    """

    def setUp(self):
        self._content = "申请号,标题,申请日\nTW123456,測試,2020-01-01\n".encode("utf-8")
        self._hash = hashlib.sha256(self._content).hexdigest()

    def _payload(self, **over):
        p = {"blob_id": 5, "original_filename": "min.csv", "file_hash": self._hash}
        p.update(over)
        return p

    def _fake_write(self, blob_id, target, *, expected_hash):
        """模擬 blob 取回：寫出內容並比對 hash（與真實 store 同語意）。"""
        if expected_hash != self._hash:
            raise ValueError("import blob hash mismatch")
        Path(target).write_bytes(self._content)

    def test_registered_in_dispatch(self):
        self.assertIn("patent_import", handlers.HANDLERS)

    def test_requires_blob_id(self):
        with self.assertRaises(ValueError):
            handlers.handle_patent_import({}, mock.MagicMock())

    def test_requires_file_hash(self):
        """缺 file_hash → ValueError，不取 blob（無從驗證完整性）。"""
        payload = self._payload()
        payload.pop("file_hash")
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path") as w:
            with self.assertRaises(ValueError):
                handlers.handle_patent_import(payload, mock.MagicMock())
        w.assert_not_called()

    def test_rejects_unsupported_suffix(self):
        """original_filename 副檔名不在 Web 白名單 → ValueError，不取 blob、不進 importer。"""
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path") as w, \
             mock.patch.object(handlers, "import_wips_file") as m:
            with self.assertRaises(ValueError):
                handlers.handle_patent_import(
                    self._payload(original_filename="escape.pdf"), mock.MagicMock())
        w.assert_not_called()
        m.assert_not_called()

    def test_rejects_hash_mismatch(self):
        """blob 內容 SHA-256 不等於 payload.file_hash → ValueError，不進 importer。"""
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path",
                               side_effect=self._fake_write), \
             mock.patch.object(handlers.import_blob_store, "delete_blob") as d, \
             mock.patch.object(handlers, "import_wips_file") as m:
            with self.assertRaises(ValueError):
                handlers.handle_patent_import(self._payload(file_hash="deadbeef"), mock.MagicMock())
        m.assert_not_called()
        # 匯入失敗保留 blob，讓 job 重試可再取同一份內容。
        d.assert_not_called()

    def test_duplicate_deletes_blob(self):
        """重複檔 → blob 一樣清除（內容已無保存價值）。"""
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path",
                               side_effect=self._fake_write), \
             mock.patch.object(handlers.import_blob_store, "delete_blob") as d, \
             mock.patch.object(handlers, "import_wips_file",
                               return_value={"status": "skipped_duplicate_file"}):
            result = handlers.handle_patent_import(self._payload(), mock.MagicMock())
        self.assertEqual(result["status"], "skipped_duplicate_file")
        d.assert_called_once_with(5)

    def test_success_deletes_blob_and_temp(self):
        """成功匯入 → blob 刪除，暫存檔不殘留（追溯靠 raw_records.source_file_hash）。"""
        summary = {"status": "imported", "records": 1, "inserted": 1,
                   "matched_existing": 0, "updated": 0, "skipped": 0}
        seen = {}

        def fake_import(path, *a, **kw):
            seen["path"] = Path(path)
            seen["content"] = Path(path).read_bytes()
            return summary

        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path",
                               side_effect=self._fake_write), \
             mock.patch.object(handlers.import_blob_store, "delete_blob") as d, \
             mock.patch.object(handlers, "import_wips_file", side_effect=fake_import):
            result = handlers.handle_patent_import(self._payload(), mock.MagicMock())
        self.assertEqual(result["status"], "imported")
        # importer 收到的是內容完整的暫存檔，且副檔名保留供選 parser。
        self.assertEqual(seen["content"], self._content)
        self.assertEqual(seen["path"].suffix, ".csv")
        self.assertFalse(seen["path"].exists())
        d.assert_called_once_with(5)


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
