"""AI bridge `serve` 常駐正式版行為契約測試（Companion 常駐機制）。

`serve` 是使用者本機 Patent Companion 的常駐入口：它必須在無人看管下長期運行，
因此以下四項是「正式版」的必要條件，缺一就會出現「沒人領 AI job」的靜默故障：

1. graceful shutdown：收到停止訊號時做完手上的 job 再退出，不留半途 running job。
2. 單一 job 失敗隔離：某筆 job 炸掉只標記該 job failed，進程續領下一筆。
3. DB 斷線重試：連線層例外採指數退避重試，不是一斷就整個進程死掉。
4. heartbeat：定期落檔，讓 doctor／前端看得出 Companion 還活著。

測試一律用假 queue／假時鐘，不連資料庫、不呼叫任何外部 CLI。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from backend.app.worker import ai_bridge
from backend.app.worker.queue_client import ProcessingJob


def _ai_job(job_id: int = 501) -> ProcessingJob:
    """建立一筆 ai:narrative 測試 job。"""
    return ProcessingJob(
        job_id=job_id,
        job_type="ai:narrative",
        status="queued",
        workspace_id=None,
        payload_json={},
        result_json=None,
        progress_percent=0,
        current_stage="queued",
        attempt_count=0,
        max_attempts=3,
    )


class _FakeClock:
    """可控時鐘：記錄 sleep 秒數，避免測試真的等待。"""

    def __init__(self) -> None:
        """初始化累計時間與 sleep 紀錄。"""
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        """回傳目前假時間。"""
        return self.now

    def sleep(self, seconds: float) -> None:
        """記錄 sleep 並推進假時間，不真的阻塞。"""
        self.slept.append(seconds)
        self.now += seconds


class ServeGracefulShutdownTests(unittest.TestCase):
    """收到停止訊號時，serve 必須先做完手上的 job 才退出。"""

    def test_serve_finishes_current_job_before_exiting(self):
        """停止訊號在 job 執行中送達：該 job 仍要跑完，且迴圈不再領下一筆。"""
        executed: list[int] = []
        shutdown = ai_bridge.ShutdownSignal()

        def _fake_run_once(**kwargs):
            """模擬領到 job：執行途中收到停止訊號。"""
            executed.append(len(executed) + 1)
            shutdown.request("SIGINT")  # job 執行中被要求停止
            return {"status": "succeeded", "job_id": 501}

        clock = _FakeClock()
        with mock.patch.object(ai_bridge, "run_once", side_effect=_fake_run_once):
            summary = ai_bridge.serve(
                worker_id="bridge-test",
                poll_seconds=1.0,
                stale_after_seconds=120,
                shutdown=shutdown,
                sleep=clock.sleep,
                monotonic=clock.monotonic,
                heartbeat_path=None,
            )

        # 手上的 job 有跑完（run_once 完整回傳），但不再領第二筆。
        self.assertEqual(executed, [1])
        self.assertEqual(summary["stopped_by"], "SIGINT")
        self.assertEqual(summary["jobs_succeeded"], 1)

    def test_serve_exits_without_claiming_when_shutdown_before_loop(self):
        """停止訊號已在啟動前送達時，serve 一筆都不領就退出。"""
        shutdown = ai_bridge.ShutdownSignal()
        shutdown.request("SIGTERM")
        clock = _FakeClock()

        with mock.patch.object(ai_bridge, "run_once") as patched:
            summary = ai_bridge.serve(
                worker_id="bridge-test",
                poll_seconds=1.0,
                stale_after_seconds=120,
                shutdown=shutdown,
                sleep=clock.sleep,
                monotonic=clock.monotonic,
                heartbeat_path=None,
            )

        patched.assert_not_called()
        self.assertEqual(summary["jobs_claimed"], 0)
        self.assertEqual(summary["stopped_by"], "SIGTERM")


class ServeJobFailureIsolationTests(unittest.TestCase):
    """單一 job 失敗不得讓常駐進程死掉。"""

    def test_serve_continues_after_job_raises(self):
        """run_once 丟出非連線類例外時，serve 記錄失敗並繼續領下一筆。"""
        shutdown = ai_bridge.ShutdownSignal()
        calls: list[int] = []

        def _fake_run_once(**kwargs):
            """第一輪炸掉、第二輪成功、第三輪要求停止。"""
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                raise ValueError("handler exploded")
            if len(calls) == 2:
                return {"status": "succeeded", "job_id": 502}
            shutdown.request("SIGINT")
            return {"status": "idle", "stale": {}}

        clock = _FakeClock()
        with mock.patch.object(ai_bridge, "run_once", side_effect=_fake_run_once):
            summary = ai_bridge.serve(
                worker_id="bridge-test",
                poll_seconds=1.0,
                stale_after_seconds=120,
                shutdown=shutdown,
                sleep=clock.sleep,
                monotonic=clock.monotonic,
                heartbeat_path=None,
            )

        # 進程沒死：三輪都跑到了。
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(summary["loop_errors"], 1)
        self.assertEqual(summary["jobs_succeeded"], 1)

    def test_execute_ai_job_failure_does_not_propagate(self):
        """既有保證的回歸：handler 例外由 execute_ai_job 收斂成 failed，不往外丟。"""
        job = _ai_job()

        class _Store:
            """只記錄 fail_job 的最小假 store。"""

            def __init__(self) -> None:
                self.failed: list[str] = []

            def heartbeat(self, **kwargs) -> None:
                """忽略 heartbeat。"""

            def fail_job(self, *, job_id, worker_id, error_message, current_stage="failed") -> None:
                """記錄失敗訊息。"""
                self.failed.append(error_message)

            def is_cancelled(self, *, job_id) -> bool:
                """測試中永不取消。"""
                return False

        store = _Store()
        with mock.patch.object(ai_bridge, "_run_ai_narrative_job", side_effect=RuntimeError("boom")):
            result = ai_bridge.execute_ai_job(job, worker_id="bridge-test", store=store)

        self.assertEqual(result["status"], "failed")
        self.assertIn("boom", store.failed[0])


class ServeDatabaseRetryTests(unittest.TestCase):
    """DB 斷線後 serve 要指數退避重試，而不是退出。"""

    def test_serve_backs_off_and_recovers_after_db_outage(self):
        """連線類例外連續三次後恢復：退避秒數遞增，且進程存活到恢復。"""
        shutdown = ai_bridge.ShutdownSignal()
        calls: list[int] = []

        def _fake_run_once(**kwargs):
            """前三輪模擬 DB 斷線，第四輪恢復並要求停止。"""
            calls.append(len(calls) + 1)
            if len(calls) <= 3:
                raise ai_bridge.OperationalError("connection refused")
            shutdown.request("SIGINT")
            return {"status": "idle", "stale": {}}

        clock = _FakeClock()
        with mock.patch.object(ai_bridge, "run_once", side_effect=_fake_run_once):
            summary = ai_bridge.serve(
                worker_id="bridge-test",
                poll_seconds=1.0,
                stale_after_seconds=120,
                shutdown=shutdown,
                sleep=clock.sleep,
                monotonic=clock.monotonic,
                heartbeat_path=None,
            )

        self.assertEqual(len(calls), 4)
        self.assertEqual(summary["db_errors"], 3)
        # 退避必須遞增（指數），且不超過上限。
        backoffs = clock.slept[:3]
        self.assertEqual(len(backoffs), 3)
        self.assertLess(backoffs[0], backoffs[1])
        self.assertLess(backoffs[1], backoffs[2])
        self.assertLessEqual(backoffs[-1], ai_bridge.MAX_DB_BACKOFF_SECONDS)

    def test_backoff_is_capped(self):
        """退避序列有上限，長時間斷線不會退避到數小時才重試。"""
        values = [ai_bridge.compute_backoff_seconds(n) for n in range(1, 12)]
        self.assertTrue(all(v <= ai_bridge.MAX_DB_BACKOFF_SECONDS for v in values))
        self.assertEqual(values[-1], ai_bridge.MAX_DB_BACKOFF_SECONDS)


class ServeHeartbeatTests(unittest.TestCase):
    """heartbeat 落檔，讓 doctor／前端查得出 Companion 是否活著。"""

    def test_serve_writes_heartbeat_file(self):
        """每輪迴圈都更新 heartbeat 檔，內容含 worker_id 與統計。"""
        shutdown = ai_bridge.ShutdownSignal()

        def _fake_run_once(**kwargs):
            """一輪 idle 後要求停止。"""
            shutdown.request("SIGINT")
            return {"status": "idle", "stale": {}}

        clock = _FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            hb = Path(tmp) / "state" / "heartbeat.json"
            with mock.patch.object(ai_bridge, "run_once", side_effect=_fake_run_once):
                ai_bridge.serve(
                    worker_id="bridge-test",
                    poll_seconds=1.0,
                    stale_after_seconds=120,
                    shutdown=shutdown,
                    sleep=clock.sleep,
                    monotonic=clock.monotonic,
                    heartbeat_path=hb,
                )

            self.assertTrue(hb.exists(), "serve 必須落 heartbeat 檔")
            data = json.loads(hb.read_text(encoding="utf-8"))
            self.assertEqual(data["worker_id"], "bridge-test")
            self.assertIn("updated_at", data)
            self.assertIn("pid", data)
            self.assertEqual(data["status"], "stopped")

    def test_read_heartbeat_reports_stale_when_old(self):
        """heartbeat 太舊要判為 stale，doctor 才能指出 Companion 已掛。"""
        with tempfile.TemporaryDirectory() as tmp:
            hb = Path(tmp) / "heartbeat.json"
            hb.write_text(
                json.dumps(
                    {
                        "worker_id": "bridge-test",
                        "pid": 1,
                        "status": "running",
                        "updated_at": "2000-01-01T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            state = ai_bridge.read_heartbeat(hb)

        self.assertFalse(state["alive"])
        self.assertEqual(state["reason"], "stale")

    def test_read_heartbeat_missing_file(self):
        """沒有 heartbeat 檔＝從未啟動過，要明確回報而不是丟例外。"""
        with tempfile.TemporaryDirectory() as tmp:
            state = ai_bridge.read_heartbeat(Path(tmp) / "nope.json")
        self.assertFalse(state["alive"])
        self.assertEqual(state["reason"], "missing")

    def test_doctor_includes_heartbeat_section(self):
        """doctor 要一併回報 heartbeat，使用者才有單一診斷入口。"""
        with (
            mock.patch.object(ai_bridge, "_db_check", return_value={"ok": True}),
            mock.patch.object(ai_bridge, "_cli_check", return_value={"ok": True}),
        ):
            result = ai_bridge.run_doctor(cli_kind="claude")

        self.assertIn("heartbeat", result)
        self.assertIn("alive", result["heartbeat"])


class ServeConfigPathTests(unittest.TestCase):
    """路徑一律可設定／自動偵測，不得寫死。"""

    def test_heartbeat_path_follows_env_override(self):
        """AI_BRIDGE_STATE_DIR 可覆蓋 heartbeat 落點（安裝腳本用來指定日誌／狀態目錄）。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"AI_BRIDGE_STATE_DIR": tmp}, clear=False):
                path = ai_bridge.default_heartbeat_path()
            self.assertEqual(path.parent, Path(tmp).resolve())

    def test_heartbeat_path_defaults_under_project_root(self):
        """未設環境變數時落在專案根目錄下的 var/，仍由 __file__ 推導而非寫死磁碟路徑。"""
        env = {k: v for k, v in __import__("os").environ.items() if k != "AI_BRIDGE_STATE_DIR"}
        with mock.patch.dict("os.environ", env, clear=True):
            path = ai_bridge.default_heartbeat_path()
        self.assertTrue(str(path).startswith(str(ai_bridge.PROJECT_ROOT)))

    def test_parser_exposes_heartbeat_and_shutdown_options(self):
        """serve 可由安裝腳本指定 heartbeat 檔位置。"""
        args = ai_bridge.build_parser().parse_args(
            ["serve", "--heartbeat-file", "X:/state/hb.json"]
        )
        self.assertEqual(args.command, "serve")
        self.assertEqual(args.heartbeat_file, "X:/state/hb.json")


class ServeStopFileTests(unittest.TestCase):
    """停止旗標檔：Windows 真實停止路徑唯一能觸發 graceful shutdown 的機制。

    實測（見 `docs`／工作紀錄）：`Stop-ScheduledTask`、`os.kill(SIGINT/SIGTERM)`
    在 Windows 都是 TerminateProcess，Python handler **不會執行**；只有
    CTRL_BREAK_EVENT 送到 process group 才會。排程器不送 CTRL_BREAK，
    因此 serve 必須自己輪詢一個停止旗標檔。
    """

    def test_serve_stops_when_stop_file_appears(self):
        """停止旗標檔出現時，serve 在下一輪之間退出，且 stopped_by 標記來源。"""
        with tempfile.TemporaryDirectory() as tmp:
            stop_file = Path(tmp) / "stop"
            calls: list[int] = []

            def _fake_run_once(**kwargs):
                """第一輪 idle，第二輪前由外部建立停止旗標檔。"""
                calls.append(len(calls) + 1)
                if len(calls) == 1:
                    stop_file.write_text("stop", encoding="utf-8")
                return {"status": "idle", "stale": {}}

            clock = _FakeClock()
            with mock.patch.object(ai_bridge, "run_once", side_effect=_fake_run_once):
                summary = ai_bridge.serve(
                    worker_id="bridge-test",
                    poll_seconds=1.0,
                    stale_after_seconds=120,
                    sleep=clock.sleep,
                    monotonic=clock.monotonic,
                    heartbeat_path=None,
                    stop_file=stop_file,
                )

            # 第一輪跑完才停：旗標只在兩輪之間生效，不打斷手上的 job。
            self.assertEqual(calls, [1])
            self.assertEqual(summary["stopped_by"], "stop_file")

    def test_serve_finishes_current_job_when_stop_file_appears(self):
        """job 執行中出現停止旗標：該 job 仍跑完，不留半途 running。"""
        with tempfile.TemporaryDirectory() as tmp:
            stop_file = Path(tmp) / "stop"
            executed: list[int] = []

            def _fake_run_once(**kwargs):
                """模擬 job 執行途中被要求停止。"""
                executed.append(len(executed) + 1)
                stop_file.write_text("stop", encoding="utf-8")
                return {"status": "succeeded", "job_id": 601}

            clock = _FakeClock()
            with mock.patch.object(ai_bridge, "run_once", side_effect=_fake_run_once):
                summary = ai_bridge.serve(
                    worker_id="bridge-test",
                    poll_seconds=1.0,
                    stale_after_seconds=120,
                    sleep=clock.sleep,
                    monotonic=clock.monotonic,
                    heartbeat_path=None,
                    stop_file=stop_file,
                )

            self.assertEqual(executed, [1])
            self.assertEqual(summary["jobs_succeeded"], 1)
            self.assertEqual(summary["stopped_by"], "stop_file")

    def test_serve_clears_stale_stop_file_at_startup(self):
        """啟動時要清掉上一輪殘留的旗標，否則重啟後會立刻自殺、永遠領不到 job。"""
        with tempfile.TemporaryDirectory() as tmp:
            stop_file = Path(tmp) / "stop"
            stop_file.write_text("leftover", encoding="utf-8")
            shutdown = ai_bridge.ShutdownSignal()

            def _fake_run_once(**kwargs):
                """能被呼叫代表殘留旗標沒有誤觸停止。"""
                shutdown.request("SIGINT")
                return {"status": "idle", "stale": {}}

            clock = _FakeClock()
            with mock.patch.object(ai_bridge, "run_once", side_effect=_fake_run_once) as patched:
                ai_bridge.serve(
                    worker_id="bridge-test",
                    poll_seconds=1.0,
                    stale_after_seconds=120,
                    shutdown=shutdown,
                    sleep=clock.sleep,
                    monotonic=clock.monotonic,
                    heartbeat_path=None,
                    stop_file=stop_file,
                )

            patched.assert_called()
            self.assertFalse(stop_file.exists(), "啟動時必須清掉殘留旗標")

    def test_parser_exposes_stop_file_option(self):
        """安裝腳本要能指定旗標檔位置（uninstall 才知道該建哪個檔）。"""
        args = ai_bridge.build_parser().parse_args(
            ["serve", "--stop-file", "X:/state/stop"]
        )
        self.assertEqual(args.stop_file, "X:/state/stop")

    def test_default_stop_file_under_state_dir(self):
        """預設旗標檔跟 heartbeat 同在狀態目錄下，安裝腳本只需傳一個 StateDir。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"AI_BRIDGE_STATE_DIR": tmp}, clear=False):
                path = ai_bridge.default_stop_file_path()
            self.assertEqual(path.parent, Path(tmp).resolve())


class DoctorHealthTests(unittest.TestCase):
    """doctor exit code 必須反映 Companion 是否存活，且分辨「沒啟動」與「該活著卻死了」。"""

    def _doctor_exit_code(self, heartbeat_state: dict) -> int:
        """跑 main() 的 doctor 分支，回傳 exit code（不連 DB、不呼叫 CLI）。"""
        argv = ["ai_bridge", "doctor"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(ai_bridge, "load_local_env"),
            mock.patch.object(ai_bridge, "configure_logging"),
            mock.patch.object(ai_bridge, "_db_check", return_value={"ok": True}),
            mock.patch.object(ai_bridge, "_cli_check", return_value={"ok": True}),
            mock.patch.object(ai_bridge, "read_heartbeat", return_value=heartbeat_state),
        ):
            return ai_bridge.main()

    def test_doctor_fails_when_heartbeat_stale(self):
        """曾經啟動但心跳過期＝該活著卻死了，必須回非零。"""
        self.assertEqual(
            self._doctor_exit_code({"alive": False, "reason": "stale"}), 1
        )

    def test_doctor_fails_when_heartbeat_unreadable(self):
        """心跳檔壞掉也視為異常，不得回 0 讓故障靜默。"""
        self.assertEqual(
            self._doctor_exit_code({"alive": False, "reason": "unreadable"}), 1
        )

    def test_doctor_passes_when_never_started(self):
        """從未啟動（missing）屬正常：安裝前 doctor 也要能過。"""
        self.assertEqual(
            self._doctor_exit_code({"alive": False, "reason": "missing"}), 0
        )

    def test_doctor_passes_when_stopped_cleanly(self):
        """正常關閉（stopped）＝使用者自己停的，不是故障。"""
        self.assertEqual(
            self._doctor_exit_code({"alive": False, "reason": "stopped"}), 0
        )

    def test_doctor_passes_when_alive(self):
        """心跳正常時回 0。"""
        self.assertEqual(self._doctor_exit_code({"alive": True, "reason": "ok"}), 0)


@unittest.skipUnless(
    os.name == "nt",
    "真子行程停止路徑測試只驗 Windows 停止語意（TerminateProcess vs 旗標檔）；"
    "非 Windows 平台 SIGTERM 本來就會觸發 handler，測不到本輪要修的問題。",
)
class ServeSubprocessStopPathTests(unittest.TestCase):
    """端到端：真的起一個子行程跑 serve，用真實停止路徑停它。

    這是本輪的核心回歸——純記憶體注入 `ShutdownSignal` 的測試**驗不出**
    Windows 上「排程器硬砍、handler 不跑」的問題。
    """

    def _child_source(self, state: Path) -> str:
        """產生子行程腳本：跑真的 ai_bridge.serve，run_once 換成不連 DB 的假實作。"""
        return textwrap.dedent(
            f"""
            import json, sys, time
            from pathlib import Path
            sys.path.insert(0, {str(ai_bridge.PROJECT_ROOT)!r})
            from backend.app.worker import ai_bridge

            state = Path({str(state)!r})
            job_done = state / "job_done.txt"
            summary_file = state / "summary.json"

            calls = {{"n": 0}}

            def fake_run_once(**kwargs):
                # 第一輪模擬一筆「執行中」的長 job：停止旗標會在它跑到一半時出現，
                # 這一筆必須完整跑完（寫 job_done）才准退出。
                calls["n"] += 1
                if calls["n"] == 1:
                    (state / "job_started.txt").write_text("1", encoding="utf-8")
                    time.sleep(1.5)
                    job_done.write_text("1", encoding="utf-8")
                    return {{"status": "succeeded", "job_id": 901}}
                return {{"status": "idle", "stale": {{}}}}

            ai_bridge.run_once = fake_run_once
            summary = ai_bridge.serve(
                worker_id="subproc-test",
                poll_seconds=0.2,
                stale_after_seconds=120,
                heartbeat_path=state / "hb.json",
                stop_file=state / "stop",
            )
            summary_file.write_text(json.dumps(summary), encoding="utf-8")
            """
        )

    def test_stop_file_triggers_graceful_shutdown_in_real_subprocess(self):
        """真子行程 + 真實停止路徑（建旗標檔）：handler 有跑、job 跑完、exit code 0。"""
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            script = state / "child_serve.py"
            script.write_text(self._child_source(state), encoding="utf-8")

            proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(ai_bridge.PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                # 等子行程真的進入第一筆 job 執行中，才送停止訊號。
                started = state / "job_started.txt"
                deadline = time.time() + 30
                while time.time() < deadline and not started.exists():
                    time.sleep(0.05)
                self.assertTrue(started.exists(), "子行程未進入 job 執行")

                # 真實停止路徑：uninstall 腳本做的就是建這個檔。
                (state / "stop").write_text("stop", encoding="utf-8")

                rc = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                self.fail("子行程未在停止旗標後退出")
            finally:
                if proc.poll() is None:
                    proc.kill()

            stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            # ① exit code 正確
            self.assertEqual(rc, 0, f"子行程非正常退出：{stderr[-2000:]}")
            # ② 執行中的 job 有跑完（沒被硬砍成孤兒）
            self.assertTrue((state / "job_done.txt").exists(), "手上的 job 未跑完就退出")
            # ③ graceful shutdown 路徑有跑：serve 有回傳 summary、heartbeat 收尾為 stopped
            summary = json.loads((state / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["stopped_by"], "stop_file")
            self.assertEqual(summary["jobs_succeeded"], 1)
            hb = json.loads((state / "hb.json").read_text(encoding="utf-8"))
            self.assertEqual(hb["status"], "stopped")

    def test_terminate_process_does_not_run_handlers(self):
        """記錄 Windows 事實：TerminateProcess（排程器停止的實作）不會跑 handler。

        這條測試把「為什麼需要旗標檔」釘成回歸契約——若哪天有人想把停止改回
        送訊號，這裡會提醒他 Windows 上收不到。
        """
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            script = state / "child_signal.py"
            script.write_text(
                textwrap.dedent(
                    f"""
                    import signal, sys, time
                    from pathlib import Path
                    marker = Path({str(state / "handler.txt")!r})

                    def _h(signum, frame):
                        marker.write_text(str(signum), encoding="utf-8")
                        sys.exit(0)

                    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                        s = getattr(signal, name, None)
                        if s is not None:
                            try:
                                signal.signal(s, _h)
                            except Exception:
                                pass
                    Path({str(state / "ready.txt")!r}).write_text("1", encoding="utf-8")
                    time.sleep(30)
                    """
                ),
                encoding="utf-8",
            )
            proc = subprocess.Popen([sys.executable, str(script)])
            try:
                deadline = time.time() + 30
                while time.time() < deadline and not (state / "ready.txt").exists():
                    time.sleep(0.05)
                self.assertTrue((state / "ready.txt").exists())
                proc.terminate()  # Windows 上即 TerminateProcess
                proc.wait(timeout=15)
            finally:
                if proc.poll() is None:
                    proc.kill()

            self.assertFalse(
                (state / "handler.txt").exists(),
                "Windows TerminateProcess 若真的觸發了 handler，停止機制可改回送訊號",
            )


if __name__ == "__main__":
    unittest.main()
