"""worker queue 相容層。

processing_jobs 的資料庫讀寫規則已由 Claude 統一放在
backend.app.db.job_repository。worker 端保留這個檔案，是為了讓既有 import
路徑不需要一起改；實際邏輯只從單一來源轉出，避免 backend 與 worker 分歧。
"""

from __future__ import annotations

from backend.app.db.job_repository import ProcessingJob, TERMINAL_STATUSES, WorkerQueueClient


__all__ = ["ProcessingJob", "TERMINAL_STATUSES", "WorkerQueueClient"]
