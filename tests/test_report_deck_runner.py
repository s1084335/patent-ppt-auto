"""`ai_report_deck_runner` 的編排契約（add-deck-delivery-line tasks 3.1）。

## 這條 runner 在系統裡的位置

前端「匯出報告」頁按「產製簡報」→ `ai:report_deck` job → Companion ai_bridge 領取
→ 本 runner。design §1 定案的分工：**runner 驅動機械步（subprocess、結構化
exit code 當閘門），CLI 只接撰稿與目視迴圈**——CLI 沒有 Bash 白名單，
機械步本來就是確定性程式。

## 測試分兩層（design 4-0b，2026-08-13 使用者定案）

單元測試（本檔）注入 fake `step_runner`／`cli_runner`——驗**編排**：步序、
短路、迴圈、回存。⚠ fake 驗不了「CLI 起不起得來、認證過不過、Chromium 在不在」
——那三件交組合驗收用真 CLI 實跑（tasks 4.2）。

## 契約重點

- 機械步依序：assemble → plan →（plan 有標記才 chip）→ fit；任一步非零
  **即 failed 短路**，後面的步不跑。
- 撰稿 CLI 的唯一輸出＝`work/content.json`；沒產出＝失敗。
- 目視迴圈（design §1）：每輪＝check → make → audit → shoot → CLI 看圖。
  check／make 非零＝**內容問題，走同一個迴圈**（不另設重試路）；
  audit 非零＝引擎違約，**硬失敗**。CLI 看圖後寫 verdict；不過且
  content.json 沒改＝停滯，立即失敗。達 `max_visual_rounds` 即失敗並附
  最後一輪發現。
- **失敗不落半成品**：全閘門過才把 pptx＋PNG 搬進 artifact root。
- manifest：based_on_version、相對 key、SHA-256、輪次紀錄——DB 只存相對 key。
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.worker import ai_report_deck_runner as deck
from backend.app.worker.cli_gateway import CliResult


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class FakeSteps:
    """假機械步：依步名產出下游需要的最小檔案，並記錄呼叫順序。

    ⚠ 假步驟必須**真的落檔**——runner 對「上一步有沒有產出」的檢查是契約的
    一部分，不落檔就測不到那些檢查。
    """

    def __init__(self, work: Path, *, fail_step: str | None = None,
                 fail_times: int = 99, plan_chips: list[str] | None = None,
                 make_fail_times: int = 0, check_fail_times: int = 0):
        self.work = work
        self.calls: list[str] = []
        self.fail_step = fail_step
        self.fail_times = fail_times
        self.plan_chips = plan_chips or []
        self.make_fail_times = make_fail_times
        self.check_fail_times = check_fail_times

    def __call__(self, step: str, argv: list[str]) -> tuple[int, str]:
        self.calls.append(step)
        fails_left = self.fail_times if step == self.fail_step else 0
        if fails_left and self.calls.count(self.fail_step) <= self.fail_times:
            return 1, f"{step} 假失敗"
        if step == "assemble":
            _write(self.work / "report.json", json.dumps({
                "report_meta": {"workspace_name": "滑雪機", "h1": "滑雪機"},
                "sections": []}))
            _write(self.work / "charts" / "a.svg", "<svg/>")
        elif step == "plan":
            _write(self.work / "plan.json", json.dumps(
                {"pages": [], "rebuildable_chip_chart": self.plan_chips}))
        elif step == "fit":
            _write(self.work / "png" / "a.png")
            _write(self.work / "png" / "font_choice.json", "{}")
        elif step == "check":
            if self.calls.count("check") <= self.check_fail_times:
                return 1, "P3 判讀帶超長（假閘門紅）"
        elif step == "make":
            if self.calls.count("make") <= self.make_fail_times:
                return 1, "⚠ 有 1 個問題（版面溢出或圖內字級不足）"
            _write(self.work / "deck.pptx", "PPTX")
            _write(self.work / "svg" / "page01.svg", "<svg/>")
            _write(self.work / "svg" / "page02.svg", "<svg/>")
        elif step == "shoot":
            _write(self.work / "shots" / "page01.png", "PNG1")
            _write(self.work / "shots" / "page02.png", "PNG2")
        return 0, f"{step} ok"


class FakeCli:
    """假 CLI：第一次呼叫＝撰稿（寫 content.json），之後＝目視（寫 verdict）。

    `verdicts` 逐輪出隊；`edit_on_fail=True` 時不過的那輪會改 content.json
    （模擬 CLI 依發現修稿）。
    """

    def __init__(self, work: Path, *, write_content: bool = True,
                 verdicts: list[dict] | None = None, edit_on_fail: bool = True):
        self.work = work
        self.write_content = write_content
        self.verdicts = list(verdicts or [{"pass": True, "findings": []}])
        self.edit_on_fail = edit_on_fail
        self.prompts: list[str] = []

    def __call__(self, argv: list[str], timeout: float) -> CliResult:
        prompt = argv[2]  # [binary, -p, prompt, ...]
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            if self.write_content:
                _write(self.work / "content.json", json.dumps({"v": 0}))
            return CliResult(0, '{"result": "content written"}', "")
        if "閘門輸出" in prompt:
            # 修稿輪：依閘門輸出改 content.json（不寫 verdict——那是目視輪的產物）
            content = self.work / "content.json"
            data = json.loads(content.read_text(encoding="utf-8"))
            data["v"] = data.get("v", 0) + 1
            content.write_text(json.dumps(data), encoding="utf-8")
            return CliResult(0, '{"result": "fixed"}', "")
        verdict = self.verdicts.pop(0) if self.verdicts else {"pass": True, "findings": []}
        _write(self.work / "visual_verdict.json", json.dumps(verdict, ensure_ascii=False))
        if not verdict.get("pass") and self.edit_on_fail:
            content = self.work / "content.json"
            data = json.loads(content.read_text(encoding="utf-8"))
            data["v"] = data.get("v", 0) + 1
            content.write_text(json.dumps(data), encoding="utf-8")
        return CliResult(0, '{"result": "reviewed"}', "")


class DeckRunnerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "reports"
        self.run_dir = self.root / "report_trial_20990101_000000"
        _write(self.run_dir / "report_data.json", "{}")
        # 預設情境＝版本已有解讀（narrative 前置只在缺檔時觸發，專測另控）
        _write(self.run_dir / "narratives.json", "{}")
        _write(self.run_dir / "version_meta.json",
               json.dumps({"workspace_id": 3, "workspace_name": "滑雪機"}))
        self.work_root = base / "deck_work"
        self.artifact_root = base / "artifacts"
        self.work = self.work_root / self.run_dir.name

    def _run(self, *, steps: FakeSteps | None = None, cli: FakeCli | None = None,
             **kw):
        steps = steps or FakeSteps(self.work)
        cli = cli or FakeCli(self.work)
        summary = deck.run_deck(
            self.run_dir.name,
            root=self.root,
            work_root=self.work_root,
            artifact_root=self.artifact_root,
            step_runner=steps,
            cli_runner=cli,
            **kw,
        )
        return summary, steps, cli

    # ── 機械步編排 ──────────────────────────────────────────

    def test_happy_path_step_order(self):
        """⚠ 2026-08-19（§6.2b）新增兩步，位置都有硬理由，**不得調換**：

        - `recolor` 在 `fit` **之前**：fit 產的 PNG 就是進投影片的畫素，
          之後再換色來不及。（也必須在 `chip` 之後——見下一條測試。）
        - `recolor_check` 在 `marks` **之後**：marks 還會再動一次 SVG，
          只在換色當下驗等於驗了一份不是最終產物的東西。
        """
        summary, steps, cli = self._run()
        self.assertEqual(
            steps.calls,
            ["assemble", "plan", "recolor", "fit", "marks", "recolor_check",
             "check", "make", "audit", "shoot"])
        self.assertLess(steps.calls.index("recolor"), steps.calls.index("fit"),
                        "換色跑在 fit 之後＝PNG 已經是報表色，換了也沒用")
        self.assertLess(steps.calls.index("marks"), steps.calls.index("recolor_check"),
                        "換色檢查跑在 marks 之前＝驗的不是最終產物")
        # CLI 恰兩次：撰稿＋目視通過
        self.assertEqual(len(cli.prompts), 2)
        self.assertEqual(summary["visual_rounds"], 1)

    def test_chip_rebuild_runs_only_when_plan_marks(self):
        steps = FakeSteps(self.work, plan_chips=["opportunity_quadrant_tech"])
        _, steps, _ = self._run(steps=steps)
        self.assertIn("chip", steps.calls)
        self.assertLess(steps.calls.index("plan"), steps.calls.index("chip"))
        self.assertLess(steps.calls.index("chip"), steps.calls.index("fit"))
        # ⚠ §6.2b：換色必須在 chip **之後**——`rebuild_chip_chart` 會寫入
        #   報表側的色（實測 5 處 #00094A），先換色等於漏掉重排過的那幾張。
        self.assertLess(steps.calls.index("chip"), steps.calls.index("recolor"),
                        "換色跑在 chip 之前＝重排寫進去的報表色不會被換掉")

    def test_mechanical_failure_short_circuits(self):
        """plan 非零 → 立即失敗，fit 之後一步都不跑，CLI 一次都不呼叫。"""
        steps = FakeSteps(self.work, fail_step="plan")
        cli = FakeCli(self.work)
        with self.assertRaises(deck.DeckRunnerError) as ctx:
            self._run(steps=steps, cli=cli)
        self.assertIn("plan", str(ctx.exception))
        self.assertNotIn("fit", steps.calls)
        self.assertEqual(cli.prompts, [])
        # 失敗不落半成品
        self.assertFalse(self.artifact_root.exists())

    def test_cli_without_content_json_fails(self):
        cli = FakeCli(self.work, write_content=False)
        with self.assertRaises(deck.DeckRunnerError) as ctx:
            self._run(cli=cli)
        self.assertIn("content.json", str(ctx.exception))

    # ── 目視迴圈 ────────────────────────────────────────────

    def test_check_red_consumes_visual_round(self):
        """check_content 閘門紅走同一個迴圈：CLI 收到閘門輸出修稿，下一輪重跑。"""
        steps = FakeSteps(self.work, check_fail_times=1)
        cli = FakeCli(self.work, verdicts=[{"pass": True, "findings": []}])
        summary, steps, cli = self._run(steps=steps, cli=cli)
        # check 紅一次 → 修稿輪；第二輪 check 過 → make → … → 目視過
        self.assertEqual(summary["visual_rounds"], 2)
        self.assertEqual(steps.calls.count("check"), 2)
        # 修稿 CLI 收到閘門輸出
        self.assertTrue(any("判讀帶超長" in p for p in cli.prompts))

    def test_make_red_consumes_visual_round(self):
        """make_deck 非零（溢出／字級不足）＝內容問題，同一個迴圈。"""
        steps = FakeSteps(self.work, make_fail_times=1)
        summary, steps, _ = self._run(steps=steps)
        self.assertEqual(summary["visual_rounds"], 2)
        self.assertEqual(steps.calls.count("make"), 2)

    def test_audit_failure_is_hard_fail(self):
        """audit 非零＝引擎違約（字級白名單外），不是內容問題——不迴圈，直接失敗。"""
        steps = FakeSteps(self.work, fail_step="audit")
        with self.assertRaises(deck.DeckRunnerError) as ctx:
            self._run(steps=steps)
        self.assertIn("audit", str(ctx.exception))
        self.assertFalse(self.artifact_root.exists())

    def test_visual_findings_trigger_rebuild_then_pass(self):
        cli = FakeCli(self.work, verdicts=[
            {"pass": False, "findings": ["P2 行首頓號"]},
            {"pass": True, "findings": []},
        ])
        summary, steps, cli = self._run(cli=cli)
        self.assertEqual(summary["visual_rounds"], 2)
        self.assertEqual(steps.calls.count("make"), 2)   # 修稿後重組版
        self.assertEqual(steps.calls.count("shoot"), 2)  # 重截圖
        # 每輪紀錄都要留（design §3：成功路徑同樣留全程）
        self.assertEqual(len(summary["visual_log"]), 2)
        self.assertIn("P2 行首頓號", json.dumps(summary["visual_log"], ensure_ascii=False))

    def test_max_rounds_fails_with_last_findings(self):
        cli = FakeCli(self.work, verdicts=[
            {"pass": False, "findings": ["發現甲"]},
            {"pass": False, "findings": ["發現乙"]},
        ])
        with self.assertRaises(deck.DeckRunnerError) as ctx:
            self._run(cli=cli, max_visual_rounds=2)
        self.assertIn("發現乙", str(ctx.exception))
        self.assertFalse(self.artifact_root.exists())

    def test_stalled_review_fails(self):
        """verdict 不過但 content.json 一個位元組都沒改＝停滯，不得空轉到上限。"""
        cli = FakeCli(self.work, edit_on_fail=False,
                      verdicts=[{"pass": False, "findings": ["假發現"]}])
        with self.assertRaises(deck.DeckRunnerError) as ctx:
            self._run(cli=cli)
        self.assertIn("content.json", str(ctx.exception))

    # ── 回存 ────────────────────────────────────────────────

    def test_persist_layout_and_manifest(self):
        summary, _, _ = self._run()
        version_dir = self.artifact_root / self.run_dir.name
        pptx = version_dir / "deck.pptx"
        self.assertTrue(pptx.is_file())
        pages = sorted((version_dir / "pages").glob("*.png"))
        self.assertEqual(len(pages), 2)
        manifest = json.loads((version_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["based_on_version"], self.run_dir.name)
        # DB 只存相對 key
        self.assertEqual(manifest["pptx_key"], f"{self.run_dir.name}/deck.pptx")
        self.assertEqual(manifest["sha256"],
                         hashlib.sha256(pptx.read_bytes()).hexdigest())
        self.assertEqual(manifest["page_count"], 2)
        # content.json 隨產物保存（可重現輸入）＋manifest 記 hash
        content = version_dir / "content.json"
        self.assertTrue(content.is_file())
        self.assertEqual(manifest["content_sha256"],
                         hashlib.sha256(content.read_bytes()).hexdigest())
        self.assertEqual(summary["pptx_key"], manifest["pptx_key"])
        self.assertEqual(summary["page_keys"],
                         [f"{self.run_dir.name}/pages/{p.name}" for p in pages])

    # ── narrative 前置（2026-08-14 使用者裁決「未產解讀要先產解讀」）──────

    def test_missing_narratives_triggers_narrative_first(self):
        """版本沒有 narratives.json → 先跑 narrative 線再繼續 deck。

        deck 的判讀素材（report.json texts）來自 narratives.json；缺了不擋
        會產出判讀帶空洞的簡報——**靜默品質損失**，比 fail 難發現。
        """
        (self.run_dir / "narratives.json").unlink()   # 本測情境：尚無解讀
        produced: list[str] = []

        def fake_narrative(version, **kw):
            produced.append(version)
            _write(self.run_dir / "narratives.json", "{}")
            return {"based_on_version": version}

        summary, _, _ = self._run(ensure_narrative=fake_narrative)
        self.assertEqual(produced, [self.run_dir.name])
        self.assertTrue(summary["narrative_chained"])

    def test_existing_narratives_not_reproduced(self):
        """已有解讀就不重跑——narrative 燒 CLI token，重跑要由使用者主動按。"""
        _write(self.run_dir / "narratives.json", "{}")
        called: list[str] = []
        summary, _, _ = self._run(
            ensure_narrative=lambda v, **kw: called.append(v))
        self.assertEqual(called, [])
        self.assertFalse(summary["narrative_chained"])

    def test_narrative_chain_failure_short_circuits(self):
        """前置 narrative 失敗＝素材不完整，deck 不得帶著空判讀繼續。"""
        (self.run_dir / "narratives.json").unlink()

        def boom(version, **kw):
            raise RuntimeError("CLI 解讀失敗")

        with self.assertRaises(deck.DeckRunnerError) as ctx:
            self._run(ensure_narrative=boom)
        self.assertIn("解讀", str(ctx.exception))
        self.assertFalse(self.artifact_root.exists())

    # ── 封面素材（tasks 2.4）──────────────────────────────

    def test_workspace_name_injected_into_prompt(self):
        _, _, cli = self._run()
        self.assertIn("滑雪機", cli.prompts[0])

    # ── 誠實進度（tasks 3.4）──────────────────────────────

    def test_progress_is_monotonic_with_stages(self):
        events: list[tuple[str, int]] = []
        self._run(progress=lambda stage, pct: events.append((stage, pct)))
        pcts = [p for _, p in events]
        self.assertEqual(pcts, sorted(pcts), f"進度必須單調遞增：{events}")
        stages = [s for s, _ in events]
        for expected in ("assemble", "fit", "cli_writing", "visual_round_1", "persist"):
            self.assertTrue(any(expected in s for s in stages),
                            f"缺階段 {expected}：{stages}")


class BridgeDispatchTests(unittest.TestCase):
    """ai_bridge 的 `ai:report_deck` handler：payload 注入 fake、誠實進度轉 heartbeat。"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "reports"
        self.run_dir = self.root / "report_trial_20990101_000000"
        _write(self.run_dir / "report_data.json", "{}")
        _write(self.run_dir / "narratives.json", "{}")   # 已有解讀，不觸發前置
        self.work = base / "deck_work" / self.run_dir.name
        self.artifact_root = base / "artifacts"

    def test_handler_runs_with_injected_fakes_and_heartbeats(self):
        from unittest import mock

        from backend.app.worker import ai_bridge

        beats: list[tuple[str, int]] = []

        class Ctx:
            def heartbeat(self, stage=None, progress=None):
                beats.append((stage, progress))

        payload = {
            "based_on_version": self.run_dir.name,
            "_cli_runner": FakeCli(self.work),
            "_step_runner": FakeSteps(self.work),
        }
        real_run_deck = deck.run_deck   # patch 前留原函式，side_effect 才不會遞迴到 mock
        with mock.patch.object(
            deck, "run_deck",
            side_effect=lambda v, **kw: real_run_deck(
                v, **{**kw, "root": self.root,
                      "work_root": self.work.parent,
                      "artifact_root": self.artifact_root}),
        ):
            result = ai_bridge._run_ai_report_deck_job(payload, Ctx())
        self.assertEqual(result["based_on_version"], self.run_dir.name)
        self.assertEqual(result["page_count"], 2)
        # 誠實進度：階段文字是繁中、且含目視輪次
        stages = [s for s, _ in beats]
        self.assertIn("CLI 撰稿中", stages)
        self.assertTrue(any("逐頁目視第 1 輪" in s for s in stages), stages)
        self.assertEqual(beats[-1], ("完成", 100))

    def test_job_type_registered(self):
        """白名單與派工表都要有 `ai:report_deck`（一致性閘門另有專測，這裡驗存在）。"""
        from backend.app.db import job_repository
        from backend.app.worker import ai_bridge

        self.assertIn("ai:report_deck", job_repository.AI_JOB_TYPES)
        self.assertIn("ai:report_deck", ai_bridge._AI_JOB_RUNNERS)


if __name__ == "__main__":
    unittest.main()
