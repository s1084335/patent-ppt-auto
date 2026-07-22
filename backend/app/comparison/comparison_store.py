"""案件比對 · 比對案件與版本化產出存取（接 0021 schema，不建新 table）。

資料落點（沿用既有表，不新增 schema）：
- 比對案件 = app_layer.workflow_runs（run_type='comparison'，request_json 帶 patent_number 與
  標的產品描述）。
- 所有產出走 PostgresWorkflowOutputsRepository 版本化 append（不覆蓋舊版本）：
  - 'understanding'          AI 理解稿原始輸出。
  - 'understanding_approval' 人工核准（approved_by／approved_at／核准的 understanding 版本號、
     可含人工修訂後全文）。AI 原稿與人工覆核分開存，互不覆蓋。
  - 'element_analysis'       逐要素比對。
  - 'verdict'                程式套 all-elements rule 後的彙總結果。

人工閘門 guard：寫 element_analysis／verdict 前，必須已有 understanding_approval，且其引用的
understanding 版本存在，否則拋 GateNotApprovedError；approval 亦不得指向不存在的版本。
四態與 claim 狀態在寫入層再驗一次（縱深防禦，非法值拒寫）。SQL 只留在本模組。
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from backend.app.comparison.verdict import parse_claim_status, parse_element_status
from backend.app.repositories.workflow_outputs_repository import (
    PostgresWorkflowOutputsRepository,
)

# output_type 命名（掛在同一 run 上，各自版本化）
OUTPUT_UNDERSTANDING = "understanding"
OUTPUT_APPROVAL = "understanding_approval"
OUTPUT_ELEMENT_ANALYSIS = "element_analysis"
OUTPUT_VERDICT = "verdict"
OUTPUT_ILLUSTRATIONS = "illustrations"
OUTPUT_TARGET = "target"
# subject＝被比對專利集合（要拆要素、與參考專利比對的案件資料）。與 target（產品標的）
# 語意區分：模式 A 產品 vs 專利時，被比對側是專利。掛在同一 run 上、版本化 append。
OUTPUT_SUBJECT = "subject"

# 被比對來源模式（2026-07-22 定案）：library＝既有庫專利號選取；import＝檔案匯入（走輪1）。
SUBJECT_MODE_LIBRARY = "library"
SUBJECT_MODE_IMPORT = "import"
SUBJECT_MODES: frozenset[str] = frozenset({SUBJECT_MODE_LIBRARY, SUBJECT_MODE_IMPORT})


class GateNotApprovedError(RuntimeError):
    """人工理解閘門未通過（無核准，或核准指向不存在的理解稿版本）即拒寫下游產出。"""


class ComparisonStore:
    """比對案件與版本化產出存取層。"""

    # 被比對來源模式常數掛在類上，供 API 層驗證（避免 API 反向依賴模組級常數多一行 import）。
    SUBJECT_MODE_LIBRARY = SUBJECT_MODE_LIBRARY
    SUBJECT_MODE_IMPORT = SUBJECT_MODE_IMPORT
    SUBJECT_MODES = SUBJECT_MODES

    def __init__(self, connect_kwargs: dict[str, Any] | None = None):
        # 未指定時沿用專案統一連線設定（env PG* / DATABASE_URL）；與 outputs repo 共用同一組
        self._connect_kwargs = connect_kwargs
        self._outputs = PostgresWorkflowOutputsRepository(connect_kwargs)

    def _connect(self):
        from backend.app.db.connection import get_connection_kwargs

        return psycopg.connect(**(self._connect_kwargs or get_connection_kwargs()))

    def create_case(
        self,
        patent_number: str,
        target_description: str,
        requested_by: str,
        request_key: str | None = None,
    ) -> int:
        """建立比對案件（workflow_runs run_type='comparison'），回傳 run_id。"""
        if not patent_number or not str(patent_number).strip():
            raise ValueError("patent_number 不得為空")
        if not target_description or not str(target_description).strip():
            raise ValueError("target_description 不得為空")
        request_json = {
            "patent_number": patent_number,
            "target_description": target_description,
            "requested_by": requested_by,
        }
        with self._connect() as conn:
            run_id = conn.execute(
                "INSERT INTO app_layer.workflow_runs "
                "(run_type, status, request_key, request_json) "
                "VALUES ('comparison', 'queued', %s, %s) RETURNING run_id",
                (request_key, Jsonb(request_json)),
            ).fetchone()[0]
            conn.commit()
        return run_id

    def save_understanding(self, run_id: int, draft: dict[str, Any]) -> int:
        """存 AI 理解稿原始輸出（版本化 append，不覆蓋）。"""
        return self._outputs.append_output(run_id, OUTPUT_UNDERSTANDING, draft)

    def approve_understanding(
        self,
        run_id: int,
        understanding_version: int,
        approved_by: str,
        revised_understanding: dict[str, Any] | None = None,
        approved_at: str | None = None,
    ) -> int:
        """人工核准某 understanding 版本；與 AI 原稿分開存，不得指向不存在的版本。"""
        if not approved_by or not str(approved_by).strip():
            raise ValueError("approved_by 不得為空")
        if self._outputs.get_output(
            run_id, OUTPUT_UNDERSTANDING, version=understanding_version
        ) is None:
            raise GateNotApprovedError(
                f"核准指向不存在的 understanding 版本 v{understanding_version}")
        if approved_at is None:
            from datetime import datetime, timezone

            approved_at = datetime.now(timezone.utc).isoformat()
        data = {
            "understanding_version": understanding_version,
            "approved_by": approved_by,
            "approved_at": approved_at,
            "revised_understanding": revised_understanding,
        }
        return self._outputs.append_output(run_id, OUTPUT_APPROVAL, data)

    def save_element_analysis(self, run_id: int, analysis: dict[str, Any]) -> int:
        """存逐要素比對；閘門未核准拒寫，四態非法拒寫。"""
        self._assert_gate(run_id)
        self._validate_element_statuses(analysis)
        return self._outputs.append_output(run_id, OUTPUT_ELEMENT_ANALYSIS, analysis)

    def save_verdict(self, run_id: int, verdict: dict[str, Any]) -> int:
        """存 all-elements rule 彙總結果；閘門未核准拒寫，claim 狀態非法拒寫。"""
        self._assert_gate(run_id)
        self._validate_claim_statuses(verdict)
        return self._outputs.append_output(run_id, OUTPUT_VERDICT, verdict)

    def save_illustrations(self, run_id: int, figure_paths: list[str]) -> int:
        """存最終選用圖片相對路徑陣列（版本化不覆蓋）。

        illustrations 是報告素材、非判斷產出，**不受 understanding_approval 閘門限制**
        （自取設計，待使用者追認）；DB 只存相對路徑，不存 metadata 或 binary。
        """
        if not isinstance(figure_paths, list):
            raise ValueError("figure_paths 必須為 list")
        return self._outputs.append_output(run_id, OUTPUT_ILLUSTRATIONS, {"figure_paths": figure_paths})

    def get_latest_element_analysis(self, run_id: int) -> dict[str, Any] | None:
        """回傳最新版 element_analysis 的 {version, data}；無則 None。"""
        output = self._outputs.get_output(run_id, OUTPUT_ELEMENT_ANALYSIS)
        if output is None:
            return None
        return {"version": output["version"], "data": output.get("data_json")}

    def save_target(self, run_id: int, target: dict[str, Any]) -> int:
        """存標的資料（output_type='target'，版本化 append）。

        target 是比對輸入素材、非判斷產出，不受 understanding_approval 閘門限制。
        payload 必須明確帶 simulated 布林標記（本階段以專利模擬標的，不得默默省略），
        缺標記拒寫且不落任何列。
        """
        if not isinstance(target, dict):
            raise ValueError("target 必須為 dict")
        if not isinstance(target.get("simulated"), bool):
            raise ValueError("target payload 必須明確標注 simulated 布林值")
        return self._outputs.append_output(run_id, OUTPUT_TARGET, target)

    def bind_subject_library(
        self, run_id: int, patent_ids: list[int]
    ) -> tuple[int, list[int]]:
        """綁定被比對專利集合（library 模式）：去重、驗存在於 core_layer.patents。

        回傳 (version, bound_patent_ids)。空集合（去重後為空）→ ValueError；有 patent_id
        不存在於庫 → ValueError（列出缺的）。存 output_type='subject'（版本化 append，不覆蓋），
        payload={mode:'library', patent_ids:[...]}。subject 是比對輸入素材、非判斷產出，
        不受 understanding_approval 閘門限制。存在性以單一 ANY 批次查（不逐筆）。
        """
        bound = self._dedup_ids(patent_ids)
        if not bound:
            raise ValueError("patent_ids 去重後不得為空")
        missing = self._missing_patent_ids(bound)
        if missing:
            raise ValueError(f"patent_ids not found in core_layer.patents: {missing}")
        data = {"mode": SUBJECT_MODE_LIBRARY, "patent_ids": bound}
        version = self._outputs.append_output(run_id, OUTPUT_SUBJECT, data)
        return version, bound

    def bind_subject_import(self, run_id: int, import_job_id: int) -> int:
        """綁定被比對來源（import 模式）：記錄觸發的輪1 匯入 job 參照。

        匯入為非同步（worker 執行實際匯入、圈 workspace、觸發 embeddings），匯入完成後的
        patent_ids 由 worker 回填到匯入 job summary；此處先綁 import_job_id 建立追溯。
        存 output_type='subject'，payload={mode:'import', import_job_id:N, patent_ids:[]}。
        subject 不受 understanding_approval 閘門限制。
        """
        data = {
            "mode": SUBJECT_MODE_IMPORT,
            "import_job_id": int(import_job_id),
            "patent_ids": [],
        }
        return self._outputs.append_output(run_id, OUTPUT_SUBJECT, data)

    def get_latest_subject(self, run_id: int) -> dict[str, Any] | None:
        """回傳最新版 subject 的 {version, data}；無則 None。供輪3 取被比對 patent_ids。"""
        output = self._outputs.get_output(run_id, OUTPUT_SUBJECT)
        if output is None:
            return None
        return {"version": output["version"], "data": output.get("data_json")}

    @staticmethod
    def _dedup_ids(patent_ids: list[int]) -> list[int]:
        """去重（保序）並轉 int；型別錯誤直接冒泡（呼叫端應已驗）。"""
        return list(dict.fromkeys(int(value) for value in patent_ids))

    def _missing_patent_ids(self, patent_ids: list[int]) -> list[int]:
        """單一 ANY 批次查 core_layer.patents，回不存在的 patent_id（保序）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM core_layer.patents WHERE id = ANY(%s)",
                (patent_ids,),
            ).fetchall()
        existing = {int(row[0]) for row in rows}
        return [pid for pid in patent_ids if pid not in existing]

    # ── 內部 guard ───────────────────────────────────────────────

    def _assert_gate(self, run_id: int) -> None:
        """下游產出寫入前檢查人工閘門：核准存在且指向存在的 understanding 版本。"""
        approval = self._outputs.get_output(run_id, OUTPUT_APPROVAL)
        if approval is None:
            raise GateNotApprovedError(
                f"run {run_id} 尚無 understanding_approval，禁止寫入下游產出")
        version = (approval.get("data_json") or {}).get("understanding_version")
        if self._outputs.get_output(run_id, OUTPUT_UNDERSTANDING, version=version) is None:
            raise GateNotApprovedError(
                f"run {run_id} 的核准指向不存在的 understanding 版本 v{version}")

    @staticmethod
    def _validate_element_statuses(analysis: dict[str, Any]) -> None:
        """逐要素 status 必須為合法四態，否則拋 VerdictError（寫入前，未落任何列）。"""
        for claim in (analysis or {}).get("claims", []):
            for element in claim.get("elements", []):
                parse_element_status(element.get("status"))

    @staticmethod
    def _validate_claim_statuses(verdict: dict[str, Any]) -> None:
        """每個 claim status 必須為合法 ClaimStatus，否則拋 VerdictError。"""
        for claim in (verdict or {}).get("claims", []):
            parse_claim_status(claim.get("status"))
