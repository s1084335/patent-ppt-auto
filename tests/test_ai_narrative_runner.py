"""ai_narrative_runner 單元測試：不真跑 CLI，餵假 CLI JSON 輸出驗指令組裝與結果解析。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.worker import ai_narrative_runner as runner
from backend.app.worker.ai_narrative_runner import CliResult, NarrativeRunnerError


def _write_run_dir(base: Path, version: str, *, with_report_data: bool = True) -> Path:
    """建立一個假的報表版本目錄（含 report_data.json）供解析測試使用。"""
    run_dir = base / version
    run_dir.mkdir(parents=True)
    if with_report_data:
        (run_dir / "report_data.json").write_text(
            json.dumps({"sections": []}), encoding="utf-8"
        )
    return run_dir


class ResolveSkillPathTests(unittest.TestCase):
    """skill 路徑解析：淺 PROJECT_ROOT（容器 /app）不得於 import 期 IndexError。"""

    def test_skill_path_can_be_overridden_by_environment(self):
        """正式部署可用 REPORT_NARRATIVE_FLOW_PATH 指到 repo／掛載後的 narrative 規格檔。"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = Path(tmp) / "report-narrative-flow.md"
            rules_path.write_text("# rules", encoding="utf-8")
            with mock.patch.dict("os.environ", {"REPORT_NARRATIVE_FLOW_PATH": str(rules_path)}):
                self.assertEqual(runner._resolve_skill_path(), rules_path.resolve())

    def test_skill_path_does_not_fallback_to_agents_directory(self):
        """不能掃祖先 .agents；本機舊規格不得掩蓋正式部署缺 repo 檔。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            agents_path = root.parent / ".agents" / "skills" / "report-narrative-flow.md"
            agents_path.parent.mkdir(parents=True)
            agents_path.write_text("# stale", encoding="utf-8")
            expected = root / "skills" / "patent-report-ppt" / "report-narrative-flow.md"
            with mock.patch.object(runner, "PROJECT_ROOT", root):
                with mock.patch.dict("os.environ", {}, clear=True):
                    self.assertEqual(runner._resolve_skill_path(), expected)

    def test_shallow_project_root_does_not_raise(self):
        """PROJECT_ROOT 為淺路徑（如容器 /app，parents 深度不足）時安全回退不炸。"""
        from pathlib import PurePosixPath
        import backend.app.worker.ai_narrative_runner as mod

        # 模擬容器根 /app（parents 只有一層 '/')；舊碼 parents[1] 會 IndexError。
        orig = mod.PROJECT_ROOT
        try:
            mod.PROJECT_ROOT = type(orig)("/app") if hasattr(orig, "parents") else orig
            # 直接呼叫；不得 raise，回傳 Path（存在與否不重要）。
            result = mod._resolve_skill_path()
            self.assertIsNotNone(result)
        finally:
            mod.PROJECT_ROOT = orig

    def test_default_skill_path_exists_in_repo(self):
        """預設 narrative 規格檔必須隨 repo/Docker image 出貨，不依賴本機 .agents。"""
        path = runner.PROJECT_ROOT / "skills" / "patent-report-ppt" / "report-narrative-flow.md"
        self.assertTrue(path.exists(), f"missing repo narrative spec: {path}")

    def test_default_skill_path_contains_narrative_quality_rules(self):
        """narrative 規格必須保留各報表解讀重點、口徑守則、主題代碼不入文。"""
        path = runner.PROJECT_ROOT / "skills" / "patent-report-ppt" / "report-narrative-flow.md"
        text = path.read_text(encoding="utf-8")
        for expected in (
            "各報表解讀重點",
            "口徑守則",
            "主題代碼不入文",
            "缺資料報表不得入文",
            "競爭者是否已進場",
            "不等於產品核心度",
        ):
            self.assertIn(expected, text)

    def test_default_skill_path_contains_specific_report_interpretation_rules(self):
        """narrative 規格必須鎖住特定報表的解讀口徑，避免 AI 超譯或重複。"""
        path = runner.PROJECT_ROOT / "skills" / "patent-report-ppt" / "report-narrative-flow.md"
        text = path.read_text(encoding="utf-8")
        for expected in (
            "L4/L5 兩變體各自成段",
            "L5 講細分類集中與斷層，不重複 L4",
            "技術意義",
            "family_country_layout",
            "country_distribution",
            "家族去重",
            "只算存活",
            "含死案",
            "不得超譯成「技術過時」",
            "年度矩陣",
            "主表與「更多」各自成段",
            "現象＋關注點",
            "不能過度擴大",
            "不能只講數據",
        ):
            self.assertIn(expected, text)


class ResolveRunDirTests(unittest.TestCase):
    """解析要解讀的報表版本目錄。"""

    def test_given_version_resolves_that_dir(self):
        """指定 based_on_version 時回該版本子目錄。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = _write_run_dir(base, "report_trial_20260722_001036")
            resolved = runner.resolve_run_dir("report_trial_20260722_001036", root=base)
            self.assertEqual(resolved, run_dir)

    def test_missing_version_picks_latest_report_trial(self):
        """未指定版本時取最新（名稱排序末位）的 report_trial_ 目錄。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_run_dir(base, "report_trial_20260722_001036")
            latest = _write_run_dir(base, "report_trial_20260722_010000")
            resolved = runner.resolve_run_dir(None, root=base)
            self.assertEqual(resolved, latest)

    def test_invalid_dir_without_report_data_raises(self):
        """目錄缺 report_data.json 時 raise（不猜路徑）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_run_dir(base, "report_trial_bad", with_report_data=False)
            with self.assertRaises(NarrativeRunnerError):
                runner.resolve_run_dir("report_trial_bad", root=base)

    def test_no_candidate_raises(self):
        """full_report_latest 無有效 report_trial_ 目錄時 raise。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(NarrativeRunnerError):
                runner.resolve_run_dir(None, root=Path(tmp))


class BuildCliCommandTests(unittest.TestCase):
    """指令組裝：雙 CLI 可換、提示插在二進位後。"""

    def test_claude_command_shape(self):
        """預設 claude：claude -p <prompt> --output-format json --allowedTools ...。"""
        argv = runner.build_cli_command("claude", "PROMPT")
        self.assertEqual(argv[0], "claude")
        self.assertEqual(argv[1], "-p")
        self.assertEqual(argv[2], "PROMPT")
        self.assertIn("--output-format", argv)
        self.assertIn("json", argv)

    def test_opencode_command_switchable(self):
        """cli_kind 可換成 opencode，二進位隨對照表切換，不寫死 claude。"""
        argv = runner.build_cli_command("opencode", "PROMPT")
        self.assertEqual(argv[0], "opencode")
        self.assertIn("PROMPT", argv)
        self.assertNotIn("claude", argv)

    def test_unknown_cli_kind_raises(self):
        """未知 cli_kind 明確 raise，不默默 fallback。"""
        with self.assertRaises(NarrativeRunnerError):
            runner.build_cli_command("unknown-cli", "PROMPT")

    def test_model_flag_inserted_when_given(self):
        """給 model 時插入該 CLI 的 model 旗標＋值（由任務 payload 帶下來，不寫死）。"""
        argv = runner.build_cli_command("claude", "PROMPT", model="claude-opus-4-8")
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-4-8")
        # opencode 也能帶 model（旗標名由對照表決定）
        oc = runner.build_cli_command("opencode", "PROMPT", model="deepseek-v4-flash")
        self.assertIn("deepseek-v4-flash", oc)

    def test_model_omitted_when_none(self):
        """未給 model 時不插旗標（用 CLI 預設模型），維持既有行為。"""
        argv = runner.build_cli_command("claude", "PROMPT")
        self.assertNotIn("--model", argv)


class ParseCliResultTests(unittest.TestCase):
    """結果解析：餵假 CLI JSON 輸出驗解析正確與錯誤處理。"""

    def test_parses_valid_json(self):
        """退出碼 0 且 stdout 為 JSON 物件時解析成功。"""
        result = CliResult(exit_code=0, stdout='{"result": "ok", "num_turns": 3}', stderr="")
        parsed = runner.parse_cli_result(result)
        self.assertEqual(parsed["result"], "ok")
        self.assertEqual(parsed["num_turns"], 3)

    def test_nonzero_exit_raises_with_stderr(self):
        """退出碼非 0 時 raise 並帶 stderr。"""
        result = CliResult(exit_code=1, stdout="", stderr="cli boom")
        with self.assertRaises(NarrativeRunnerError) as ctx:
            runner.parse_cli_result(result)
        self.assertIn("cli boom", str(ctx.exception))

    def test_non_json_output_raises(self):
        """stdout 非合法 JSON 時 raise 並保留原始輸出。"""
        result = CliResult(exit_code=0, stdout="not json at all", stderr="")
        with self.assertRaises(NarrativeRunnerError):
            runner.parse_cli_result(result)

    def test_empty_output_raises(self):
        """退出碼 0 但無輸出時 raise。"""
        result = CliResult(exit_code=0, stdout="   ", stderr="")
        with self.assertRaises(NarrativeRunnerError):
            runner.parse_cli_result(result)


class RunNarrativeOrchestrationTests(unittest.TestCase):
    """整條系統化：組提示 → fake CLI 產 narratives.json → fake refresh-index → 回傳摘要。"""

    def _fake_cli_runner(self, run_dir: Path, version: str, *, exit_code: int = 0):
        """回傳一個 fake CLI runner：模擬 CLI 寫出 narratives.json 並回 JSON 結果。"""

        def _runner(argv, timeout):
            (run_dir / "narratives.json").write_text(
                json.dumps({"based_on_version": version, "reports": {}}),
                encoding="utf-8",
            )
            return CliResult(exit_code=exit_code, stdout='{"result": "done"}', stderr="")

        return _runner

    def test_run_narrative_happy_path(self):
        """fake CLI 產 narratives.json、fake refresh 回覆蓋統計，回傳摘要含版本與覆蓋數。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            version = "report_trial_20260722_010000"
            run_dir = _write_run_dir(base, version)
            stages: list[tuple[str, int]] = []

            fake_refresh = lambda rd: {  # noqa: E731 測試用簡短假函式
                "narrated": 12,
                "variants_total": 14,
                "pending": ["x:default"],
                "narratives_expired": False,
            }
            summary = runner.run_narrative(
                version,
                cli_runner=self._fake_cli_runner(run_dir, version),
                refresh_index=fake_refresh,
                progress=lambda s, p: stages.append((s, p)),
                root=base,
            )
            self.assertEqual(summary["based_on_version"], version)
            self.assertEqual(summary["narrated"], 12)
            self.assertEqual(summary["variants_total"], 14)
            self.assertEqual(summary["pending"], ["x:default"])
            self.assertEqual(summary["cli_kind"], "claude")
            # runner 內 CLI 進度階段：cli_running 30 → 85。
            self.assertIn(("cli_running", 30), stages)
            self.assertIn(("cli_running", 85), stages)

    def test_run_narrative_missing_narratives_raises(self):
        """CLI 正常結束但未產出 narratives.json 時 raise。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            version = "report_trial_20260722_010000"
            _write_run_dir(base, version)

            def _cli_no_output(argv, timeout):
                return CliResult(exit_code=0, stdout='{"result": "done"}', stderr="")

            with self.assertRaises(NarrativeRunnerError):
                runner.run_narrative(
                    version,
                    cli_runner=_cli_no_output,
                    refresh_index=lambda rd: {},
                    root=base,
                )

    def test_run_narrative_version_mismatch_raises(self):
        """narratives.json based_on_version 與目錄版本不符時 raise（解讀過期）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            version = "report_trial_20260722_010000"
            run_dir = _write_run_dir(base, version)

            def _cli_wrong_version(argv, timeout):
                (run_dir / "narratives.json").write_text(
                    json.dumps({"based_on_version": "report_trial_OLD"}),
                    encoding="utf-8",
                )
                return CliResult(exit_code=0, stdout='{"result": "done"}', stderr="")

            with self.assertRaises(NarrativeRunnerError):
                runner.run_narrative(
                    version,
                    cli_runner=_cli_wrong_version,
                    refresh_index=lambda rd: {},
                    root=base,
                )

    def test_run_narrative_cli_failure_raises(self):
        """CLI 退出碼非 0 時 raise（不硬通）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            version = "report_trial_20260722_010000"
            run_dir = _write_run_dir(base, version)

            def _cli_fail(argv, timeout):
                return CliResult(exit_code=2, stdout="", stderr="login required")

            with self.assertRaises(NarrativeRunnerError):
                runner.run_narrative(
                    version,
                    cli_runner=_cli_fail,
                    refresh_index=lambda rd: {},
                    root=base,
                )

    def test_default_subprocess_runner_raises_when_cli_absent(self):
        """未注入 runner 且環境無該 CLI 二進位時 raise 清楚錯誤（測試環境無 claude）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            version = "report_trial_20260722_010000"
            _write_run_dir(base, version)
            # 用一定不存在的 cli_kind binary 觸發 shutil.which 為 None 的路徑。
            with self.assertRaises(NarrativeRunnerError):
                runner.run_narrative(
                    version,
                    cli_kind="opencode",  # 測試環境不會裝 opencode
                    refresh_index=lambda rd: {},
                    root=base,
                )


if __name__ == "__main__":
    unittest.main()
