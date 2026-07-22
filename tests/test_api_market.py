"""Market evidence FastAPI 契約測試。

市場資料線不能讓 AI 直接寫正式資料；候選 evidence 先進 `workflow_outputs`，
使用者確認後才寫入 `derived_layer.market_evidence`。
"""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.market.evidence_model import MarketEvidenceError


PREFIX = "/api/v1"
client = TestClient(app)


def _candidate() -> dict:
    """建立一筆符合 market evidence schema 的候選資料。"""
    return {
        "kind": "market_size",
        "scope": "robot mower",
        "target": "US",
        "payload_json": {
            "source_name": "US Department of Energy",
            "source_url": "https://example.org/market",
            "published_on": "2025-01-01",
            "reliability": "industry_gov_corp",
            "summary": "市場規模持續成長。",
            "evidence_excerpt": "The source reports market growth in 2025.",
        },
        "source_url": "https://example.org/market",
        "summary": "市場規模持續成長。",
    }


class MarketEvidenceApiTests(unittest.TestCase):
    """驗證 market evidence API 對外契約與人工確認 guard。"""

    def test_prepare_task_returns_claude_research_brief_and_run_id(self) -> None:
        """建立 research brief，同時建立 workflow run 供候選 evidence 對齊。"""
        with mock.patch(
            "backend.app.api.market.evidence_runs.create_market_evidence_run",
            return_value={
                "run_id": 31,
                "run_type": "market_evidence_research",
                "status": "waiting_external_research",
                "workspace_id": None,
            },
        ) as create_run:
            response = client.post(
                f"{PREFIX}/market-evidence/tasks",
                json={
                    "scope": "robot mower",
                    "targets": ["US", "EU"],
                    "kinds": ["market_size"],
                    "report_version": "r1",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run_id"], 31)
        self.assertEqual(body["run_type"], "market_evidence_research")
        self.assertEqual(body["status"], "needs_external_research")
        self.assertEqual(body["output_type"], "market:evidence_candidates")
        self.assertEqual(body["scope"], "robot mower")
        self.assertIn("anti_hallucination_rules", body)
        self.assertEqual(create_run.call_args.kwargs["task_payload"]["scope"], "robot mower")

    def test_prepare_task_validation_errors_return_422(self) -> None:
        """空 scope 或非法 kind 這類 market workflow 錯誤要回 422。"""
        response = client.post(
            f"{PREFIX}/market-evidence/tasks",
            json={"scope": "", "targets": ["US"], "kinds": ["market_size"]},
        )

        self.assertEqual(response.status_code, 422)

    def test_save_candidates_writes_workflow_output_only(self) -> None:
        """Claude CLI 回填候選 evidence 時，只暫存 workflow_outputs。"""
        with mock.patch(
            "backend.app.api.market.tools_market.save_market_evidence_candidates",
            return_value={"run_id": 7, "output_type": "market:evidence_candidates", "version": 1},
        ) as save:
            response = client.post(
                f"{PREFIX}/market-evidence/candidates",
                json={
                    "run_id": 7,
                    "scope": "robot mower",
                    "candidates": [_candidate()],
                    "report_version": "r1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 1)
        self.assertEqual(save.call_args.kwargs["run_id"], 7)

    def test_accept_candidates_persists_selected_evidence(self) -> None:
        """使用者確認後，只有選定 candidate 會寫入正式 market_evidence。"""
        with mock.patch(
            "backend.app.api.market.tools_market.accept_market_evidence_candidates",
            return_value={"accepted_count": 1, "ids": [11]},
        ) as accept:
            response = client.post(
                f"{PREFIX}/market-evidence/accept",
                json={"candidates": [_candidate()], "accepted_indexes": [0]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ids"], [11])
        self.assertEqual(accept.call_args.kwargs["accepted_indexes"], [0])

    def test_accept_candidate_validation_errors_return_422(self) -> None:
        """正式入庫前的 evidence 驗證失敗要回 422，不讓前端誤判成功。"""
        with mock.patch(
            "backend.app.api.market.tools_market.accept_market_evidence_candidates",
            side_effect=MarketEvidenceError("bad evidence"),
        ):
            response = client.post(
                f"{PREFIX}/market-evidence/accept",
                json={"candidates": [_candidate()], "accepted_indexes": [0]},
            )

        self.assertEqual(response.status_code, 422)

    def test_list_market_evidence_passes_filters(self) -> None:
        """查詢時保留 kind、scope、target 篩選條件。"""
        with mock.patch(
            "backend.app.api.market.tools_market.get_market_evidence",
            return_value={"count": 1, "evidence": [{"id": 3}]},
        ) as get_evidence:
            response = client.get(
                f"{PREFIX}/market-evidence",
                params={"kind": "market_size", "scope": "robot mower", "target": "US"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(get_evidence.call_args.kwargs["scope"], "robot mower")

    def test_aggregate_market_evidence_returns_report_payload(self) -> None:
        """彙總 API 回傳報表/PPT 可使用的 market evidence payload。"""
        with mock.patch(
            "backend.app.api.market.tools_market.aggregate_market_evidence",
            return_value={"scope": "robot mower", "groups": []},
        ) as aggregate:
            response = client.get(
                f"{PREFIX}/market-evidence/aggregate",
                params={"scope": "robot mower"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scope"], "robot mower")
        self.assertEqual(aggregate.call_args.kwargs["scope"], "robot mower")


if __name__ == "__main__":
    unittest.main()
