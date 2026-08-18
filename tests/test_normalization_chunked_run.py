"""分段執行、逐段隔離與蓋章（queue-normalization-candidates）。

## 為什麼要分段

現況一個 job＝一次 CLI 呼叫，候選全部塞進同一個 prompt。契約錯誤是整批硬失敗
——#396／#397 連兩次 failed、使用者拿到零筆，就是這個形狀。候選愈多，
一次協定失誤損失愈大。

## 最容易做對一半的地方

段隔離做了、但失敗沒顯示；或顯示了卻仍蓋章。⚠ 失敗段**不得蓋章**：
契約錯誤代表協定壞了，不是「這些候選查不到證據」；蓋了章會把一個程式問題
當成資料結論，把候選推到隊尾，而件數不變就再也回不來——一批候選因為一次
程式錯誤永久消失，且沒有任何訊息。
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


BATCH = 20
CHUNK = 5


class RecordingStore(FakeStore):
    """多記一件事：誰被蓋了章。"""

    def __init__(self, candidates, targets):
        super().__init__(candidates, targets)
        self.stamped: list[dict] = []

    def mark_asked(self, entries):
        self.stamped.extend(entries)
        return {"stamped": len(entries)}


def _candidates(n: int) -> list[dict]:
    rows = []
    for i in range(n):
        row = _candidate(ref=f"cand:{i}", raw_name=f"Company {i} Ltd.")
        row["lookup_key"] = f"company {i} ltd."
        row["patent_count"] = n - i
        rows.append(row)
    return rows


def _reply_for(chunk_candidates: list[dict]) -> str:
    """對某一段回一筆合法建議，指向該段第一個候選。"""
    payload = json.loads(_valid_result())
    payload["suggestions"][0]["candidate_refs"] = [chunk_candidates[0]["candidate_ref"]]
    return json.dumps(payload, ensure_ascii=False)


def _run(store, cli_runner):
    from backend.app.worker import ai_company_normalization_suggestion_runner as runner

    return runner.run_company_normalization_suggestions(
        store=store, cli_runner=cli_runner)


def _chunks_seen(prompts: list[str], store) -> list[list[str]]:
    """從側錄到的 prompt 反推每段送了哪些 candidate_ref。"""
    refs = [c["candidate_ref"] for c in store.candidates]
    return [[r for r in refs if f'"{r}"' in p] for p in prompts]


def test_batch_is_capped_and_split_into_chunks():
    """🔴 一個 job 最多 20 個候選，切成 4 段各 5 個。"""
    store = RecordingStore(_candidates(50), [_target()])
    prompts: list[str] = []

    def cli(prompt, **_kw):
        prompts.append(prompt)
        chunk = _chunks_seen([prompt], store)[0]
        return _reply_for([c for c in store.candidates
                           if c["candidate_ref"] in chunk])

    result = _run(store, cli)

    assert len(prompts) == BATCH // CHUNK, f"呼叫次數應為 4，實際 {len(prompts)}"
    sizes = [len(c) for c in _chunks_seen(prompts, store)]
    assert sizes == [CHUNK] * (BATCH // CHUNK), f"每段大小應為 5，實際 {sizes}"
    assert result["batch_size"] == BATCH
    assert result["chunk_size"] == CHUNK


def test_one_bad_chunk_does_not_zero_the_run():
    """🔴 第 2 段契約錯誤，其餘 3 段照常寫入。"""
    store = RecordingStore(_candidates(20), [_target()])
    calls = {"n": 0}

    def cli(prompt, **_kw):
        calls["n"] += 1
        if calls["n"] == 2:
            payload = json.loads(_valid_result())
            payload["suggestions"][0]["target_ref"] = "unknown-ref"  # 協定違反
            return json.dumps(payload, ensure_ascii=False)
        chunk = _chunks_seen([prompt], store)[0]
        return _reply_for([c for c in store.candidates
                           if c["candidate_ref"] in chunk])

    result = _run(store, cli)

    assert result["suggestion_count"] == 3, "其餘三段的建議應該都在"
    assert len(result.get("failed_chunks") or []) == 1, "失敗段沒有被記錄"
    assert result["failed_chunks"][0]["index"] == 2
    assert result["failed_chunks"][0].get("reason"), "失敗沒有留下原因"


def test_failed_chunk_is_not_stamped():
    """🔴 協定錯誤不是「查無證據」——蓋了章那批候選會永久消失。"""
    store = RecordingStore(_candidates(20), [_target()])
    calls = {"n": 0}

    def cli(prompt, **_kw):
        calls["n"] += 1
        if calls["n"] == 2:
            payload = json.loads(_valid_result())
            payload["suggestions"][0]["target_ref"] = "unknown-ref"
            return json.dumps(payload, ensure_ascii=False)
        chunk = _chunks_seen([prompt], store)[0]
        return _reply_for([c for c in store.candidates
                           if c["candidate_ref"] in chunk])

    _run(store, cli)

    stamped = {e["lookup_key"] for e in store.stamped}
    assert len(stamped) == BATCH - CHUNK, \
        f"蓋章數應為 15（失敗那段的 5 個不蓋），實際 {len(stamped)}"
    failed_keys = {c["lookup_key"] for c in store.candidates[CHUNK:CHUNK * 2]}
    assert not (stamped & failed_keys), "失敗段的候選被蓋章了——下次不會再被取到"


def test_stamp_records_patent_count_and_outcome():
    """重新入列靠件數比較；沒存件數這條規則就失效。"""
    store = RecordingStore(_candidates(5), [_target()])

    def cli(prompt, **_kw):
        chunk = _chunks_seen([prompt], store)[0]
        return _reply_for([c for c in store.candidates
                           if c["candidate_ref"] in chunk])

    _run(store, cli)

    assert store.stamped, "完全沒有蓋章"
    entry = store.stamped[0]
    assert "lookup_key" in entry
    assert isinstance(entry.get("patent_count"), int), "沒有記下當時的件數"
    assert entry.get("outcome") in {"suggested", "no_evidence"}, entry


def test_candidate_with_suggestion_marked_suggested():
    """⚠ 兩種結果在畫面上長得一樣，不分開事後查不出來。"""
    store = RecordingStore(_candidates(5), [_target()])

    def cli(prompt, **_kw):
        chunk = _chunks_seen([prompt], store)[0]
        return _reply_for([c for c in store.candidates
                           if c["candidate_ref"] in chunk])

    _run(store, cli)

    by_key = {e["lookup_key"]: e for e in store.stamped}
    hit = store.candidates[0]["lookup_key"]
    assert by_key[hit]["outcome"] == "suggested"
    miss = store.candidates[1]["lookup_key"]
    assert by_key[miss]["outcome"] == "no_evidence"


def test_missing_evidence_still_skips_only_that_row():
    """既有行為不得被分段改壞：段內缺證據只跳過該筆。"""
    store = RecordingStore(_candidates(5), [_target()])

    def cli(prompt, **_kw):
        chunk = _chunks_seen([prompt], store)[0]
        rows = [c for c in store.candidates if c["candidate_ref"] in chunk]
        payload = json.loads(_valid_result())
        good = payload["suggestions"][0]
        good["candidate_refs"] = [rows[0]["candidate_ref"]]
        bad = json.loads(json.dumps(good))
        bad["candidate_refs"] = [rows[1]["candidate_ref"]]
        bad.pop("evidence")
        payload["suggestions"] = [good, bad]
        return json.dumps(payload, ensure_ascii=False)

    result = _run(store, cli)

    assert result["suggestion_count"] == 1
    assert result["skipped_invalid"] == 1
    assert not (result.get("failed_chunks") or []), \
        "缺證據被誤判為整段失敗——那會讓同段其他建議一起消失"


def test_no_candidates_is_not_an_error():
    store = RecordingStore([], [_target()])
    result = _run(store, lambda *_a, **_kw: pytest.fail("不該呼叫 CLI"))
    assert result["candidate_count"] == 0
    assert result["suggestion_count"] == 0
