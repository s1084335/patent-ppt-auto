"""初階篩選：AI 轉英文比對詞（切片 B，PRE-002／AIC-009）。

## 本檔守的三件事

1. **AI 產出一律為未確認草稿**——寫進去的當下 `terms_confirmed` 仍是 `false`。
   🔴 這是**護欄測試不是 code review**：就算未來有人在 runner 裡多寫一行
   `terms_confirmed=True`，這裡要紅。
2. **未確認的比對詞不得產生任何 pending**——切片 A 已驗 `active_match_terms`
   為空；本檔驗「AI 剛寫完」這個時間點同樣為空。
3. **三處註冊必須同步**（AIC-009）——`job_repository.AI_JOB_TYPES`、
   `ai_bridge._AI_JOB_RUNNERS`、`test_cli_gateway` 的權限政策表。

## ⚠ 為什麼 runner 不從別的 runner import `build_cli_command`

兩支既有 runner 留下同一個血淚註解（`ai_candidate_explanation_runner:52`、
`ai_topic_backfill_runner:41`）：早期從別的 runner import，而那是
`partial(tools=RESEARCH_TOOLS)`，於是**靜默取得 12 支工具＋MCP 取證權限**。

⇒ 本 runner 自行 `functools.partial(_gw_build_cli_command, tools=NO_TOOLS)`。
本檔用「指令裡不得出現任何工具名」把這件事鎖住。
"""
from __future__ import annotations

import unittest


class KeywordExpandRegistrationTests(unittest.TestCase):
    """AIC-009：三處註冊同步。少一處就是靜默失效。"""

    JOB_TYPE = "ai:keyword_expand"

    def test_registered_in_job_types(self):
        from backend.app.db.job_repository import AI_JOB_TYPES, JOB_TYPES

        self.assertIn(self.JOB_TYPE, AI_JOB_TYPES,
                      "沒註冊進 AI_JOB_TYPES——Companion 不會來領")
        self.assertIn(self.JOB_TYPE, JOB_TYPES,
                      "沒註冊進 JOB_TYPES——建 job 會被拒")

    def test_registered_in_bridge_runners(self):
        from backend.app.worker.ai_bridge import _AI_JOB_RUNNERS

        self.assertIn(self.JOB_TYPE, _AI_JOB_RUNNERS,
                      "沒註冊進 _AI_JOB_RUNNERS——領到了也不知道要跑誰")

    def test_three_registries_agree(self):
        """🔴 三處集合相等——這條才是真正的守門，個別存在只是必要條件。"""
        import re
        from pathlib import Path

        from backend.app.db.job_repository import AI_JOB_TYPES
        from backend.app.worker.ai_bridge import _AI_JOB_RUNNERS

        policy_src = Path("tests/test_cli_gateway.py").read_text(encoding="utf-8")
        policy_keys = set(re.findall(r'"(ai:[a-z_]+)":\s*"[A-Z_]+"', policy_src))

        self.assertEqual(
            set(AI_JOB_TYPES), set(_AI_JOB_RUNNERS),
            "AI_JOB_TYPES 與 _AI_JOB_RUNNERS 不一致——差集就是會靜默失效的 job")
        self.assertEqual(
            set(AI_JOB_TYPES), policy_keys,
            "權限政策表與 AI_JOB_TYPES 不一致——沒宣告權限等級的 job 會拿到預設值")


class KeywordExpandToolPolicyTests(unittest.TestCase):
    """B.4：runner 必須自行綁 NO_TOOLS，不得從別的 runner 借。"""

    def test_command_carries_no_tools(self):
        from backend.app.worker import ai_keyword_expand_runner as R

        argv = R.build_keyword_expand_cli_command("claude", "prompt")
        joined = " ".join(str(a) for a in argv)
        for tool in ("Read", "Grep", "Glob", "Write", "Edit", "Bash",
                     "WebSearch", "WebFetch", "mcp__"):
            self.assertNotIn(
                tool, joined,
                f"指令夾帶了 {tool}——本 job 只做中英轉換，不該有任何工具")

    def test_does_not_import_build_cli_command_from_other_runner(self):
        """🔴 血淚註解的落點：從別的 runner import 會靜默拿到 RESEARCH_TOOLS。"""
        import inspect

        from backend.app.worker import ai_keyword_expand_runner as R

        src = inspect.getsource(R)
        self.assertNotIn(
            "from backend.app.worker.ai_", src,
            "從其他 runner import——那些是 partial(tools=RESEARCH_TOOLS)，"
            "會靜默取得 12 支工具＋MCP 取證權限")
        self.assertIn("NO_TOOLS", src, "沒有明確綁 NO_TOOLS")


class KeywordExpandDraftGuardTests(unittest.TestCase):
    """B.1／B.2：AI 產出一律未確認。"""

    def test_store_writes_unconfirmed(self):
        """🔴 護欄：即使呼叫端傳了 terms_confirmed=True，落庫仍是未確認。"""
        import inspect

        from backend.app.worker import ai_keyword_expand_runner as R

        src = inspect.getsource(R.store_expansion)
        self.assertIn(
            "terms_confirmed=False", src,
            "store_expansion 沒有把確認狀態寫死為 False——AI 產出可能直接生效")

    def test_store_signature_has_no_confirm_parameter(self):
        """連參數都不該有——有參數就有人會傳 True。"""
        import inspect

        from backend.app.worker import ai_keyword_expand_runner as R

        params = inspect.signature(R.store_expansion).parameters
        for bad in ("terms_confirmed", "confirmed", "confirm"):
            self.assertNotIn(
                bad, params,
                f"store_expansion 開放了 {bad} 參數——確認狀態只能由使用者操作改")


class KeywordExpandParseTests(unittest.TestCase):
    """B.7：轉換失敗要明確回報，且不阻斷使用者自行輸入。"""

    def test_extract_terms_from_valid_output(self):
        from backend.app.worker import ai_keyword_expand_runner as R

        raw = '{"terms": ["mow", "mower", "lawn"]}'
        self.assertEqual(R.extract_terms(raw), ["lawn", "mow", "mower"])

    def test_extract_terms_deduplicates_and_sorts(self):
        """排序固定：PRE-001 要求重跑可重現。"""
        from backend.app.worker import ai_keyword_expand_runner as R

        raw = '{"terms": ["Mower", "mow", "MOW", "mower"]}'
        self.assertEqual(R.extract_terms(raw), ["mow", "mower"])

    def test_extract_terms_rejects_non_ascii(self):
        """比對詞必須是英文——中文留在 original_term，不進 match_terms。

        ⚠ 理由是資料事實不是偏好：非原文欄位（title／abstract／獨立項）全為英文，
        中文詞放進去必然零命中，而零命中看起來就像「這個詞沒問題」。
        """
        from backend.app.worker import ai_keyword_expand_runner as R

        raw = '{"terms": ["mow", "割草", "lawn"]}'
        self.assertEqual(R.extract_terms(raw), ["lawn", "mow"])

    def test_extract_terms_raises_on_garbage(self):
        from backend.app.worker import ai_keyword_expand_runner as R

        with self.assertRaises(R.KeywordExpandError):
            R.extract_terms("not json at all")

    def test_extract_terms_raises_on_empty_result(self):
        """空結果要當失敗回報，不得靜默寫入空陣列。

        ⚠ 靜默寫空的後果：使用者看到「轉換完成」但一個詞都沒有，
        以為是 AI 判斷沒有對應詞，實際是解析失敗。
        """
        from backend.app.worker import ai_keyword_expand_runner as R

        with self.assertRaises(R.KeywordExpandError):
            R.extract_terms('{"terms": []}')


if __name__ == "__main__":
    unittest.main()
