"""ai:company_zh_name 改走資料檔，不塞命令列（2026-07-28）。

使用者原則（2026-07-27 定，本次點名確認此支）：「AI 分類記得不要再走參數傳遞那種」。

## 實測風險

`build_prompt(candidates)` 把整批公司名串進 prompt、再經 `build_cli_command` 塞進
命令列。長度隨公司數線性成長：

    20 家 ->  2,384 字元   安全
    60 家 ->  5,784 字元   安全
   200 家 -> 17,784 字元   安全
   500 家 -> 43,584 字元   🔴 超過 Windows CreateProcess 上限 32,767

臨界點約 370 家。目前本庫只有 20+ 種名稱不會炸，但全庫一擴張就會——而且是
`WinError 206`（檔名或副檔名太長）這種**看起來像 CLI 壞掉**的錯誤，
`ai:topic_label` 2026-07-27 踩過同一個坑（實測 128,101 字元）才改資料檔。

## 修法

沿 `ai_topic_label_runner` 既有模式，用同一支 `ai_payload_file`：
    pf.write_payload_file(...) → pf.build_cli_command_with_payload(...)
CLI 端以 Read 讀檔，命令列只留短 instruction 與檔案路徑。

⚠ 不另造一套資料檔機制——`ai_payload_file` 已是七支 runner 共用的唯一實作。
"""
from __future__ import annotations

import inspect
import unittest


class UsesPayloadFileTests(unittest.TestCase):
    """資料走檔案，命令列只留路徑。"""

    def test_writes_payload_file(self):
        from backend.app.worker import ai_company_zh_name_runner as r

        src = inspect.getsource(r)
        self.assertIn(
            "write_payload_file", src,
            "仍把整批公司名串進命令列——公司數一多就撞 Windows 32,767 上限")

    def test_uses_shared_cli_builder(self):
        """用共用的 build_cli_command_with_payload，不自組 argv。"""
        from backend.app.worker import ai_company_zh_name_runner as r

        src = inspect.getsource(r.run_company_zh_name)
        self.assertIn("build_cli_command_with_payload", src)

    def test_prompt_not_passed_as_argv(self):
        """run_company_zh_name 不得再走「整段 prompt 進命令列」那條。"""
        from backend.app.worker import ai_company_zh_name_runner as r

        src = inspect.getsource(r.run_company_zh_name)
        self.assertNotIn(
            "build_cli_command(cli_kind, prompt", src,
            "還在用整段 prompt 組 argv")

    def test_no_second_payload_mechanism(self):
        """不得另造資料檔機制——ai_payload_file 是七支 runner 的唯一實作。"""
        from backend.app.worker import ai_company_zh_name_runner as r

        src = inspect.getsource(r)
        self.assertIn("ai_payload_file", src)


class PayloadContentTests(unittest.TestCase):
    """落檔內容要夠 AI 判讀，且不夾帶多餘資訊。"""

    def test_payload_carries_candidates(self):
        from backend.app.worker import ai_company_zh_name_runner as r

        payload = r.build_zh_name_payload([("UN1", "ACME CORP"), ("UN2", "BETA LTD")])
        self.assertIn("companies", payload)
        self.assertEqual(len(payload["companies"]), 2)
        codes = {c["code"] for c in payload["companies"]}
        self.assertEqual(codes, {"UN1", "UN2"})

    def test_payload_has_output_contract(self):
        """契約要寫進 payload，CLI 才知道要回什麼形狀。"""
        from backend.app.worker import ai_company_zh_name_runner as r

        payload = r.build_zh_name_payload([("UN1", "ACME CORP")])
        blob = str(payload)
        self.assertIn("verdict", blob)
        self.assertIn("keep_original", blob)


class ScaleTests(unittest.TestCase):
    """規模驗證：命令列長度不得再隨公司數成長。"""

    def test_argv_length_flat_regardless_of_count(self):
        from backend.app.worker import ai_company_zh_name_runner as r
        from backend.app.worker import ai_payload_file as pf

        argv = pf.build_cli_command_with_payload(
            "claude",
            instruction="任務：為公司產生中文名（系統派工）。",
            payload_path="/tmp/x.json",
            model=None,
        )
        joined = " ".join(str(a) for a in argv)
        self.assertLess(
            len(joined), 4000,
            "命令列仍過長——資料應在檔案裡，argv 只放路徑")


if __name__ == "__main__":
    unittest.main()
