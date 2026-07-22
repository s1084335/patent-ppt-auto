"""案件比對整條線端到端 smoke（真 DB，非 mock）。

用途：驗證 create → subject → target → understanding → approve → element-analysis
整條 API 鏈能對真實 DB 串通並落資料，不只各關卡單元綠。跑法：
    uv run python scripts/comparison_e2e_smoke.py

每一關卡都印出結果與版本；任一關卡非預期狀態即拋錯中止，暴露整線斷點。
本腳本只寫案件比對自己的 workflow_runs/outputs，不動既有專利資料。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 直接跑腳本時 backend 套件不在 sys.path（pytest 由 conftest 設定），此處補專案根。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 上 psycopg_pool 背景 worker 連 localhost 可能解析成 IPv6 ::1 而卡死；
# 強制 127.0.0.1（IPv4）避開，比照 test_api_comparison.setUpModule 的處置。
os.environ.setdefault("PGHOST", "127.0.0.1")

from fastapi.testclient import TestClient

from backend.app.main import app

# 整線各關卡的固定測試輸入（模擬單一案件走完流程）
CASE_BODY = {
    "workspace_id": 1,
    "case_title": "E2E smoke — brush cutter",
    "case_text": "自走式割草機控制器侵權初判案件（smoke）。",
    "comparison_type": "claim_or_technical",
}
TARGET_BODY = {
    "target_name": "競品割草機 A",
    "target_description": "帶馬達控制器的自走式割草機。",
    "target_features": ["a frame rod", "a controller connected to a motor"],
    "simulated": True,
}
# understanding 存正式格式（independent_claims/dependent_claims），供 verdict 彙總讀引用鏈。
UNDERSTANDING_BODY = {
    "independent_claims": [{
        "claim_number": "1",
        "elements": [
            {"text": "a frame rod", "explanation": "機架桿"},
            {"text": "a controller connected to a motor", "explanation": "連接馬達的控制器"},
        ],
    }],
    "dependent_claims": [],
    "unknown_claims": [],
}
ELEMENT_ANALYSIS_BODY = {
    "claims": [{
        "claim_number": "1",
        "elements": [
            {"element_id": "1a", "status": "met", "notes": "frame rod 對應"},
            {"element_id": "1b", "status": "met", "notes": "controller 對應"},
        ],
    }],
}

PREFIX = "/api/v1/comparisons"


def _expect(cond: bool, label: str, detail: object) -> None:
    """關卡斷言：非預期即印出詳情並中止，讓整線斷點無所遁形。"""
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label}: {detail}")
    if not cond:
        raise SystemExit(f"整線在「{label}」關卡斷裂，中止 smoke。")


def main() -> None:
    client = TestClient(app)

    # ① 建 job
    r = client.post(PREFIX, json=CASE_BODY)
    _expect(r.status_code == 202, "create job (202)", r.status_code)
    job_id = r.json()["job_id"]
    print(f"    job_id = {job_id}")

    # ② 被比對來源 subject（library 模式；patent_ids 需存在，取庫內前一筆）
    # 先查一筆真實 patent_id，避免綁不存在的 id 被 422。
    import psycopg
    from backend.app.db.connection import get_connection_kwargs
    with psycopg.connect(**get_connection_kwargs()) as conn:
        row = conn.execute("SELECT id FROM core_layer.patents ORDER BY id LIMIT 1").fetchone()
    if row:
        r = client.post(f"{PREFIX}/{job_id}/subject",
                        json={"mode": "library", "patent_ids": [row[0]]})
        _expect(r.status_code == 200, "subject library (200)", r.json())
    else:
        print("[SKIP] subject：庫內無 patent，略過（不影響後續關卡）")

    # ③ 產品標的 target
    r = client.post(f"{PREFIX}/{job_id}/target", json=TARGET_BODY)
    _expect(r.status_code == 200, "save target (200)", r.json())

    # ④ AI understanding
    r = client.post(f"{PREFIX}/{job_id}/understanding", json=UNDERSTANDING_BODY)
    _expect(r.status_code == 200, "save understanding (200)", r.json())
    u_version = r.json()["version"]

    # ⑤ 人工核准閘門
    r = client.post(f"{PREFIX}/{job_id}/understanding/approve",
                    json={"understanding_version": u_version, "approved_by": "smoke"})
    _expect(r.status_code == 200, "approve understanding (200)", r.json())

    # ⑥ 逐要素比對（閘門後才允許）；存妥後應自動產出 claim 級 verdict 彙總
    r = client.post(f"{PREFIX}/{job_id}/element-analysis", json=ELEMENT_ANALYSIS_BODY)
    _expect(r.status_code == 200, "save element-analysis (200)", r.json())
    _expect(r.json().get("verdict_version") is not None,
            "element-analysis 自動產出 verdict", r.json().get("verdict_version"))

    # ⑦ GET 讀回，確認整線資料都回填得到（含 verdict）
    r = client.get(f"{PREFIX}/{job_id}")
    _expect(r.status_code == 200, "get comparison (200)", r.status_code)
    body = r.json()
    _expect(body.get("element_analysis") is not None,
            "element_analysis 回填", body.get("element_analysis_version"))
    # verdict 回填且 claim 1（全 met）應為 possibly_established
    verdict = body.get("verdict")
    _expect(verdict is not None, "verdict 回填", body.get("verdict_version"))
    v_by_num = {c["claim_number"]: c["status"] for c in (verdict or {}).get("claims", [])}
    _expect(v_by_num.get("1") == "possibly_established",
            "verdict claim 1 全met→possibly_established", v_by_num.get("1"))

    # ⑧ 上傳真實說明書 PDF → 只抽圖式頁（用 data/raw/pdf測試.pdf 真 PDF 驗證）
    spec_pdf = Path(__file__).resolve().parents[1] / "data" / "raw" / "pdf測試.pdf"
    if spec_pdf.exists():
        r = client.post(
            f"{PREFIX}/{job_id}/specification?patent_number=US9300001B",
            content=spec_pdf.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        _expect(r.status_code == 200, "上傳說明書 PDF 抽圖式頁 (200)", r.status_code)
        figs = r.json()
        # 真 PDF 圖式頁在 3-21（前段），22+ 為文字不抽
        _expect(figs["figure_pages"] and max(figs["figure_pages"]) <= 21,
                "只抽圖式頁（≤21，22後文字不抽）", figs["figure_pages"])
        _expect(len(figs["figure_paths"]) == len(figs["figure_pages"]),
                "圖式頁數=產出圖檔數", len(figs["figure_paths"]))
        print(f"    抽出圖式頁 {len(figs['figure_pages'])} 張：{figs['figure_pages']}")
    else:
        print("[SKIP] 上傳抽圖：找不到 data/raw/pdf測試.pdf")

    # ⑨ reference 相似查件：用 subject patent 找語意相近的比對來源候選
    if row:
        r = client.get(f"{PREFIX}/{job_id}/reference-candidates"
                       f"?subject_patent_id={row[0]}&limit=5")
        # 有 embedding 才有候選；本機庫該 patent 可能無 technical embedding，兩種都算通過
        if r.status_code == 200:
            cands = r.json()["candidates"]
            _expect(all(c["patent_id"] != row[0] for c in cands),
                    "reference 候選排除 subject 自身", len(cands))
            print(f"    reference 候選 {len(cands)} 筆（依相似度排序）")
        elif r.status_code == 422:
            print(f"    reference：patent {row[0]} 無 embedding（422，合理）")
        else:
            _expect(False, "reference-candidates 非預期狀態", r.status_code)

    print("\n--- happy path 全通，續驗閘門與判定邊界 ---")

    # ⑧ 閘門防護：另建一 job，未 approve 就打 element-analysis → 應 409。
    r2 = client.post(PREFIX, json=CASE_BODY)
    gate_job = r2.json()["job_id"]
    client.post(f"{PREFIX}/{gate_job}/understanding", json=UNDERSTANDING_BODY)
    # 故意不 approve
    r2 = client.post(f"{PREFIX}/{gate_job}/element-analysis", json=ELEMENT_ANALYSIS_BODY)
    _expect(r2.status_code == 409, "未核准即比對應被閘門擋 (409)", r2.status_code)

    # ⑨ 非法四態：status 亂填 → 應 422（防幻覺/防亂寫）。
    bad = {"claims": [{"claim_number": "1", "elements": [
        {"element_id": "1a", "status": "definitely_yes"}]}]}
    # 先讓此 job 過閘門才測 element-analysis 的四態驗證
    ru = client.post(f"{PREFIX}/{gate_job}/understanding", json=UNDERSTANDING_BODY)
    client.post(f"{PREFIX}/{gate_job}/understanding/approve",
                json={"understanding_version": ru.json()["version"], "approved_by": "smoke"})
    r2 = client.post(f"{PREFIX}/{gate_job}/element-analysis", json=bad)
    _expect(r2.status_code == 422, "非法四態應被拒 (422)", r2.status_code)

    # ⑩ all-elements rule：含 not_met 的 claim，彙總應為 not_established（純邏輯直驗）。
    from backend.app.comparison.verdict import ClaimStatus, evaluate_claim
    v = evaluate_claim(["met", "not_met"])
    _expect(v == ClaimStatus.NOT_ESTABLISHED, "not_met → claim not_established", v.value)
    v = evaluate_claim(["met", "met"])
    _expect(v == ClaimStatus.POSSIBLY_ESTABLISHED, "全 met → possibly_established", v.value)
    v = evaluate_claim(["met", "arguably_met"])
    _expect(v == ClaimStatus.NEEDS_REVIEW, "含 arguably → needs_review", v.value)

    print("\n=== 整線端到端 + 閘門 + 四態判定全數通過 ===")
    print(json.dumps({
        "happy_path_job": job_id,
        "gate_job": gate_job,
        "element_analysis_version": body.get("element_analysis_version"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
