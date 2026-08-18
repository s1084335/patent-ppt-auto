"""缺證據的建議一律跳過，且跳過必須被揭露（2026-08-18 使用者定案）。

## 兩個問題

1. **person 相關證據仍是整批死**：`harden` commit 讓主 evidence 缺失時跳過該筆，
   但 `person_identity_evidence`／`relationship_evidence` 走的還是硬失敗
   —— AI 只要輸出一筆缺證據的 person_affiliation，**整個 job failed、使用者拿到零筆**。
   #396／#397 連兩次失敗的訊息就是 `evidence requires at least 1 source(s)`。

2. **跳過是靜默的**：後端算了 `skipped_invalid`，前端完全沒有這個字。
   使用者看到 5 筆會以為「AI 只找到 5 個」，實際是「找到 8 個、3 個沒證據」
   ——後者代表那 3 家值得人工查。⚠ 缺席型偏差：多出來的看得見，被丟掉的沒人發現。

## 判準

- 跳過的**理由要留下**：三個 evidence 欄位共用同一句錯誤訊息，不指名欄位與
  candidate_ref 的話，跳過了也查不出為什麼。
"""
from __future__ import annotations

import json

import pytest

from tests.test_ai_company_normalization_suggestion import (  # noqa: F401
    FakeStore,
    _candidate,
    _target,
    _valid_result,
)


#: `_valid_result("person_affiliation")` 只換 kind，person 欄位要自己補
#: （沿用既有測試 test_person_affiliation_is_review_only_before_confirmation 的資料）。
PERSON_FIELDS = {
    "relationship_role": "director",
    "person_identity_evidence": [
        {"url": "https://example.com/person", "title": "Registry",
         "claim": "same person"}
    ],
    "relationship_evidence": [
        {"url": "https://example.com/director", "title": "Registry",
         "claim": "director"}
    ],
}


def _person_payload() -> dict:
    payload = json.loads(_valid_result("person_affiliation"))
    payload["suggestions"][0].update(PERSON_FIELDS)
    return payload


def _run(payload: dict, store: FakeStore) -> dict:
    from backend.app.worker import ai_company_normalization_suggestion_runner as runner

    return runner.run_company_normalization_suggestions(
        store=store,
        cli_runner=lambda *_a, **_kw: json.dumps(payload, ensure_ascii=False),
    )


@pytest.mark.parametrize(
    "field", ["person_identity_evidence", "relationship_evidence"])
def test_person_evidence_missing_skips_row_not_whole_job(field: str):
    """🔴 核心：person 證據缺失只跳過該筆，其餘建議照常產出。"""
    payload = _person_payload()
    bad = dict(payload["suggestions"][0])
    bad.pop(field)
    good = json.loads(_valid_result())["suggestions"][0]
    good["candidate_refs"] = ["cand:2"]     # ⚠ 同一 ref 不得重複出現在兩筆建議
    payload["suggestions"] = [bad, good]

    result = _run(payload, FakeStore(
        [_candidate(), _candidate(ref="cand:2")], [_target()]))

    assert result["suggestion_count"] == 1, "有效那筆應該還在"
    assert result["skipped_invalid"] == 1
    assert result["inserted"] == 1


def test_skip_reason_names_field_and_candidate():
    """⚠ 三個 evidence 欄位共用同一句訊息，不指名就查不出為什麼被跳過。"""
    payload = _person_payload()
    bad = dict(payload["suggestions"][0])
    bad.pop("relationship_evidence")
    payload["suggestions"] = [bad]

    result = _run(payload, FakeStore([_candidate()], [_target()]))

    details = result.get("skipped_details")
    assert details, "跳過沒有留下任何理由"
    assert "relationship_evidence" in details[0]["reason"], details[0]
    assert details[0]["candidate_refs"], "沒有指出是哪一筆候選"


def test_non_evidence_contract_errors_still_hard_fail():
    """⚠ 不得把跳過擴大成「什麼錯都跳過」——契約錯誤仍要整批拒絕。

    AI 供 code 欄位、未知 ref 這類是**協定被破壞**，不是「這筆查不到證據」，
    靜默跳過會讓錯誤的整合方式一直存在而沒人知道。
    """
    from backend.app.worker import ai_company_normalization_suggestion_runner as runner

    payload = json.loads(_valid_result())
    payload["suggestions"][0]["target_ref"] = "unknown-ref"

    with pytest.raises(ValueError):
        runner.run_company_normalization_suggestions(
            store=FakeStore([_candidate()], [_target()]),
            cli_runner=lambda *_a, **_kw: json.dumps(payload, ensure_ascii=False),
        )


def test_frontend_surfaces_skipped_count():
    """前端要看得到「有幾筆因缺證據被跳過」，否則跳過等於沒發生。"""
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1]
            / "backend/app/static/index.html").read_text(encoding="utf-8")
    assert "skipped_invalid" in html, "前端沒有讀 skipped_invalid"
    assert "跳過" in html, "前端沒有把跳過的情形寫給使用者看"
