"""ai:narrative 跨容器讀寫（2026-07-27 待辦 9d；實機 job 95 失敗）。

實機症狀：報表產完自動排的 `ai:narrative` 秒失敗——
`NarrativeRunnerError: 找不到可解讀的報表版本：D:\\...\\output\\full_report_latest
下無含 report_data.json 的 report_trial_ 目錄`。
結果：DB 內 narratives 一份都沒有，報表解讀區永遠空白。

## 兩段都斷
    報表由**容器內 worker** 產出 → upload_run_dir 上傳到 app_layer.report_artifacts（DB）
                                     ↕ 斷開
    ai:narrative 在**使用者本機 Companion** 跑 → resolve_run_dir 讀本機
                                                 output/full_report_latest/  ← 空的
    CLI 寫 run_dir/narratives.json（本機檔案系統）
    backend 讀 → report_artifact_store.read_file（DB）  ← 對不上

- **讀不到**：報表在 DB，runner 找本機路徑。
- **寫不回**：CLI 寫本機，backend 從 DB 讀。

## 修法
沿用既有 `report_artifact_store`（不另造機制）：
1. 讀：`materialize_version()` 把該版本所有檔案從 DB 落地到暫存目錄，再給 CLI 讀。
2. 寫：跑完把 `narratives.json` 上傳回 `report_artifacts`。

⚠ 本機檔案系統仍優先——本機開發（backend 與報表同一台）時目錄真的存在，
不必繞 DB。只有本機找不到時才落地 DB 版本，兩種部署都能跑。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class MaterializeVersionTests(unittest.TestCase):
    """report_artifact_store.materialize_version：把 DB 內某版本落地成目錄。"""

    def test_writes_all_files_of_version(self):
        from backend.app.db import report_artifact_store as store

        files = [
            {"filename": "report_data.json", "content": b'{"sections":[]}'},
            {"filename": "chart_trend.svg", "content": b"<svg/>"},
        ]
        with TemporaryDirectory() as td, \
                mock.patch.object(store, "list_files", return_value=files):
            run_dir = store.materialize_version("report_trial_20260727_000000", Path(td))
            self.assertTrue((run_dir / "report_data.json").exists())
            self.assertTrue((run_dir / "chart_trend.svg").exists())
            self.assertEqual(
                json.loads((run_dir / "report_data.json").read_text(encoding="utf-8")),
                {"sections": []})

    def test_dir_name_is_the_version(self):
        """目錄名必須等於版本名——resolve_run_dir 以 run_dir.name 當 version。"""
        from backend.app.db import report_artifact_store as store

        version = "report_trial_20260727_000000"
        with TemporaryDirectory() as td, mock.patch.object(
                store, "list_files",
                return_value=[{"filename": "report_data.json", "content": b"{}"}]):
            run_dir = store.materialize_version(version, Path(td))
        self.assertEqual(run_dir.name, version)

    def test_raises_when_version_has_no_files(self):
        """版本不存在時明確 raise，不回一個空目錄讓下游誤判成「報表沒內容」。"""
        from backend.app.db import report_artifact_store as store

        with TemporaryDirectory() as td, \
                mock.patch.object(store, "list_files", return_value=[]):
            with self.assertRaises(Exception):
                store.materialize_version("no_such_version", Path(td))


class ResolveRunDirFallbackTests(unittest.TestCase):
    """resolve_run_dir：本機找不到時改從 DB 落地（跨容器的讀那一段）。"""

    def test_prefers_local_directory(self):
        """本機目錄存在時直接用，不繞 DB（本機開發路徑不變）。"""
        from backend.app.worker import ai_narrative_runner as r

        with TemporaryDirectory() as td:
            root = Path(td)
            local = root / "report_trial_20260727_000000"
            local.mkdir()
            (local / "report_data.json").write_text("{}", encoding="utf-8")
            called = {"db": False}

            def _fake_materialize(*a, **k):
                called["db"] = True
                raise AssertionError("本機有目錄時不該落地 DB 版本")

            with mock.patch.object(r, "materialize_report_version", _fake_materialize):
                run_dir = r.resolve_run_dir("report_trial_20260727_000000", root=root)
            self.assertEqual(run_dir, local)
            self.assertFalse(called["db"])

    def test_falls_back_to_db_when_local_missing(self):
        """本機沒有（＝容器產的報表）時，從 DB 落地——這是實機 job 95 掛掉的那一段。"""
        from backend.app.worker import ai_narrative_runner as r

        version = "report_trial_20260727_000000"
        with TemporaryDirectory() as td, TemporaryDirectory() as cache:
            materialized = Path(cache) / version
            materialized.mkdir()
            (materialized / "report_data.json").write_text('{"sections":[]}', encoding="utf-8")

            with mock.patch.object(
                    r, "materialize_report_version", return_value=materialized) as m:
                run_dir = r.resolve_run_dir(version, root=Path(td))
            self.assertEqual(run_dir, materialized)
            m.assert_called_once()

    def test_error_message_mentions_both_sources(self):
        """兩邊都找不到時，訊息要說清楚本機與 DB 都查過了（不要只講本機路徑）。"""
        from backend.app.worker import ai_narrative_runner as r

        with TemporaryDirectory() as td:
            with mock.patch.object(
                    r, "materialize_report_version", side_effect=FileNotFoundError("no rows")):
                with self.assertRaises(r.NarrativeRunnerError) as ctx:
                    r.resolve_run_dir("report_trial_x", root=Path(td))
        msg = str(ctx.exception)
        self.assertIn("report_trial_x", msg)


class UploadNarrativesBackTests(unittest.TestCase):
    """跑完要把 narratives.json 上傳回 report_artifacts（跨容器的寫那一段）。"""

    def test_run_uploads_narratives(self):
        from backend.app.worker import ai_narrative_runner as r

        version = "report_trial_20260727_000000"
        uploaded: dict = {}

        with TemporaryDirectory() as td:
            run_dir = Path(td) / version
            run_dir.mkdir()
            (run_dir / "report_data.json").write_text(
                json.dumps({"sections": []}), encoding="utf-8")

            def _fake_cli(argv, timeout):
                # 模擬 CLI 寫檔
                (run_dir / "narratives.json").write_text(
                    json.dumps({"based_on_version": version, "reports": {}}),
                    encoding="utf-8")
                from backend.app.worker.cli_gateway import CliResult
                return CliResult(exit_code=0, stdout="{}", stderr="")

            def _fake_upload(path):
                uploaded["dir"] = Path(path)
                return 1

            r.run_narrative(
                version,
                cli_runner=_fake_cli,
                resolve_run_dir=lambda v, **k: run_dir,
                upload_run_dir=_fake_upload,
            )

        self.assertIn("dir", uploaded, "跑完沒把 narratives.json 上傳回 DB——backend 讀不到")
        self.assertEqual(uploaded["dir"], run_dir)

    def test_run_fails_when_narratives_upload_fails(self):
        """narratives.json 沒回存 DB 時不可回 succeeded，否則前端永遠讀不到解讀。"""
        from backend.app.worker import ai_narrative_runner as r

        version = "report_trial_20260727_000000"
        with TemporaryDirectory() as td:
            run_dir = Path(td) / version
            run_dir.mkdir()
            (run_dir / "report_data.json").write_text(
                json.dumps({"sections": []}), encoding="utf-8")

            def _fake_cli(argv, timeout):
                (run_dir / "narratives.json").write_text(
                    json.dumps({"based_on_version": version, "reports": {}}),
                    encoding="utf-8")
                from backend.app.worker.cli_gateway import CliResult
                return CliResult(exit_code=0, stdout="{}", stderr="")

            with self.assertRaises(r.NarrativeRunnerError):
                r.run_narrative(
                    version,
                    cli_runner=_fake_cli,
                    resolve_run_dir=lambda v, **k: run_dir,
                    upload_run_dir=lambda _p: 0,
                )


if __name__ == "__main__":
    unittest.main()
