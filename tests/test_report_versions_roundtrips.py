"""`/reports/versions?workspace_id=` 的 DB 往返次數不得隨版本數成長。

2026-08-17 實測：48 個版本 × 每版 2 趟（先查檔名集合、再讀 version_meta.json）
＝ 97 趟；Supabase pooler 單趟 ~44ms → **開報表頁要等 4.3 秒**，而且每產一份
報表就再慢 0.09 秒。過濾又發生在 `limit` **之前**，所以 limit 幫不上忙。

⚠ 判準是恆等式（往返次數與版本數無關），不是「程式裡有沒有出現 hint 這個字」
——後者是代理指標，改個變數名就失效，而且不保證真的少打 DB。
"""
from __future__ import annotations

import unittest
from unittest import mock

from backend.app import main as app_main
from backend.app.db import report_artifact_store


class _FakeCursor:
    """回傳 (version, has_narratives, meta_bytes) 三元組，並記錄 execute 次數。"""

    def __init__(self, rows, counter):
        self._rows = rows
        self._counter = counter

    def execute(self, *_args, **_kwargs):
        self._counter["execute"] += 1

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeConn:
    def __init__(self, rows, counter):
        self._rows, self._counter = rows, counter

    def cursor(self, *_a, **_kw):
        return _FakeCursor(self._rows, self._counter)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakePool:
    def __init__(self, rows, counter):
        self._rows, self._counter = rows, counter

    def connection(self):
        return _FakeConn(self._rows, self._counter)


def _db_rows(n: int) -> list[tuple]:
    """⚠ 版本名必須唯一：`_list_run_sources` 用 dict 去重，撞名會讓 n 縮水。"""
    return [(f"report_trial_20260817_{i:06d}", False, b'{"workspace_id": 3}')
            for i in range(n)]


class VersionListRoundtripTests(unittest.TestCase):
    """把 DB 存取全部換成計數樁，只看「打了幾次」。"""

    def _run(self, n: int) -> tuple[int, int]:
        """跑**真正的** list_versions＋list_report_versions，只把 DB 換掉。

        ⚠ 不假造 `list_versions` 的回傳——假了就只驗到 main 這一端，
        store 少回一個欄位時測試照樣綠（第一版就是這樣寫，抓不到）。
        """
        calls = {"list_filenames": 0, "read_artifact": 0, "execute": 0}

        def fake_list_filenames(version):
            calls["list_filenames"] += 1
            return {"report_data.json", "version_meta.json"}

        def fake_read_artifact(version, filename):
            calls["read_artifact"] += 1
            return b'{"workspace_id": 3}' if filename == "version_meta.json" else None

        with mock.patch.object(app_main, "_run_dirs", return_value=[]), \
             mock.patch.object(report_artifact_store, "get_pool",
                               return_value=_FakePool(_db_rows(n), calls)), \
             mock.patch.object(app_main, "_db_list_filenames", fake_list_filenames), \
             mock.patch.object(app_main, "_db_read_artifact", fake_read_artifact):
            result = app_main.list_report_versions(limit=5, workspace_id=3)

        self.assertEqual(len(result["versions"]), 5, "limit 應截到 5 筆")
        self.assertEqual(result["total"], n, "total 應為過濾後總數")
        self.assertEqual(calls["execute"], 1, "版本清單應只有一趟聚合查詢")
        return calls["list_filenames"], calls["read_artifact"]

    def test_roundtrips_do_not_grow_with_version_count(self):
        small = sum(self._run(5))
        large = sum(self._run(50))
        self.assertEqual(
            small, large,
            f"往返次數隨版本數成長：5 版 {small} 趟、50 版 {large} 趟")

    def test_roundtrips_are_constant_and_small(self):
        """一趟聚合查詢就該拿到 workspace 歸屬——per-version 存取次數為 0。"""
        names, reads = self._run(50)
        self.assertEqual((names, reads), (0, 0),
                         f"仍有 per-version DB 存取：檔名 {names} 次、讀檔 {reads} 次")


class WorkspaceHintTests(unittest.TestCase):
    def test_missing_meta_still_unassigned(self):
        """⚠ 行為不得變：沒有 version_meta.json 的舊版本仍不歸屬任何 workspace。"""
        # meta 為 NULL＝該版本沒有 version_meta.json（舊版本）。
        rows = [("old_20260101_000000", False, None)]
        with mock.patch.object(app_main, "_run_dirs", return_value=[]), \
             mock.patch.object(report_artifact_store, "get_pool",
                               return_value=_FakePool(rows, {"execute": 0})), \
             mock.patch.object(app_main, "_db_list_filenames",
                               return_value={"report_data.json"}), \
             mock.patch.object(app_main, "_db_read_artifact", return_value=None):
            filtered = app_main.list_report_versions(workspace_id=3)
            unfiltered = app_main.list_report_versions()
        self.assertEqual(filtered["total"], 0, "無 meta 的版本不該被歸進某 workspace")
        self.assertEqual(unfiltered["total"], 1, "不帶過濾時仍要列出")


if __name__ == "__main__":
    unittest.main()
