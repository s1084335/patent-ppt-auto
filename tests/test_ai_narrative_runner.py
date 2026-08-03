"""ai_narrative_runner 單元測試：不真跑 CLI，餵假 CLI JSON 輸出驗指令組裝與結果解析。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.worker import ai_narrative_runner as runner
from backend.app.worker import ai_narrative_runner
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
            "象限名稱",
            "象限判讀",
            "後續檢視點",
            "不得只寫成「可人工確認」",
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
            # ⚠ uploader 必須注入 fake：513927c 把 upload_run_dir 加進 run_narrative
            # （跨容器需求）後本測試沒跟著注入，一直在連**真實 DB**。
            # 先前之所以「通過」，是 conninfo 壞掉導致連線瞬間失敗；18ec129 修好池的
            # 參數傳遞後它變成真的嘗試連線，卡 30 秒 PoolTimeout 才失敗。
            # 單元測試不得依賴外部連線——記下上傳呼叫即可。
            uploaded: list[Path] = []
            summary = runner.run_narrative(
                version,
                cli_runner=self._fake_cli_runner(run_dir, version),
                refresh_index=fake_refresh,
                progress=lambda s, p: stages.append((s, p)),
                root=base,
                upload_run_dir=lambda rd: (uploaded.append(rd), 3)[1],
            )
            self.assertEqual(uploaded, [run_dir], "run_dir 未被上傳（跨容器會讀不到）")
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


class NarrativeContractV6Tests(unittest.TestCase):
    """W-1＋C-6：電報體規則把數字砍掉了；放寬形式，改成強制帶依據與意義。

    🔴 W-1 的根因是**我訂的規則**，不是 CLI 偷懶也不是容量不足：
    「不得含句號」→ 一條只能寫一個子句；「逗號至多一個」→
    「A63B 達 47 件，是絕對主體」已用掉唯一逗號，再加依據就違規。
    容量給到 8 條 × 54 字，實際只用 4 條 × 19–22 字。

    🔴 C-6 使用者定調：文字要從「看到什麼數據 → 描述數據」提升為
    「數據代表什麼 → 為何重要 → 對技術布局有何意義」。
    ⚠ 這條若只寫進 prompt 而沒有檢查，就等於沒有規則（AGENTS.md）。
    可程式化的部分有三件：現況要帶數字、每頁至少一條意涵、CPC 要講與 IPC 的差異。
    """

    def _warn(self, points, body, key="ipc_main_distribution"):
        narratives = {"reports": {key: {"variants": {"default": {
            "headline": "測試標題", "points": points, "text": body}}}}}
        return ai_narrative_runner.validate_narrative_contract(narratives)

    @staticmethod
    def _pick(warnings, keyword):
        return [w for w in warnings if keyword in w]

    def test_one_period_is_allowed(self):
        """一個句號代表寫成完整判讀，不是把長文塞進要點。"""
        warns = self._warn(
            [{"label": "現況", "text": "A63B達47件，佔78%，為絕對主體。"}],
            "A63B達47件，佔78%，為絕對主體。")
        self.assertFalse(self._pick(warns, "句號"))

    def test_two_periods_still_rejected(self):
        """⚠ 放寬不等於取消：兩個句號就是串接了兩個論點。"""
        warns = self._warn(
            [{"label": "現況", "text": "A63B達47件。F03G僅2件。"}],
            "A63B達47件。F03G僅2件。")
        self.assertTrue(self._pick(warns, "句號"))

    def test_two_commas_allowed_three_rejected(self):
        ok = self._warn([{"label": "現況", "text": "A63B達47件，佔78%，為主體"}],
                        "A63B達47件，佔78%，為主體")
        self.assertFalse(self._pick(ok, "逗號"))
        bad = self._warn([{"label": "現況", "text": "A達4件，B達3件，C達2件，D達1件"}],
                         "A達4件，B達3件，C達2件，D達1件")
        self.assertTrue(self._pick(bad, "逗號"))

    def test_status_point_must_carry_a_number(self):
        """🔴「現況」是講數據的——沒有數字就只是形容詞。

        實機原句：「IPC大方向幾乎全落在運動訓練器材領域」（19 字，零數字）。
        """
        warns = self._warn(
            [{"label": "現況", "text": "IPC大方向幾乎全落在運動訓練器材領域"}],
            "IPC大方向幾乎全落在運動訓練器材領域")
        self.assertTrue(self._pick(warns, "數字"))

    def test_variant_must_have_an_implication(self):
        """C-6：只描述數據不給意義，就是停在「看到什麼數據」那一層。"""
        points = [{"label": "現況", "text": f"第{i}類達{i}件"} for i in range(1, 5)]
        warns = self._warn(points, "\n\n".join(p["text"] for p in points))
        self.assertTrue(self._pick(warns, "意涵"))

    def test_implication_satisfies_the_rule(self):
        points = [{"label": "現況", "text": "A63B達47件"},
                  {"label": "意涵", "text": "布局集中訓練器材，跨領域延伸僅2件"},
                  {"label": "後續", "text": "第二布局線尚未成形"},
                  {"label": "現況", "text": "F03G僅2件"}]
        warns = self._warn(points, "\n\n".join(p["text"] for p in points))
        self.assertFalse(self._pick(warns, "意涵"))

    def test_cpc_must_contrast_with_ipc(self):
        """🔴 參考報告的 CPC 段落寫的是「與 IPC 分布圖不同的是…」。

        實機 p10／p11 的 CPC 判讀與 p8／p9 的 IPC **逐字相同**——F-4 修好了
        「取到對的 variant」這個機制，但內容上該講什麼差異是解讀層的事。
        """
        points = [{"label": "現況", "text": "CPC僅8件全落在A63B"},
                  {"label": "意涵", "text": "標引覆蓋遠低於樣本稀薄"},
                  {"label": "後續", "text": "訊號量不足應以主分類為主"},
                  {"label": "現況", "text": "A63B-0022達5件"}]
        body = "\n\n".join(p["text"] for p in points)
        warns = self._warn(points, body, key="cpc_main_distribution")
        self.assertTrue(self._pick(warns, "IPC"))

    def test_cpc_mentioning_ipc_passes(self):
        points = [{"label": "現況", "text": "CPC僅8件，遠低於IPC的49件"},
                  {"label": "意涵", "text": "CPC標引覆蓋率不足兩成"},
                  {"label": "後續", "text": "判讀應以IPC為主"},
                  {"label": "現況", "text": "A63B-0022達5件"}]
        warns = self._warn(points, "\n\n".join(p["text"] for p in points),
                           key="cpc_main_distribution")
        self.assertFalse(self._pick(warns, "未與"))

    def test_too_little_content_is_flagged(self):
        """🔴 C-9：版面給 432 字只寫 81 字（18.8%）——資訊在寫的時候就沒進去。

        ⚠ 實測沒有任何一頁被版面裁掉。根因是我 07-31 寫的「容量是上限，不是目標」
        被當成鼓勵留白，而且沒有相對的下限要求。使用者定調：要的是**濃縮**
        （同一段版面塞進更多判讀），不是**丟棄**（把該講的省略掉）。
        """
        cap = {"ipc_main_distribution": {"max_points": 8, "max_chars": 54}}
        points = [{"label": "現況", "text": f"第{i}類達{i}件"} for i in range(1, 4)]
        points.append({"label": "意涵", "text": "布局集中"})
        body = chr(10).join(p["text"] for p in points)
        narratives = {"reports": {"ipc_main_distribution": {"variants": {"L4": {
            "headline": "測試", "points": points, "text": body}}}}}
        warns = ai_narrative_runner.validate_narrative_contract(narratives, cap)
        self.assertTrue([w for w in warns if "偏低" in w or "用量" in w],
                        f"寫太少沒有被標記：{warns}")

    def test_full_enough_content_passes(self):
        """寫到容量六成以上就不該被嫌少——下限不是要求寫滿。"""
        cap = {"ipc_main_distribution": {"max_points": 4, "max_chars": 30}}
        points = [{"label": "現況", "text": "A63B體育訓練器材次分類達47件為絕對主體"},
                  {"label": "意涵", "text": "技術布局幾乎全落在運動訓練器材這一線"},
                  {"label": "後續", "text": "跨領域延伸僅2件尚未形成第二布局線"},
                  {"label": "現況", "text": "F03G彈力發動機僅2件為唯一跨界分類"}]
        body = chr(10).join(p["text"] for p in points)
        narratives = {"reports": {"ipc_main_distribution": {"variants": {"L4": {
            "headline": "測試", "points": points, "text": body}}}}}
        warns = ai_narrative_runner.validate_narrative_contract(narratives, cap)
        self.assertFalse([w for w in warns if "偏低" in w or "用量" in w], warns)

    def test_other_reports_are_not_asked_to_mention_ipc(self):
        """⚠ 只有 CPC 需要對照 IPC；別的報表不得被這條規則波及。"""
        points = [{"label": "現況", "text": "CN受理39件"},
                  {"label": "意涵", "text": "申請足跡高度集中中國"},
                  {"label": "後續", "text": "海外保護偏薄"},
                  {"label": "現況", "text": "EP僅3件"}]
        warns = self._warn(points, "\n\n".join(p["text"] for p in points),
                           key="country_distribution")
        self.assertFalse(self._pick(warns, "IPC"))


if __name__ == "__main__":
    unittest.main()
