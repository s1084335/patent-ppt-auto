"""「匯出報告」頁的 deck 交付物 API（add-deck-delivery-line design §6）。

頁面分工：報表種類頁＝報表工作介面；**匯出報告頁＝交付物中心**——本檔供它
三件事：deck 紀錄清單、逐頁 PNG 預覽（「先看到」）、pptx 下載（「再下載」，
使用者主動按，不自動推送）。

## 資料來源

- 紀錄＝workflow_runs 的 `ai:report_deck` job（`jr.list_jobs(job_type=…)`）
  ＋ workflow_outputs 的 manifest（`jr.fetch_job_result`）合併。
- 檔案＝`DECK_ARTIFACT_ROOT`（runner 回存的落點；DB 只存相對 key）。

## 安全邊界

供檔路徑一律以 **manifest 內的 key** 為準——`{name}` 是使用者輸入，
不在 manifest `page_keys` 裡的一律 404；解析後仍須落在 artifact root 內
（沿 `clustering` artifacts 的 escape 檢查前例）。pptx 供檔前先驗 SHA-256
與 manifest 相符：**靜默供出被動過的檔比 404 危險**——使用者拿到的簡報
會和目視驗過的不是同一份，故不符回 409。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.app.db import job_repository as jr
from backend.app.worker.ai_report_deck_runner import default_artifact_root

router = APIRouter(prefix="/deck-exports", tags=["deck-exports"])


def _resolve_key(key: str) -> Path:
    """manifest 相對 key → artifact root 下的絕對路徑；逃出 root 即拒絕。"""
    root = default_artifact_root()
    path = (root / key).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="artifact key escapes root") from exc
    return path


def _manifest_for(run_id: int) -> tuple[Any, dict[str, Any]]:
    """取 job 與其 manifest；job 不存在／非 deck job／無結果都是 404。"""
    job = jr.get_job(run_id)
    if job is None or job.job_type != "ai:report_deck":
        raise HTTPException(status_code=404, detail=f"deck export {run_id} not found")
    manifest = jr.fetch_job_result(run_id, job.job_type)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"deck export {run_id} has no result")
    return job, manifest


@router.get("")
def list_deck_exports(limit: int = 20) -> dict[str, Any]:
    """deck 紀錄清單（新到舊）。執行中／失敗的 job 也列——匯出頁要顯示進度與錯誤。

    預覽與下載 URL 由後端組好給前端：key 形制改了（例如搬 NAS 換層級）
    前端不用跟著改。
    """
    items: list[dict[str, Any]] = []
    for job in jr.list_jobs(job_type="ai:report_deck", limit=limit):
        manifest = jr.fetch_job_result(job.job_id, job.job_type) or {}
        page_names = [k.rsplit("/", 1)[-1] for k in manifest.get("page_keys") or []]
        items.append({
            "run_id": job.job_id,
            "status": job.status,
            "progress_percent": job.progress_percent,
            "current_stage": job.current_stage,
            "error_message": job.error_message,
            "based_on_version": (manifest.get("based_on_version")
                                 or job.payload_json.get("based_on_version")),
            "page_count": manifest.get("page_count"),
            "visual_rounds": manifest.get("visual_rounds"),
            "sha256": manifest.get("sha256"),
            "size_bytes": manifest.get("size_bytes"),
            "page_urls": [f"/api/v1/deck-exports/{job.job_id}/pages/{n}"
                          for n in page_names],
            "pptx_url": (f"/api/v1/deck-exports/{job.job_id}/pptx"
                         if manifest.get("pptx_key") else None),
        })
    return {"items": items}


@router.get("/{run_id}/pages/{name}")
def get_deck_page(run_id: int, name: str) -> FileResponse:
    """逐頁預覽 PNG——就是產線目視那批圖，backend 自 artifact root 供圖。"""
    _, manifest = _manifest_for(run_id)
    match = next((k for k in manifest.get("page_keys") or []
                  if k.rsplit("/", 1)[-1] == name), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"page {name!r} not in manifest")
    path = _resolve_key(match)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"page file missing: {match}")
    return FileResponse(path, media_type="image/png")


@router.get("/{run_id}/pptx")
def download_deck_pptx(run_id: int) -> FileResponse:
    """下載 pptx（使用者主動按）。串流前驗 SHA-256 與 manifest 相符。"""
    _, manifest = _manifest_for(run_id)
    key = manifest.get("pptx_key")
    if not key:
        raise HTTPException(status_code=404, detail="manifest has no pptx_key")
    path = _resolve_key(str(key))
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"pptx file missing: {key}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != manifest.get("sha256"):
        raise HTTPException(
            status_code=409,
            detail=f"pptx SHA-256 與 manifest 不符（檔案 {digest[:12]}… ≠ "
                   f"manifest {str(manifest.get('sha256'))[:12]}…）；"
                   "產物可能被改動，拒絕供檔。請重新產製。")
    version = manifest.get("based_on_version") or "deck"
    return FileResponse(
        path,
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".presentationml.presentation"),
        filename=f"deck_{version}.pptx",
    )
