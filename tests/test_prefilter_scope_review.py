"""初階篩選：AI 對命中專利建議留或剔（切片 C-2，PRE-008）。

## 本檔守的四件事

1. **沒有範圍描述就不准跑**——沒有判讀依據卻硬產建議，等於編造。
2. **三欄皆空者不呼叫 AI**——那是確定性判斷（`no_basis`），花錢問也問不出東西。
3. **幻覺 patent_id 與非法建議值當場失敗**——不把不存在的判斷寫進正式資料。
4. **按字數切批不按件數**——獨立項單篇逾萬字。

⚠ 本檔刻意**不連 DB**：fetch／store 皆注入。要驗的是判讀流程的決策，
不是資料庫行為（那在 `test_prefilter_decisions.py`）。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _cli_result(payload):
    """包成 `--output-format json` 的信封。

    ⚠ 答案在 `result` 欄不是 stdout 本身——直接讀 stdout 會拿到整個信封，
    這在別的 runner 上踩過。
    """
    from backend.app.worker.cli_gateway import CliResult

    inner = json.dumps(payload, ensure_ascii=False)
    return CliResult(exit_code=0,
                     stdout=json.dumps({"result": inner}, ensure_ascii=False),
                     stderr="")


ITEMS = [
    # (patent_id, title, abstract, claims)
    (2001, "Lawn mower blade assembly", "a mower deck", "1. A mower comprising"),
    (2002, "Leaf blower", "a blower duct", "1. A blower comprising"),
]

BLANK_ITEM = (2003, None, "", "   ")


class PrefilterScopeReviewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.payload_root = Path(self._tmp.name)
        self.stored = []
        self.seen_argv = []

    def _store(self, workspace_id, items, **_kw):
        self.stored.extend(items)
        return len(list(items))

    def _fake_cli(self, verdicts):
        def runner(argv, _timeout):
            self.seen_argv.append(list(argv))
            return _cli_result({"verdicts": verdicts})
        return runner

    def _echo_cli(self, verdict="keep", reason="r"):
        """讀 payload 檔、只回該批項目的假 CLI。

        ⚠ 比「每批都回全部」真實：真的 CLI 只看得到自己那批，
        回了別批的 id 就是幻覺，會被 extract_verdicts 擋下。
        分批測試必須用這個，否則測到的是幻覺檢查而不是分批。
        """
        def runner(argv, _timeout):
            self.seen_argv.append(list(argv))
            # ⚠ 路徑夾在 prompt 文字裡（「資料檔（JSON，UTF-8）：C:\...json」），
            #   用空白切不開——中文冒號後面直接接路徑。用正規式取。
            found = re.search(r"[^\s：]+\.json", " ".join(argv))
            data = json.loads(Path(found.group()).read_text(encoding="utf-8"))
            return _cli_result({"verdicts": [
                {"patent_id": it["patent_id"], "verdict": verdict, "reason": reason}
                for it in data["items"]]})
        return runner

    def _run(self, **kwargs):
        from backend.app.worker import ai_prefilter_review_runner as m

        defaults = dict(
            workspace_id=901,
            scope_description="自走式割草機的驅動與刀盤機構",
            items=ITEMS,
            store=self._store,
            payload_root=self.payload_root,
        )
        defaults.update(kwargs)
        return m.run_prefilter_review(**defaults)

    # ── 1. 沒有範圍描述不准跑 ────────────────────────────
    def test_missing_scope_description_is_refused(self):
        """🔴 沒有判讀依據卻硬產建議＝編造。

        ⚠ 這裡**不能**退化成「用 workspace 名稱湊合」：使用者裁決過，
        「自走式割草機」五個字判斷不了「刀片結構算不算範圍內」。
        含糊的依據會產出看起來很肯定的錯建議，比沒有建議更糟。
        """
        from backend.app.worker import ai_prefilter_review_runner as m

        for bad in ("", "   ", None):
            with self.subTest(scope=bad):
                with self.assertRaises(m.ScopeReviewError):
                    self._run(scope_description=bad,
                              cli_runner=self._fake_cli([]))
        self.assertEqual(self.seen_argv, [], "沒有範圍描述卻還是呼叫了 CLI")

    # ── 2. 三欄皆空不呼叫 AI ─────────────────────────────
    def test_blank_fields_resolved_without_calling_ai(self):
        """🔴 三欄皆空是確定性判斷，不該花錢問 AI。

        ⚠ 而且問了也只會得到編造的答案——沒有任何內容可讀。
        """
        out = self._run(items=[BLANK_ITEM], cli_runner=self._fake_cli([]))
        self.assertEqual(self.seen_argv, [], "三欄皆空的專利被送去問 AI 了")
        self.assertEqual(len(self.stored), 1)
        self.assertEqual(self.stored[0]["verdict"], "no_basis")
        self.assertEqual(out["no_basis"], 1)
        self.assertEqual(out["judged"], 0)

    def test_blank_and_normal_are_both_handled(self):
        """混合輸入：空的走確定性、有內容的走 AI，兩者都要有結果。

        ⚠ 只處理其中一種的話，另一種會**永遠停在「尚無建議」**——
        使用者會一直等一個不會來的東西。
        """
        out = self._run(
            items=[*ITEMS, BLANK_ITEM],
            cli_runner=self._fake_cli([
                {"patent_id": 2001, "verdict": "keep", "reason": "屬範圍內"},
                {"patent_id": 2002, "verdict": "exclude", "reason": "吹葉機"},
            ]))
        by_pid = {s["patent_id"]: s for s in self.stored}
        self.assertEqual(by_pid[2001]["verdict"], "keep")
        self.assertEqual(by_pid[2002]["verdict"], "exclude")
        self.assertEqual(by_pid[2003]["verdict"], "no_basis")
        self.assertEqual(out["judged"], 2)
        self.assertEqual(out["no_basis"], 1)

    # ── 3. 幻覺與非法值當場失敗 ──────────────────────────
    def test_unknown_patent_id_is_rejected(self):
        """CLI 回了不在本批的 patent_id＝幻覺，不得寫進正式資料。"""
        from backend.app.worker import ai_prefilter_review_runner as m

        with self.assertRaises(m.ScopeReviewError):
            self._run(cli_runner=self._fake_cli([
                {"patent_id": 9999, "verdict": "keep", "reason": "x"}]))
        self.assertEqual(self.stored, [], "幻覺結果被寫進去了")

    def test_invalid_verdict_is_rejected(self):
        from backend.app.worker import ai_prefilter_review_runner as m

        with self.assertRaises(m.ScopeReviewError):
            self._run(cli_runner=self._fake_cli([
                {"patent_id": 2001, "verdict": "maybe", "reason": "x"}]))

    def test_ai_may_not_claim_no_basis(self):
        """🔴 `no_basis` 是程式判定，AI 不得自己宣稱。

        ⚠ 讓 AI 回得了 `no_basis`，等於給它一個「不想判就跳過」的出口，
        而那筆專利明明有內容可讀。使用者會以為是資料缺漏，實際是 AI 偷懶。
        """
        from backend.app.worker import ai_prefilter_review_runner as m

        with self.assertRaises(m.ScopeReviewError):
            self._run(cli_runner=self._fake_cli([
                {"patent_id": 2001, "verdict": "no_basis", "reason": "x"}]))

    def test_missing_reason_is_rejected(self):
        """建議一定要有理由——沒有理由的建議使用者無從評估。"""
        from backend.app.worker import ai_prefilter_review_runner as m

        with self.assertRaises(m.ScopeReviewError):
            self._run(cli_runner=self._fake_cli([
                {"patent_id": 2001, "verdict": "keep", "reason": "  "}]))

    # ── 4. 按字數切批 ────────────────────────────────────
    def test_batches_by_chars_not_by_count(self):
        """🔴 獨立項單篇逾萬字，按件數切會塞爆。"""
        long_items = [(3000 + i, f"T{i}", "a", "x" * 5000) for i in range(4)]
        out = self._run(items=long_items, char_budget=6000,
                        cli_runner=self._echo_cli())
        self.assertGreaterEqual(
            len(self.seen_argv), 4,
            f"四筆各 5000 字、預算 6000 應切成四批，實際 {len(self.seen_argv)} 批")
        self.assertEqual(out["judged"], 4, "分批後有專利沒被判到")

    def test_payload_carries_three_fields_and_scope(self):
        """判讀依據要真的送過去：三個欄位＋範圍描述。

        ⚠ prompt 寫「請依標題摘要獨立項判斷」但 payload 沒帶，
        AI 就是在**憑空判斷**——而它仍會給出很肯定的答案。
        """
        self._run(cli_runner=self._fake_cli([
            {"patent_id": p, "verdict": "keep", "reason": "r"}
            for p, *_ in ITEMS]))
        files = sorted(self.payload_root.rglob("*.json"))
        self.assertTrue(files, "沒有寫出 payload 檔")
        data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertIn("自走式割草機", json.dumps(data, ensure_ascii=False),
                      "payload 沒帶範圍描述")
        first = data["items"][0]
        for key in ("patent_id", "title", "abstract", "claims"):
            self.assertIn(key, first, f"payload 缺 {key}")

    def test_payload_data_not_in_argv(self):
        """資料走檔案不走命令列——Windows argv 上限 32,767。"""
        self._run(cli_runner=self._fake_cli([
            {"patent_id": p, "verdict": "keep", "reason": "r"}
            for p, *_ in ITEMS]))
        joined = " ".join(self.seen_argv[0])
        self.assertNotIn("Lawn mower blade assembly", joined,
                         "專利內文被塞進 argv 了")

    # ── 5. 內部名稱不得洩漏到給人看的文字 ────────────────
    def test_prompt_forbids_internal_field_names(self):
        """🔴 2026-08-21 實跑抓到：AI 把 payload 的欄位名寫進理由。

        實際產出：「…屬於 **batch_scope** 的割草機刀盤機構範圍。」

        ⚠ `batch_scope`／`title`／`claims` 是我給機器看的鍵名，不是使用者的詞彙。
        洩漏出來的後果不只是難看——使用者會以為那是系統的某個設定項而去找它，
        或乾脆看不懂整句話而略過建議，等於這欄白做。

        🔴 根因是**我在 instruction 裡自己這樣寫的**（「說明它與 batch_scope 的
        關係」）——模型只是照做。所以修在 prompt，不是事後用字串比對過濾：
        過濾會漏掉沒列舉到的名稱，而且改欄名時不會有人記得同步。
        """
        from backend.app.worker import ai_prefilter_review_runner as m

        text = m.build_payload("割草機", ITEMS)["instruction"]
        self.assertIn("不要提到", text, "沒有明令禁止提及內部名稱")

        # ⚠ 禁止用語本身要舉那些名字當**反例**（具體反例對 LLM 遠比抽象指示
        #   有效），所以不能整段禁掉——只檢查它們沒出現在**其他**行。
        lines = [ln for ln in text.splitlines() if "不要提到" not in ln]
        for name in ("batch_scope", "output_contract", "patent_id"):
            with self.subTest(name=name):
                self.assertNotIn(
                    name, "\n".join(lines),
                    f"instruction 在禁止句以外的地方叫模型引用內部鍵名 {name}")

    def test_payload_still_carries_the_three_fields(self):
        """⚠ 禁止**提及**欄位名，不等於不給欄位內容——依據還是要送過去。"""
        from backend.app.worker import ai_prefilter_review_runner as m

        payload = m.build_payload("割草機", ITEMS)
        first = payload["items"][0]
        for key in ("title", "abstract", "claims"):
            self.assertIn(key, first, f"payload 缺 {key}")

    # ── 6. 空輸入 ────────────────────────────────────────
    def test_no_targets_is_not_an_error(self):
        """沒有待判讀的專利＝正常結果，不是失敗。"""
        out = self._run(items=[], cli_runner=self._fake_cli([]))
        self.assertEqual(self.seen_argv, [])
        self.assertEqual(out["judged"], 0)
        self.assertEqual(out["no_basis"], 0)


if __name__ == "__main__":
    unittest.main()
