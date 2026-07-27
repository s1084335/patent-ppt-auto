"""AI job 註冊一致性守門（待辦 B-5，2026-07-27 因實際故障而落實）。

事故：`ai:irrelevant_filter` 在 `job_repository.AI_JOB_TYPES` 白名單內（Companion
領得走），但 `ai_bridge._AI_JOB_RUNNERS` **從未註冊** → 領到就丟
`ValueError: unsupported AI bridge job_type: ai:irrelevant_filter`。

為何拖到今天才暴露：它的上游 `_enqueue_irrelevant_filter` 讀 `summary["workspace_id"]`，
而 `FinalizationSummary` 直到今天才補上該欄位——在此之前這個 job **從未被建立過**
（DB 歷來 0 筆）。也就是說，兩份清單不一致的狀態存在很久，只是沒有觸發條件。

本測試把「兩份清單必須一致」變成硬性契約：新增 AI 任務時漏改任一邊即 red，
不必等到實際派工才發現。
"""
from __future__ import annotations

import unittest

from backend.app.db import job_repository
from backend.app.worker import ai_bridge


class AiJobRegistrationGuardTests(unittest.TestCase):
    """AI_JOB_TYPES 與 _AI_JOB_RUNNERS 必須一一對應。"""

    def test_every_ai_job_type_has_runner(self):
        """白名單內的每個 job_type 都要有 runner，否則 Companion 領到會直接失敗。"""
        declared = set(job_repository.AI_JOB_TYPES)
        registered = set(ai_bridge._AI_JOB_RUNNERS)
        missing = declared - registered
        self.assertFalse(
            missing,
            f"這些 AI job 已在 AI_JOB_TYPES 白名單但 bridge 沒有 runner，"
            f"Companion 領到會拋 unsupported AI bridge job_type：{sorted(missing)}",
        )

    def test_no_orphan_runner(self):
        """反向：註冊了 runner 卻不在白名單＝永遠不會被派工的死碼。"""
        declared = set(job_repository.AI_JOB_TYPES)
        registered = set(ai_bridge._AI_JOB_RUNNERS)
        orphan = registered - declared
        self.assertFalse(
            orphan,
            f"這些 runner 不在 AI_JOB_TYPES 內，永遠不會被派工（死碼）：{sorted(orphan)}",
        )

    def test_runner_functions_actually_exist(self):
        """對照表指向的函式名必須真的存在於模組內（避免改名後留下壞字串）。"""
        missing = [
            f"{job_type} -> {func_name}"
            for job_type, func_name in ai_bridge._AI_JOB_RUNNERS.items()
            if not callable(getattr(ai_bridge, func_name, None))
        ]
        self.assertFalse(missing, f"對照表指向不存在的函式：{missing}")


if __name__ == "__main__":
    unittest.main()
