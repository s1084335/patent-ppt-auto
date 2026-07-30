"""報告 PPT 產製（ai:report_ppt）契約測試（串匯出報告線）。

匯出報告分工（export-report-flow-spec.md 第二節）：AI 只產文案 slots 草稿 →
寫 approvals.json → CLI 順手呼 deterministic 的 build_ppt.py 組版 → .pptx 進
report_artifacts。本檔鎖住的紅線：

1. job type 落 AI_JOB_TYPES（走 Companion，一般 worker 不領）。
2. runner 分工：AI 只產文案（slots）、不碰排版、不碰數字；build_ppt.py deterministic 組版。
3. slots＝spec 第二節列的確認槽（cover.title/trend.narrative/direction.body/...）。
4. 產出 .pptx 進 report_artifacts（跨容器：本機檔案系統不通，必須進 DB）。
5. 全庫也能產 PPT（build_ppt 對全庫不設限，只市場章節第7/9/10頁在全庫空著）。
6. dispatch 路由：ai:report_ppt → _run_ai_report_ppt_job。

CLI 一律用可注入的 fake runner，build_ppt 與 upload 也可注入，
不真跑二進位、不燒 token、不真碰 DB／檔案系統。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.db import job_repository
from backend.app.worker import ai_bridge
from backend.app.worker import ai_report_ppt_runner as runner_mod
from backend.app.worker import runner as worker_runner
from backend.app.worker.ai_narrative_runner import CliResult
from backend.app.worker.queue_client import ProcessingJob
from tests.ai_payload_test_helpers import read_payload_from_argv


# ── 測試替身 ───────────────────────────────────────────────────────


class RecordingCli:
    """假 CLI runner：記錄 argv，回吐一份 slots 文案草稿。"""

    def __init__(self, slots=None):
        """保存要回吐的 slots；準備記錄 argv。"""
        self.calls: list[list[str]] = []
        self.slots = slots if slots is not None else {
            "cover.title": "自走式割草機專利情報整合分析",
            "trend.narrative": "近三年申請量穩定成長。",
            "direction.body": "建議聚焦電池平台生態。",
        }

    def __call__(self, argv, timeout):
        """記錄 argv，回吐 {"slots": {...}}（外層包在 claude -p json 的 result 字串）。"""
        self.calls.append(list(argv))
        return CliResult(
            exit_code=0,
            stdout=json.dumps({"result": json.dumps(
                {"slots": self.slots}, ensure_ascii=False)}),
            stderr="",
        )

    @property
    def prompts(self) -> list[str]:
        """所有批次的 prompt 字串。"""
        return [argv[argv.index("-p") + 1] for argv in self.calls]


def _make_report_dir(tmp: str, version: str = "report_trial_20260724_120000") -> Path:
    """建一個最小有效報表版本目錄（含 report_data.json）。"""
    run_dir = Path(tmp) / version
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report_data.json").write_text(
        json.dumps({"parameters": {"version": version},
                    "reports": {"application_trend": {"rows": [
                        {"application_year": 2025, "patent_count": 4}]}}}),
        encoding="utf-8")
    (run_dir / "narratives.json").write_text(
        json.dumps(
            {
                "based_on_version": version,
                "reports": {
                    "application_trend": {
                        "variants": {
                            "default": {
                                "text": "2025 年有 4 件申請。",
                                "prompt_version": "report_narrative_v3",
                            }
                        }
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return run_dir


# ── job 註冊：走 Companion，一般 worker 不領 ───────────────────────


class JobRegistrationTests(unittest.TestCase):
    """ai:report_ppt 只由 ai_bridge 領取，一般 worker 領不到。"""

    def test_job_type_registered_as_ai_job(self):
        """job type 落在 AI_JOB_TYPES（唯一事實來源），一般 worker 不領。"""
        self.assertIn("ai:report_ppt", job_repository.AI_JOB_TYPES)
        self.assertIn("ai:report_ppt", job_repository.JOB_TYPES)
        self.assertNotIn("ai:report_ppt", worker_runner.DEFAULT_WORKER_JOB_TYPES)


# ── dispatch 路由 ─────────────────────────────────────────────────


class DispatchTests(unittest.TestCase):
    """execute_ai_job 依 job_type 路由到 _run_ai_report_ppt_job。"""

    class _Store:
        """記錄 complete／fail；不碰資料庫。"""

        def __init__(self):
            self.completed = []
            self.failed = []

        def heartbeat(self, **kw):
            pass

        def complete_job(self, *, job_id, worker_id, result_json):
            self.completed.append(result_json)

        def fail_job(self, *, job_id, worker_id, error_message, current_stage="failed"):
            self.failed.append(error_message)

        def is_cancelled(self, *, job_id):
            return False

    def _job(self, payload):
        return ProcessingJob(
            job_id=51, job_type="ai:report_ppt", status="running", workspace_id=None,
            payload_json=payload, result_json=None, progress_percent=0,
            current_stage="queued", attempt_count=1, max_attempts=1)

    def test_dispatch_routes_report_ppt_job(self):
        """ai:report_ppt 必須路由到 _run_ai_report_ppt_job（dispatch 表接對）。"""
        store = self._Store()
        job = self._job({"based_on_version": "v1"})
        with mock.patch.object(
            ai_bridge, "_run_ai_report_ppt_job", return_value={"pptx_filename": "v1.pptx"}
        ) as patched:
            result = ai_bridge.execute_ai_job(job, worker_id="ai-bridge-test", store=store)
        patched.assert_called_once()
        self.assertEqual(result["status"], "succeeded")


# ── runner 分工：AI 只產文案、build_ppt 組版、.pptx 進 artifact ─────


class RunnerWorkSeparationTests(unittest.TestCase):
    """runner 分工紅線：AI 只產 slots 文案，build_ppt deterministic 組版，.pptx 進 DB。"""

    def _run(self, *, cli, based_on_version=None, resolve_dir=None,
             build_calls=None, uploaded=None, slots_written=None,
             approval_overrides=None):
        """跑 runner，build_ppt／upload／resolve 全替身，回傳 result。

        payload_root 指到報表目錄底下，避免測試把資料檔寫進專案的 var/ai_payloads。
        """

        def _fake_resolve(bov, *, root=None):
            return resolve_dir

        def _fake_build_ppt(*, report_dir, approvals_path, output_dir, theme_path=None):
            # 記錄 build_ppt 收到的 approvals.json 內容（驗 slots 寫入契約）。
            build_calls.append({
                "report_dir": str(report_dir),
                "approvals_path": str(approvals_path),
                "output_dir": str(output_dir),
            })
            if slots_written is not None:
                data = json.loads(Path(approvals_path).read_text(encoding="utf-8"))
                slots_written.update(data)
            pptx = Path(output_dir) / (resolve_dir.name + ".pptx")
            pptx.write_bytes(b"PK\x03\x04fake-pptx")
            manifest = Path(output_dir) / (resolve_dir.name + ".manifest.json")
            manifest.write_text(json.dumps({"pptx_file": pptx.name}), encoding="utf-8")
            return {"pptx_path": str(pptx), "manifest_path": str(manifest),
                    "manifest": {"pptx_file": pptx.name}}

        def _fake_upload(run_dir):
            uploaded.extend(sorted(p.name for p in Path(run_dir).iterdir()))
            return len(uploaded)

        return runner_mod.run_report_ppt(
            based_on_version=based_on_version,
            cli_runner=cli,
            resolve_run_dir=_fake_resolve,
            build_ppt=_fake_build_ppt,
            upload_run_dir=_fake_upload,
            payload_root=resolve_dir.parent / "payloads",
            approval_overrides=approval_overrides,
        )

    def test_ai_produces_slots_written_to_approvals(self):
        """AI 產的 slots 文案寫進 approvals.json，供 build_ppt 吃。"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli()
            build_calls, uploaded, slots_written = [], [], {}
            result = self._run(cli=cli, resolve_dir=run_dir, build_calls=build_calls,
                               uploaded=uploaded, slots_written=slots_written)
            # slots 進 approvals.json 的 slots 區。
            self.assertEqual(slots_written["slots"]["cover.title"],
                             "自走式割草機專利情報整合分析")
            self.assertEqual(slots_written["report_version"], run_dir.name)
            self.assertEqual(result["cli_kind"], "claude")

    def test_user_overrides_are_preserved_in_approvals(self):
        """使用者在預覽頁改文案、版型、座標時，覆寫資料要寫入 approvals.json。"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli(slots={"trend.narrative": "AI trend"})
            build_calls, uploaded, slots_written = [], [], {}
            overrides = {
                "slots": {"cover.title": "人工標題"},
                "layout_overrides": {"3": "table"},
                "position_overrides": {
                    "3.chart": {
                        "left_in": 1.2,
                        "top_in": 2.3,
                        "width_in": 4.5,
                        "height_in": 2.0,
                    }
                },
            }
            self._run(
                cli=cli,
                resolve_dir=run_dir,
                build_calls=build_calls,
                uploaded=uploaded,
                slots_written=slots_written,
                approval_overrides=overrides,
            )
            self.assertEqual(slots_written["slots"]["cover.title"], "人工標題")
            self.assertEqual(slots_written["slots"]["trend.narrative"], "AI trend")
            self.assertEqual(slots_written["layout_overrides"], overrides["layout_overrides"])
            self.assertEqual(slots_written["position_overrides"], overrides["position_overrides"])

    def test_build_ppt_called_with_report_dir_and_approvals(self):
        """CLI 順手呼 build_ppt：帶 report_dir 與剛寫的 approvals.json。"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli()
            build_calls, uploaded = [], []
            self._run(cli=cli, resolve_dir=run_dir, build_calls=build_calls,
                      uploaded=uploaded)
            self.assertEqual(len(build_calls), 1)
            self.assertEqual(build_calls[0]["report_dir"], str(run_dir))
            self.assertTrue(build_calls[0]["approvals_path"].endswith(".json"))

    def test_pptx_uploaded_to_report_artifacts(self):
        """.pptx 進 report_artifacts（跨容器：本機檔案系統不通，必須進 DB）。"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli()
            build_calls, uploaded = [], []
            result = self._run(cli=cli, resolve_dir=run_dir, build_calls=build_calls,
                               uploaded=uploaded)
            # 上傳目錄含 .pptx（build_ppt 產在 report_dir 裡，upload 一起上傳）。
            self.assertIn(run_dir.name + ".pptx", uploaded)
            self.assertEqual(result["pptx_filename"], run_dir.name + ".pptx")

    def test_payload_asks_only_for_text_slots(self):
        """AI 只被要求產文案 slots——資料檔明寫不碰排版、不碰數字。

        ⚠ 2026-07-28 由查 prompt 改查資料檔：分工鐵律隨報表數據一起搬進 payload，
        argv 只剩「短指示＋路徑」。鎖的行為不變（AI 必須被明確告知不碰排版／數字），
        只是換到它現在真正所在的位置——查 argv 會變成永遠通不過的假紅線。
        """
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli()
            self._run(cli=cli, resolve_dir=run_dir, build_calls=[], uploaded=[])
            payload = read_payload_from_argv(cli.calls[0])
            self.assertTrue(payload, "argv 內找不到資料檔")
            # 分工鐵律必須在 rules 欄（結構化位置），不是散落在任意文字裡。
            rules_text = json.dumps(payload.get("rules"), ensure_ascii=False)
            self.assertTrue(
                any(k in rules_text for k in ("排版", "組版", "不捏造", "不碰")),
                f"資料檔 rules 未表達 AI 不碰排版/數字的分工：{rules_text[:200]}")

    def test_payload_includes_narratives_for_cross_page_context(self):
        """PPT slots payload 必須帶 narratives，讓第二階段能沿用第一階段解讀。"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli()
            self._run(cli=cli, resolve_dir=run_dir, build_calls=[], uploaded=[])
            payload = read_payload_from_argv(cli.calls[0])

        self.assertIn("narratives", payload)
        self.assertEqual(payload["narratives"]["based_on_version"], run_dir.name)
        rules_text = json.dumps(payload.get("rules"), ensure_ascii=False)
        for expected in ("全部報表", "頁間脈絡", "不得編造因果", "實際存在"):
            self.assertIn(expected, rules_text)

    def test_slot_keys_come_from_build_ppt_not_hardcoded(self):
        """slot 命名一律取自 build_ppt.py 的 all_slot_keys()，不在 runner 另定一套。

        沿用既有 PAGE_LAYOUT 定義（唯一來源）：資料檔 slot_keys 必須與 build_ppt 一致，
        避免 runner 產的槽名與組版程式讀的槽名對不上。

        ⚠ 比對整份 slot_keys 清單相等（不是「有包含」）：多一個或少一個槽都要抓到，
        才擋得住 runner 日後偷加自定槽名。
        """
        expected = runner_mod.report_slot_keys()
        # 至少包含 spec 第二節列的代表性槽（來自 build_ppt PAGE_LAYOUT）。
        for slot in ("cover.title", "trend.narrative", "direction.body", "key_players.summary"):
            self.assertIn(slot, expected)
        for removed in ("pain_point.narrative", "key_players.market", "market.scope", "market.size"):
            self.assertNotIn(removed, expected)
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli()
            self._run(cli=cli, resolve_dir=run_dir, build_calls=[], uploaded=[])
            payload = read_payload_from_argv(cli.calls[0])
            self.assertEqual(
                payload.get("slot_keys"), expected,
                "資料檔 slot_keys 必須與 build_ppt.all_slot_keys() 完全一致")

    def test_invalid_slots_are_filtered_and_reported(self):
        """AI 或使用者 override 給無效 slot 時要過濾，並在結果中回報。"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli(slots={
                "cover.title": "valid",
                "market.scope": "legacy invalid",
                "ai.made_up": "invalid",
            })
            build_calls, uploaded, slots_written = [], [], {}
            result = self._run(
                cli=cli,
                resolve_dir=run_dir,
                build_calls=build_calls,
                uploaded=uploaded,
                slots_written=slots_written,
                approval_overrides={"slots": {"market.size": "manual invalid"}},
            )

        self.assertEqual(slots_written["slots"], {"cover.title": "valid"})
        self.assertEqual(
            sorted(result["invalid_slots"]),
            ["ai.made_up", "market.scope", "market.size"],
        )


# ── CLI 白名單：不開網路、不寫檔（安全來自任務設計） ─────────────


class CliWhitelistTests(unittest.TestCase):
    """報告 PPT 文案 CLI 維持最小權限：主路徑只放行 Read（讀那一個資料檔），不連網、不寫檔。"""

    def test_legacy_inline_command_opens_no_tools(self):
        """保留的內嵌路徑（離線除錯用）白名單仍為空——資料內嵌就不需要任何工具。"""
        argv = runner_mod.build_cli_command("claude", "prompt-body")
        joined = " ".join(argv)
        for banned in ("WebSearch", "WebFetch", "Read", "Glob", "Grep", "Write"):
            self.assertNotIn(banned, joined)
        self.assertIn("--allowedTools", argv)
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "")

    def test_runner_grants_read_only(self):
        """🔴 主路徑（實際跑的那條）：只放行 Read，不得出現 Write／Bash／連網工具。

        走資料檔後 CLI 必須讀得到檔，故白名單從空放寬到 Read——這是最小必要放寬。
        以實跑攔 argv 驗證，不查程式碼字串（避免被註解餵飽的假性通過）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli()

            def _fake_resolve(bov, *, root=None):
                return run_dir

            def _fake_build_ppt(*, report_dir, approvals_path, output_dir, theme_path=None):
                pptx = Path(output_dir) / (run_dir.name + ".pptx")
                pptx.write_bytes(b"PK")
                return {"pptx_path": str(pptx), "manifest_path": "", "manifest": {}}

            runner_mod.run_report_ppt(
                based_on_version=None, cli_runner=cli, resolve_run_dir=_fake_resolve,
                build_ppt=_fake_build_ppt, upload_run_dir=lambda rd: 1,
                payload_root=Path(tmp) / "payloads")
        argv = cli.calls[0]
        self.assertIn("--allowedTools", argv)
        tools = argv[argv.index("--allowedTools") + 1]
        self.assertEqual(tools, "Read", "主路徑白名單應恰為 Read")
        for banned in ("WebSearch", "WebFetch", "Write", "Bash", "Glob", "Grep"):
            self.assertNotIn(banned, tools)


# ── 預設 build_ppt 子程序路徑 ─────────────────────────────────────


class DefaultBuildPptSubprocessTests(unittest.TestCase):
    """預設 build_ppt 子程序輸出解析要有防呆，不能因 stdout/stderr 為 None 蓋掉真因。"""

    def test_success_without_stdout_reports_missing_pptx_path(self):
        """實機回報 stdout=None 時，不得噴 AttributeError: splitlines。"""
        completed = subprocess.CompletedProcess(args=["uv"], returncode=0)
        completed.stdout = None
        completed.stderr = None
        with mock.patch.object(runner_mod.subprocess, "run", return_value=completed):
            with self.assertRaises(runner_mod.ReportPptRunnerError) as ctx:
                runner_mod._default_build_ppt(
                    report_dir=Path("report"),
                    approvals_path=Path("approvals.json"),
                    output_dir=Path("output"),
                )
        msg = str(ctx.exception)
        self.assertIn("build_ppt 未回報 pptx 路徑", msg)
        self.assertIn("stdout 為空", msg)
        self.assertNotIn("AttributeError", msg)
        self.assertNotIn("splitlines", msg)

    def test_failure_without_stderr_reports_exit_code(self):
        """子程序失敗且 stderr/stdout 都是 None 時，也要回可讀 exit code。"""
        completed = subprocess.CompletedProcess(args=["uv"], returncode=2)
        completed.stdout = None
        completed.stderr = None
        with mock.patch.object(runner_mod.subprocess, "run", return_value=completed):
            with self.assertRaises(runner_mod.ReportPptRunnerError) as ctx:
                runner_mod._default_build_ppt(
                    report_dir=Path("report"),
                    approvals_path=Path("approvals.json"),
                    output_dir=Path("output"),
                )
        msg = str(ctx.exception)
        self.assertIn("build_ppt 子行程失敗（exit=2）", msg)
        self.assertIn("stdout/stderr 皆為空", msg)


# ── 全庫也能產 PPT（build_ppt 對全庫不設限） ───────────────────────


class GlobalWorkspaceAllowedTests(unittest.TestCase):
    """全庫整條線都有 PPT，只市場章節第7/9/10頁在全庫空著——runner 不對全庫設限。"""

    def test_runner_has_no_global_rejection(self):
        """report_ppt runner 不因全庫 raise（與市場摘要不同：PPT 全庫可產）。"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            cli = RecordingCli()
            build_calls, uploaded = [], []

            def _fake_resolve(bov, *, root=None):
                return run_dir

            def _fake_build_ppt(*, report_dir, approvals_path, output_dir, theme_path=None):
                pptx = Path(output_dir) / (run_dir.name + ".pptx")
                pptx.write_bytes(b"PK\x03\x04")
                return {"pptx_path": str(pptx), "manifest_path": "", "manifest": {}}

            def _fake_upload(rd):
                return 1
            # workspace_id 帶全庫值也不 raise、照產。
            result = runner_mod.run_report_ppt(
                based_on_version=None, workspace_id=99, cli_runner=cli,
                resolve_run_dir=_fake_resolve, build_ppt=_fake_build_ppt,
                upload_run_dir=_fake_upload, payload_root=Path(tmp) / "payloads")
            self.assertEqual(result["pptx_filename"], run_dir.name + ".pptx")


# ── 進度 ───────────────────────────────────────────────────────────


class ProgressTests(unittest.TestCase):
    """AI 任務要有 0→100 進度與階段文字，不可無限 spinner。"""

    def test_progress_advances_to_100(self):
        """回報進度單調遞增、落在 0–100、最後到 100。"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_report_dir(tmp)
            seen = []

            def _fake_resolve(bov, *, root=None):
                return run_dir

            def _fake_build_ppt(*, report_dir, approvals_path, output_dir, theme_path=None):
                pptx = Path(output_dir) / (run_dir.name + ".pptx")
                pptx.write_bytes(b"PK")
                return {"pptx_path": str(pptx), "manifest_path": "", "manifest": {}}

            runner_mod.run_report_ppt(
                based_on_version=None, cli_runner=RecordingCli(),
                resolve_run_dir=_fake_resolve, build_ppt=_fake_build_ppt,
                upload_run_dir=lambda rd: 1,
                payload_root=Path(tmp) / "payloads",
                progress=lambda stage, percent: seen.append((stage, percent)))
            self.assertTrue(seen)
            percents = [p for _, p in seen]
            self.assertEqual(percents, sorted(percents))
            self.assertTrue(all(0 <= p <= 100 for p in percents))
            self.assertEqual(percents[-1], 100)


if __name__ == "__main__":
    unittest.main()
