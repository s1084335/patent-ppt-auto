"""ai:report_ppt 改走資料檔路線（2026-07-27 使用者定「5 支全改，全部收斂同一套」）。

改的兩個理由：

1. **命令列上限**：報表數據內嵌 prompt，實測 report_data 50KB → argv 51,775 字元、
   200KB → 205,375 字元，Windows CreateProcess 上限 32,767 → **必爆 WinError 206**。
2. **不再截斷資料**：現況用 `text[:20_000]` 硬截斷避免撐爆 context——**超過的報表數據
   直接丟掉**，AI 看不到後半段就寫文案。走資料檔後全量給 CLI 自己 Read，不需截斷。

沿用既有共用核心（`write_payload_file` ＋ `build_cli_command_with_payload`），
與 topic_label／patent_note／irrelevant_filter 同一套。

⚠ 白名單變更：原本 `--allowedTools ""`（空，因資料內嵌不需工具），
改資料檔後 CLI **必須能 Read**，故走共用核心的 `READ_ONLY_TOOLS`（僅 Read）。
安全性仍由任務設計保證：CLI 只讀我們寫的那一個 JSON、不連網、不寫檔
（寫 approvals.json 由 runner 自己做，不交給 CLI）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.worker.ai_narrative_runner import CliResult  # noqa: E402
from tests.ai_payload_test_helpers import read_payload_from_argv  # noqa: E402


class ReportPptPayloadFileTests(unittest.TestCase):
    """argv 只帶指示與路徑，報表數據走檔案。"""

    def _run(self, tmp: Path, *, report_data: dict, captured: dict):
        """跑一次 run_report_ppt，用 fake CLI 攔 argv。"""
        from backend.app.worker import ai_report_ppt_runner as r

        run_dir = tmp / "report_trial_20260727_000000"
        run_dir.mkdir(parents=True)
        (run_dir / "report_data.json").write_text(
            json.dumps(report_data, ensure_ascii=False), encoding="utf-8")

        def _fake_cli(argv, timeout):
            # 回 CliResult（與真實 _subprocess_cli_runner 同契約）；回純字串會在
            # parse_cli_result 炸 AttributeError，測不到本檔要鎖的 argv／payload 行為。
            captured["argv"] = list(argv)
            slots = {key: f"{key} 的文案" for key in r.report_slot_keys()}
            return CliResult(
                exit_code=0,
                stdout=json.dumps(
                    {"result": json.dumps({"slots": slots}, ensure_ascii=False)}),
                stderr="")

        return r.run_report_ppt(
            based_on_version=run_dir.name,
            cli_kind="claude",
            cli_runner=_fake_cli,
            resolve_run_dir=lambda _v: run_dir,
            upload_run_dir=lambda _d: 0,
            build_ppt=lambda **k: {"pptx_path": str(run_dir / "out.pptx")},
            payload_root=tmp / "payloads",
        )

    def test_argv_stays_short_with_large_report_data(self):
        """報表數據 200KB 時 argv 仍遠小於 Windows 上限。"""
        captured: dict = {}
        big = {"sections": [{"key": f"s{i}", "rows": [{"a": "x" * 200}] * 20}
                            for i in range(50)]}
        with TemporaryDirectory() as td:
            self._run(Path(td), report_data=big, captured=captured)
        argv_len = sum(len(a) for a in captured["argv"])
        self.assertLess(
            argv_len, 4000,
            f"argv {argv_len} 字元——資料應走檔案而非命令列（Windows 上限 32,767）")

    def test_payload_file_carries_full_report_data(self):
        """資料檔要帶**完整**報表數據，不得截斷（原本 text[:20_000] 會丟掉後半）。"""
        captured: dict = {}
        marker = "END_OF_REPORT_MARKER"
        big = {
            "sections": [{"key": f"s{i}", "rows": [{"a": "y" * 300}] * 20}
                         for i in range(40)],
            "tail_marker": marker,
        }
        with TemporaryDirectory() as td:
            self._run(Path(td), report_data=big, captured=captured)
            payload = read_payload_from_argv(captured["argv"])
        self.assertTrue(payload, "argv 內找不到資料檔")
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertGreater(len(blob), 20_000, "資料檔應帶全量，不是截斷後的 20K")
        self.assertIn(marker, blob, "報表數據尾端被截斷——AI 看不到後半段就寫文案")

    def test_payload_contains_slot_keys_and_instruction(self):
        """資料檔要含 slot 清單與 instruction（CLI 依此作業）。"""
        captured: dict = {}
        with TemporaryDirectory() as td:
            self._run(Path(td), report_data={"sections": []}, captured=captured)
            payload = read_payload_from_argv(captured["argv"])
        self.assertIn("instruction", payload)
        self.assertIn("output_contract", payload)
        self.assertTrue(payload.get("slot_keys"), "缺 slot_keys，CLI 不知要產哪些槽")

    def test_payload_uses_runtime_content_rules_file(self):
        """AI 文案 prompt 必須吃專案 runtime rules 檔，不能只靠 runner 內建舊規則。

        ⚠ 判準是「blob 就是那個檔的內容」，不是逐句比對措辭。原本逐條 assert 十七個
        句子，等於把規則檔的**文字**凍結在測試裡：規則一改寫測試就紅，改測試又等於
        把規則抄了第二份——正是這支測試想擋的「第二落點」。故改成檔案同一性比對，
        另外只釘住不論怎麼改寫都不能消失的護欄語意。
        """
        captured: dict = {}
        with TemporaryDirectory() as td:
            self._run(Path(td), report_data={"sections": []}, captured=captured)
            payload = read_payload_from_argv(captured["argv"])
        rules_blob = "\n".join(payload.get("rules", []))

        rules_file = (
            Path(__file__).resolve().parents[1]
            / "skills" / "patent-report-ppt" / "report_ppt_content_rules.md"
        )
        self.assertIn(
            rules_file.read_text(encoding="utf-8").strip(), rules_blob,
            "payload 的 rules 不是 runtime 規則檔的內容（runner 又內建了一份？）",
        )
        # 護欄語意：合法槽位、品質標準、超譯禁令、缺漏不入 PPT、象限判讀口徑。
        for guardrail in (
            "cover.title", "direction.body", "內容品質標準",
            "產品核心度", "競爭者是否已進場",
            "不得在 PPT 中提示缺漏", "主題代碼", "象限判讀",
        ):
            self.assertIn(guardrail, rules_blob, f"規則檔缺少護欄：{guardrail}")

    def test_cli_can_read_the_payload_file(self):
        """白名單必須放行 Read——資料在檔案裡，CLI 讀不到就什麼都做不了。"""
        captured: dict = {}
        with TemporaryDirectory() as td:
            self._run(Path(td), report_data={"sections": []}, captured=captured)
        argv = captured["argv"]
        self.assertIn("--allowedTools", argv)
        tools = argv[argv.index("--allowedTools") + 1]
        self.assertIn("Read", tools, "資料檔路線的 CLI 必須能 Read")
        for forbidden in ("WebSearch", "WebFetch", "Write", "Bash"):
            self.assertNotIn(
                forbidden, tools,
                f"白名單不得放行 {forbidden}——只需讀那一個 JSON")

    def test_no_truncation_constant_left(self):
        """不再需要 char_limit 截斷；殘留常數代表還有路徑在截。"""
        src = (PROJECT_ROOT / "backend" / "app" / "worker"
               / "ai_report_ppt_runner.py").read_text(encoding="utf-8")
        self.assertTrue(
            "text[:char_limit]" not in src,
            "仍有截斷邏輯——走資料檔後應給全量")


if __name__ == "__main__":
    unittest.main()
