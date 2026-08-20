"""報表產物跨容器共享驗收：report_generate 真的出圖、產物落 DB、讀取端取得回。

背景（2026-07-23）：
1. handle_report_generate 原本只呼叫 run_reports_batch（純查詢、回 rows），不產 SVG／
   report_data.json／index.html，導致 /report-latest/content 一律 404、匯出工作台空白、
   AI 解讀（需 run_dir 實體檔）與 PPT 產生器（需 report_data.json）全部跑不了。
2. Railway 上 worker 與 backend 是**不同容器**、檔案系統不共享（同 import_blobs 的情境），
   worker 寫的 output/full_report_latest/<版本>/ backend 讀不到。

本檔分三段：
1. ReportArtifactStoreTests：store 以 mock psycopg 驗 SQL 契約（逐檔上傳、單檔取回、列版本）。
2. ReportGenerateHandlerTests：handler 呼叫 run_chart_trial（含 cluster_data 解析、無分群
   優雅跳過）、階段百分比遞增收 100、產物上傳。
3. CrossContainerReadTests：模擬「寫入端與讀取端不共享檔案系統」，驗證 API 仍讀得到。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.db import report_artifact_store
from backend.app.main import app
from backend.app.worker import handlers
from backend.app.worker.job_context import JobContext
from backend.app.worker.queue_client import ProcessingJob


client = TestClient(app)


def _mock_pool(*, fetchone_returns=None, fetchall_returns=None):
    """組一個 get_pool() 相容的假連線池，回傳 (pool, cursor)（同 import_blob_store 測試做法）。"""
    cur = mock.MagicMock()
    if fetchone_returns is not None:
        cur.fetchone.side_effect = list(fetchone_returns)
    if fetchall_returns is not None:
        cur.fetchall.side_effect = list(fetchall_returns)
    cur_cm = mock.MagicMock()
    cur_cm.__enter__.return_value = cur
    cur_cm.__exit__.return_value = False

    conn = mock.MagicMock()
    conn.cursor.return_value = cur_cm
    conn_cm = mock.MagicMock()
    conn_cm.__enter__.return_value = conn
    conn_cm.__exit__.return_value = False

    pool = mock.MagicMock()
    pool.connection.return_value = conn_cm
    return pool, cur


class ReportArtifactStoreTests(unittest.TestCase):
    """store 契約：逐檔 upsert、單檔取回、列版本、讀 report_data.json。"""

    def test_upload_run_dir_writes_one_row_per_file(self):
        """整個報表版本目錄逐檔落 DB：一檔一列，帶版本、檔名、內容與 sha256。"""
        pool, cur = _mock_pool()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "report_trial_20260723_120000"
            run_dir.mkdir()
            (run_dir / "report_data.json").write_text('{"sections": []}', encoding="utf-8")
            (run_dir / "a.svg").write_text("<svg/>", encoding="utf-8")
            (run_dir / "index.html").write_text("<html></html>", encoding="utf-8")
            with mock.patch.object(report_artifact_store, "get_pool", return_value=pool):
                uploaded = report_artifact_store.upload_run_dir(run_dir)
        self.assertEqual(uploaded, 3)
        # 每檔一次 INSERT，且帶版本名與檔名
        inserted = [c.args for c in cur.execute.call_args_list if "INSERT" in c.args[0]]
        self.assertEqual(len(inserted), 3)
        versions = {params[0] for _sql, params in inserted}
        self.assertEqual(versions, {"report_trial_20260723_120000"})
        filenames = {params[1] for _sql, params in inserted}
        self.assertEqual(filenames, {"report_data.json", "a.svg", "index.html"})
        # 內容 hash 落款供完整性追溯
        by_name = {params[1]: params for _sql, params in inserted}
        self.assertEqual(
            by_name["a.svg"][3], hashlib.sha256("<svg/>".encode("utf-8")).hexdigest()
        )

    def test_read_file_fetches_single_artifact_not_whole_version(self):
        """單檔取回只撈那一列——asset 端點不得為了一張 SVG 把整版產物撈回。"""
        pool, cur = _mock_pool(fetchone_returns=[(b"<svg/>",)])
        with mock.patch.object(report_artifact_store, "get_pool", return_value=pool):
            content = report_artifact_store.read_file("v1", "a.svg")
        self.assertEqual(content, b"<svg/>")
        sql, params = cur.execute.call_args.args
        self.assertIn("filename = %s", sql)
        self.assertEqual(params, ("v1", "a.svg"))

    def test_read_file_missing_returns_none(self):
        """檔案不存在回 None（呼叫端才能明確回 404，不猜路徑）。"""
        pool, _cur = _mock_pool(fetchone_returns=[None])
        with mock.patch.object(report_artifact_store, "get_pool", return_value=pool):
            self.assertIsNone(report_artifact_store.read_file("v1", "nope.svg"))

    def test_list_versions_does_not_read_content(self):
        """列版本只取 metadata，不得把 content 撈進來（版本一多也不會慢）。"""
        # ⚠ 三欄（version／has_narratives／meta）——03dd621「版本清單去 N+1」把
        #   workspace 歸屬併進同一次查詢，SQL 從兩欄變三欄卻沒同步這裡的假回傳，
        #   於是 `row[2]` IndexError。假資料的欄數就是契約，少一欄等於契約沒對上。
        pool, cur = _mock_pool(fetchall_returns=[[
            ("report_trial_20260723_120000", True, b'{"workspace_id": 3}'),
            ("v0", False, None),
        ]])
        with mock.patch.object(report_artifact_store, "get_pool", return_value=pool):
            versions = report_artifact_store.list_versions()
        self.assertEqual(versions[0]["version"], "report_trial_20260723_120000")
        self.assertTrue(versions[0]["has_narratives"])
        self.assertEqual(versions[0]["workspace_id"], 3)
        # 沒有 meta 的舊版本＝不歸屬任何 workspace，不得變成 0 或 KeyError
        self.assertIsNone(versions[1]["workspace_id"])
        sql = cur.execute.call_args.args[0]
        # ⚠ 斷言從「SQL 不准出現 content」收緊成真正的不變量。
        #   原本是字面禁令，而 03dd621 去 N+1 時要把 version_meta.json（幾百
        #   bytes）併進同一次查詢——那正是這條規則想達成的「版本一多也不會慢」，
        #   卻被字面禁令擋下。字面禁令與它想守的東西不是同一件事。
        #   真正要守的是：**不得撈大檔**（report_data.json／narratives.json 的
        #   blob）。所以先把「限定 version_meta.json 的那個 content 取用」拿掉，
        #   再要求剩下的 SQL 完全不碰 content。
        stripped = re.sub(
            r"array_agg\(content\)\s*filter\s*\(\s*where\s+filename\s*=\s*"
            r"'version_meta\.json'\s*\)",
            "", sql.lower(), flags=re.S)
        self.assertNotIn(
            "content", stripped.replace("content_", ""),
            "除了 version_meta.json 以外還撈了 content——版本一多就會慢")

    def test_list_ppt_files_returns_pptx_of_version_without_content(self):
        """列某報表版本下的 .pptx 清單（#10 R10-1）：只回 metadata（filename／byte_size），
        不撈 content；只取 .pptx（同版本可有多個 _rN 序號檔）；限定該 version。"""
        pool, cur = _mock_pool(fetchall_returns=[[
            ("patent_report.pptx", 1024),
            ("patent_report_r2.pptx", 2048),
        ]])
        with mock.patch.object(report_artifact_store, "get_pool", return_value=pool):
            ppts = report_artifact_store.list_ppt_files("report_trial_20260723_120000")
        self.assertEqual([p["filename"] for p in ppts],
                         ["patent_report.pptx", "patent_report_r2.pptx"])
        self.assertEqual(ppts[0]["byte_size"], 1024)
        sql, params = cur.execute.call_args.args
        # 限定該 version
        self.assertEqual(params, ("report_trial_20260723_120000",))
        # 只取 .pptx（SQL 需帶 pptx 過濾）、不撈 content
        self.assertIn(".pptx", sql)
        self.assertNotIn("content", sql.lower().replace("content_", ""))


class ReportGenerateHandlerTests(unittest.TestCase):
    """handle_report_generate 真的出圖：呼叫 run_chart_trial、上傳產物、階段收 100。"""

    class _Store:
        """記錄 heartbeat 呼叫；不碰資料庫。"""

        def __init__(self):
            self.heartbeats: list[tuple[str | None, int | None]] = []

        def heartbeat(self, *, job_id, worker_id, current_stage=None, progress_percent=None):
            self.heartbeats.append((current_stage, progress_percent))

        def is_cancelled(self, *, job_id):
            return False

    def _context(self, store, payload, *, workspace_id=None):
        job = ProcessingJob(
            job_id=99,
            job_type="report_generate",
            status="running",
            workspace_id=workspace_id,
            payload_json=payload,
            result_json=None,
            progress_percent=0,
            current_stage="queued",
            attempt_count=1,
            max_attempts=3,
        )
        return JobContext(job=job, worker_id="worker-report", store=store)

    def _run(self, payload, *, workspace_id=None, cluster_data=None, cluster_error=None):
        """跑 handler，run_chart_trial 與 cluster loader 都替身；回 (result, captured, store)。"""
        captured: dict = {}
        store = self._Store()
        context = self._context(store, payload, workspace_id=workspace_id)

        def _fake_run_chart_trial(**kwargs):
            captured["chart_kwargs"] = kwargs
            run_dir = Path(self.tmp) / "report_trial_20260723_130000"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "report_data.json").write_text(
                json.dumps({"sections": [{"title": "t"}]}), encoding="utf-8"
            )
            (run_dir / "a.svg").write_text("<svg/>", encoding="utf-8")
            return {
                "status": "ok",
                "output_dir": str(run_dir),
                "version": run_dir.name,
                "files": ["report_data.json", "a.svg"],
                "sections_rendered": ["annual_trend"],
            }

        def _fake_load_cluster(workspace_id, source_field, pain_data=None,
                               report_scope="company"):
            # ⚠ 簽名跟隨真函式（2026-07-28 雙通道改版加了第三參數 pain_data；
            # 2026-08-20 申請人口徑改版加了 report_scope）。
            # 原 2 參數 fake 會 TypeError → _merge_cluster_channels 內部吞掉 →
            # cluster_data 靜默變 None，測試以「參數不合」的姿勢假失敗，
            # 錯誤訊息（None != {...}）與真因完全對不上——昨日 5 個懸案 F 之一。
            captured.setdefault("cluster_calls", []).append((workspace_id, source_field))
            if cluster_error is not None:
                raise cluster_error
            return cluster_data

        def _fake_upload(run_dir):
            captured["uploaded"] = sorted(p.name for p in Path(run_dir).iterdir())
            return len(captured["uploaded"])

        with mock.patch.object(handlers, "run_chart_trial", _fake_run_chart_trial), \
                mock.patch.object(handlers, "_load_report_cluster_data", _fake_load_cluster), \
                mock.patch.object(report_artifact_store, "upload_run_dir", _fake_upload):
            result = handlers.handle_report_generate(payload, context)
        return result, captured, store

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="report_handler_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_handler_calls_run_chart_trial_and_produces_artifacts(self):
        """handler 走 run_chart_trial 出圖，回傳帶 version／output_dir／files。"""
        result, captured, _store = self._run({"report_names": ["annual_trend"]})
        self.assertIn("chart_kwargs", captured)
        self.assertEqual(captured["chart_kwargs"]["report_names"], ["annual_trend"])
        self.assertEqual(result["version"], "report_trial_20260723_130000")
        self.assertIn("report_data.json", result["files"])

    def test_handler_preserves_payload_semantics(self):
        """既有 payload 語意保留：report_names／filters／limit／patent_ids 都要傳進去。

        limit＝報表列數上限，對應引擎內唯一吃 limit 的 ranking_limit（排名類報表）。
        """
        payload = {
            "report_names": ["annual_trend"],
            "filters": {"country_code": "TW"},
            "limit": 5,
            "patent_ids": [1, 2, 3],
        }
        _result, captured, _store = self._run(payload)
        kwargs = captured["chart_kwargs"]
        self.assertEqual(kwargs["report_names"], ["annual_trend"])
        self.assertEqual(kwargs["filters"], {"country_code": "TW"})
        self.assertEqual(kwargs["patent_ids"], [1, 2, 3])
        self.assertEqual(kwargs["ranking_limit"], 5)

    def test_handler_uploads_run_dir_for_cross_container_read(self):
        """產物上傳到 DB，backend 容器才讀得到（Railway 兩容器不共享檔案系統）。"""
        _result, captured, _store = self._run({})
        self.assertEqual(captured["uploaded"], ["a.svg", "report_data.json"])

    def test_handler_passes_cluster_data_when_workspace_has_topics(self):
        """有分群資料時餵給 run_chart_trial，分群類圖表才畫得出來。

        ⚠ 2026-07-28 雙通道改版後，handler 對**兩個通道各呼一次** loader 再合併
        （`_merge_cluster_channels`）；本測試的 fake 對兩通道都回同一份資料，
        故期望值是合併後形狀（topics 串接、帶 source_fields），不再是原樣傳遞
        ——舊期望值即昨日懸案 F 之一（fake 簽名過期＋期望值過期，雙重過期）。
        """
        data = {"topics": [{"topic_code": "T01"}], "assignments": [], "topic_rows": []}
        _result, captured, _store = self._run({}, workspace_id=3, cluster_data=data)
        merged = captured["chart_kwargs"]["cluster_data"]
        self.assertEqual(merged["topics"],
                         [{"topic_code": "T01"}, {"topic_code": "T01"}],
                         "兩通道的 topics 應串接（fake 兩通道回同一份）")
        self.assertEqual(merged["source_fields"],
                         ["wips_independent_claims", "effect_summary"],
                         "合併結果要記錄來源通道，順序＝技術先功效後")
        # 兩通道各呼一次，workspace 一致
        self.assertEqual([c[0] for c in captured["cluster_calls"]], [3, 3])
        self.assertEqual([c[1] for c in captured["cluster_calls"]],
                         ["wips_independent_claims", "effect_summary"])

    def test_handler_skips_cluster_charts_when_no_topics(self):
        """無分群資料時 cluster_data=None，run_chart_trial 靜默跳過分群區塊，不 crash。"""
        _result, captured, _store = self._run({}, workspace_id=3, cluster_data=None)
        self.assertIsNone(captured["chart_kwargs"]["cluster_data"])

    def test_handler_survives_cluster_load_failure(self):
        """分群資料載入失敗（無 topic run 等）不得讓整張報表失敗，只跳過分群區塊。"""
        result, captured, _store = self._run(
            {}, workspace_id=3, cluster_error=ValueError("no topic run")
        )
        self.assertIsNone(captured["chart_kwargs"]["cluster_data"])
        self.assertEqual(result["version"], "report_trial_20260723_130000")

    def test_handler_heartbeats_are_monotonic_and_end_at_100(self):
        """階段回報：繁中可讀、百分比遞增、收 100（沿用既有 heartbeat 階段模式）。"""
        _result, _captured, store = self._run({})
        percents = [p for _, p in store.heartbeats]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents[-1], 100)
        stages = [s for s, _ in store.heartbeats]
        self.assertTrue(all(any("一" <= ch <= "鿿" for ch in s) for s in stages),
                        f"階段文字應為繁中可讀，實得 {stages}")


class CrossContainerReadTests(unittest.TestCase):
    """跨容器：讀取端本機檔案系統**沒有**產物，仍要能從 DB 取回內容與圖檔。"""

    @classmethod
    def setUpClass(cls):
        # 空 tmp 目錄當本機輸出根＝模擬 backend 容器完全沒有 worker 寫的檔案。
        cls.tmp = Path(tempfile.mkdtemp(prefix="report_cross_container_"))
        cls._orig_root = main_module.REPORT_OUTPUT_ROOT
        main_module.REPORT_OUTPUT_ROOT = cls.tmp

    @classmethod
    def tearDownClass(cls):
        main_module.REPORT_OUTPUT_ROOT = cls._orig_root
        shutil.rmtree(cls.tmp, ignore_errors=True)

    _VERSION = "report_trial_20260723_140000"
    _REPORT_DATA = {
        "parameters": {
            "generated_at": "2026-07-23T14:00:00",
            "version": _VERSION,
            "scope": "patent_ids_snapshot",
            "patent_ids_count": 12,
        },
        "reports": {
            "annual_trend": {
                "report_name": "annual_trend",
                "rows": [{"application_year": 2025, "patent_count": 4}],
            }
        },
        "family_reports": {},
        "chart_rows": {},
        "sections": [
            {
                "title": "專利申請趨勢",
                "report_key": "annual_trend",
                "variants": [{"label": "Trend", "file": "annual_trend.svg", "variant_key": "default"}],
            }
        ],
    }

    def _patched_store(self):
        """替身 store：只有 DB 有這一版產物，本機檔案系統是空的。"""
        files = {
            "report_data.json": json.dumps(self._REPORT_DATA, ensure_ascii=False).encode("utf-8"),
            "annual_trend.svg": b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        }

        def _read_file(version, filename):
            if version != self._VERSION:
                return None
            return files.get(filename)

        def _list_versions():
            # workspace_id 要明給：main.py 用 `in` 分辨「不歸屬」與「沒帶這欄」，
            # 缺鍵會退回逐版讀 version_meta.json。
            return [{"version": self._VERSION, "has_narratives": False,
                     "workspace_id": None}]

        def _list_filenames(version):
            return set(files) if version == self._VERSION else set()

        # ⚠ `list_filenames` 也必須替身化。它是 3f48b8b（content 端點 12 秒修復）
        #   為了「一次列檔名、不逐檔 exists 往返」新增的，而這個替身沒跟上——
        #   於是 `_DbRunSource.exists()` 掉去打**真** DB，四條測試各卡 30 秒
        #   PoolTimeout。⚠ 假替身只補一半比完全沒有更難查：錯誤訊息是連線逾時，
        #   看起來像環境問題，不像測試替身漏了一個函式。
        return (
            mock.patch.object(main_module.report_artifact_store, "read_file", _read_file),
            mock.patch.object(main_module.report_artifact_store, "list_versions", _list_versions),
            mock.patch.object(main_module.report_artifact_store, "list_filenames", _list_filenames),
        )

    def test_latest_content_reads_from_db_when_filesystem_empty(self):
        """/report-latest/content 在本機無檔時改由 DB 取回，不再 404。"""
        p1, p2, p3 = self._patched_store()
        with p1, p2, p3:
            resp = client.get("/api/v1/report-latest/content")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], self._VERSION)
        self.assertEqual(len(body["sections"]), 1)
        self.assertEqual(body["sections"][0]["report_key"], "annual_trend")
        self.assertEqual(body["sections"][0]["row_count"], 1)

    def test_asset_endpoint_reads_single_file_from_db(self):
        """asset 端點在本機無檔時單檔從 DB 取回（不撈整版）。"""
        p1, p2, p3 = self._patched_store()
        with p1, p2, p3:
            resp = client.get(
                f"/api/v1/report-latest/asset/{self._VERSION}/annual_trend.svg")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"<svg", resp.content)

    def test_versions_listing_includes_db_versions(self):
        """/reports/versions 列出 DB 內的版本（worker 產的版本 backend 也看得到）。"""
        p1, p2, p3 = self._patched_store()
        with p1, p2, p3:
            resp = client.get("/api/v1/reports/versions")
        self.assertEqual(resp.status_code, 200)
        names = [v["version"] for v in resp.json()["versions"]]
        self.assertIn(self._VERSION, names)

    def test_version_content_endpoint_reads_from_db(self):
        """/reports/versions/{v}/content 同樣走 DB，形狀與 latest 一致。"""
        p1, p2, p3 = self._patched_store()
        with p1, p2, p3:
            resp = client.get(f"/api/v1/reports/versions/{self._VERSION}/content")
            latest = client.get("/api/v1/report-latest/content")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sorted(resp.json().keys()), sorted(latest.json().keys()))

    def test_unknown_version_still_404_from_db(self):
        """DB 也沒有的版本仍回 404，不 500。"""
        p1, p2, p3 = self._patched_store()
        with p1, p2, p3:
            resp = client.get("/api/v1/reports/versions/report_trial_不存在/content")
        self.assertEqual(resp.status_code, 404)

    # ⚠ 原 `test_ppt_files_endpoint_lists_pptx_with_download_url`（#10 R10-1）
    #   於 2026-08-19 移除：它守的 `GET /reports/versions/{v}/ppt-files` 端點
    #   已隨 2026-08-11 `remove-ppt-delivery-line` 一併刪掉（main.py:575 留有
    #   移除註記），測試卻沒跟著走，於是永遠 404。守著不存在的端點的測試不會
    #   保護任何東西，只會讓紅燈變成背景雜訊。
    #   store 層的 `list_ppt_files` 仍在（見上方 ReportArtifactStoreTests），
    #   那支測試保留——函式還在就還該有契約。


if __name__ == "__main__":
    unittest.main()
