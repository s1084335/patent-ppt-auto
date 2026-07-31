"""content 端點不得為「判斷檔案存在」下載整個 blob（2026-07-31 實機 12 秒根因）。

## 問題

報表種類頁展開版本後長時間「載入報表內容中…」，使用者以為前端沒接好。
實測 `/reports/versions/{v}/content` **每次 12 秒**（versions 清單只要 1.7 秒）。

分解計時：讀 report_data.json＋narratives.json 不到 1 秒——時間全花在
sections 迴圈的 `run_dir.exists(file_name)`：`_DbRunSource.exists()` 直接呼叫
`read_bytes()`，**把 30–100KB 的 SVG 完整撈回來只為了回答「在不在」**，
20+ 個變體圖檔逐一下載，RTT×blob 疊出 12 秒。

## 修法

`_DbRunSource` 首次需要時**一次撈該版本的檔名集合**（只查 filename，一趟往返），
`exists()` 查集合；`read_bytes()` 維持逐檔（真的要內容才下載）。
content 端點從 ~12s 降到 ~1s（兩檔讀＋一次清單查）。
"""
from __future__ import annotations

import unittest
from unittest import mock

from backend.app import main as app_main


class DbRunSourceExistsTests(unittest.TestCase):
    def _source(self, filenames, blobs):
        src = app_main._DbRunSource("vtest")
        calls = {"names": 0, "reads": []}

        def fake_names(version):
            calls["names"] += 1
            return set(filenames)

        def fake_read(version, filename):
            calls["reads"].append(filename)
            return blobs.get(filename)

        return src, calls, fake_names, fake_read

    def test_exists_uses_filename_set_not_blob(self):
        src, calls, fake_names, fake_read = self._source(
            {"a.svg", "b.svg", "report_data.json"}, {})
        with mock.patch.object(app_main, "_db_list_filenames", fake_names), \
             mock.patch.object(app_main, "_db_read_artifact", fake_read):
            self.assertTrue(src.exists("a.svg"))
            self.assertTrue(src.exists("b.svg"))
            self.assertFalse(src.exists("missing.svg"))
        self.assertEqual(calls["reads"], [],
                         "exists() 仍在下載 blob——12 秒問題沒修")
        self.assertEqual(calls["names"], 1, "檔名清單應只查一次（同版本快取）")

    def test_read_bytes_still_fetches_content(self):
        src, calls, fake_names, fake_read = self._source(
            {"report_data.json"}, {"report_data.json": b"{}"})
        with mock.patch.object(app_main, "_db_list_filenames", fake_names), \
             mock.patch.object(app_main, "_db_read_artifact", fake_read):
            self.assertEqual(src.read_bytes("report_data.json"), b"{}")
        self.assertEqual(calls["reads"], ["report_data.json"])

    def test_read_bytes_skips_fetch_for_known_missing(self):
        """⚠ 清單已知不存在的檔，read_bytes 不必再打 DB。"""
        src, calls, fake_names, fake_read = self._source({"a.svg"}, {})
        with mock.patch.object(app_main, "_db_list_filenames", fake_names), \
             mock.patch.object(app_main, "_db_read_artifact", fake_read):
            self.assertIsNone(src.read_bytes("nope.json"))
        self.assertEqual(calls["reads"], [], "已知缺檔仍打 DB")


if __name__ == "__main__":
    unittest.main()
