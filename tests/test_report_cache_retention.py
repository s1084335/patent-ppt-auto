"""批 5：本機報表快取要有保留策略（2026-08-03）。

`materialize_version` 每次把一個報表版本落地到 `var/report_cache/<version>/`，
但**從來不清**——實測累積 13 個版本約 28 MB，且只會一直長。

⚠ 這與 `output/_verify/` 的教訓同源（AGENTS.md 記過：27 個目錄 90.9 MB）：
「換落點不等於解決問題，沒設保留策略就只是換個地方繼續膨脹」。

⚠ 刪快取是安全的：產物的真身在 `app_layer.report_artifacts`（DB），
本機快取只是給 CLI 讀檔用的落地副本，刪了下次 materialize 會重建。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.db import report_artifact_store as store


class PruneCacheTests(unittest.TestCase):
    def _make(self, root: Path, names: list[str]) -> None:
        for index, name in enumerate(names):
            d = root / name
            d.mkdir(parents=True)
            (d / "report_data.json").write_text("{}", encoding="utf-8")

    def test_keeps_newest_and_removes_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = [f"report_trial_2026080{i}_010101" for i in range(1, 9)]
            self._make(root, names)
            store.prune_cache(root, keep=3)
            left = sorted(d.name for d in root.iterdir() if d.is_dir())
            self.assertEqual(left, sorted(names[-3:]),
                             "保留的不是最新的三個")

    def test_keep_larger_than_count_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ["report_trial_20260801_010101", "report_trial_20260802_010101"]
            self._make(root, names)
            store.prune_cache(root, keep=5)
            self.assertEqual(len(list(root.iterdir())), 2, "版本數未超過上限卻刪了")

    def test_missing_root_is_noop(self):
        """快取目錄還不存在時不得炸——第一次執行就是這個狀態。"""
        with tempfile.TemporaryDirectory() as tmp:
            store.prune_cache(Path(tmp) / "not-there", keep=3)

    def test_files_are_left_alone(self):
        """只清版本目錄，不動同層的檔案。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, [f"report_trial_2026080{i}_010101" for i in range(1, 6)])
            (root / "note.txt").write_text("x", encoding="utf-8")
            store.prune_cache(root, keep=1)
            self.assertTrue((root / "note.txt").exists(), "把不相干的檔案刪掉了")

    def test_materialize_prunes(self):
        """落地新版本時要順手清舊的——否則策略寫了也不會生效。"""
        import inspect

        src = inspect.getsource(store.materialize_version)
        self.assertIn("prune_cache", src, "materialize_version 沒有觸發保留策略")

    def test_keep_default_is_named(self):
        self.assertTrue(hasattr(store, "REPORT_CACHE_KEEP"))
        self.assertGreaterEqual(store.REPORT_CACHE_KEEP, 3)


if __name__ == "__main__":
    unittest.main()
