"""單筆 worker job 的執行上下文。"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from types import TracebackType

from .queue_client import ProcessingJob, WorkerQueueClient


class JobCancelledError(RuntimeError):
    """通知目前工作已被外部要求取消，runner 會把狀態收斂成 cancelled。"""


class HeartbeatKeeper:
    """在長時間 handler 執行期間定期補 heartbeat，避免大資料工作被誤判 stale。"""

    def __init__(
        self,
        context: "JobContext",
        *,
        stage: str,
        progress: int,
        interval_seconds: float,
    ) -> None:
        """建立背景 heartbeat 迴圈；真正啟動發生在 context manager 進入時。"""
        self.context = context
        self.stage = stage
        self.progress = progress
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "HeartbeatKeeper":
        """啟動 daemon thread，先打一筆 heartbeat 再進入長時間工作。"""
        self.context.heartbeat(self.stage, self.progress)
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-job-{self.context.job.job_id}", daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """停止背景 heartbeat，避免 handler 結束後仍繼續更新同一筆 job。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self.interval_seconds, 5.0))

    def _run(self) -> None:
        """固定間隔補 heartbeat；一次更新失敗不打斷正在跑的模型工作。"""
        while not self._stop_event.wait(self.interval_seconds):
            try:
                self.context.heartbeat(self.stage, self.progress)
            except Exception:
                continue


@dataclass
class JobContext:
    """提供 handler 回報進度、heartbeat 與取消檢查的薄封裝。"""

    job: ProcessingJob
    worker_id: str
    store: WorkerQueueClient

    def heartbeat(self, stage: str | None = None, progress: int | None = None) -> None:
        """更新目前工作 heartbeat，可同時更新階段與進度百分比。"""
        if progress is not None and not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        self.store.heartbeat(
            job_id=self.job.job_id,
            worker_id=self.worker_id,
            current_stage=stage,
            progress_percent=progress,
        )

    def keepalive(self, stage: str, progress: int, *, interval_seconds: float = 60.0) -> HeartbeatKeeper:
        """回傳長任務用 heartbeat keeper，讓 handler 用 with 包住耗時計算。"""
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than 0")
        return HeartbeatKeeper(self, stage=stage, progress=progress, interval_seconds=interval_seconds)

    def check_cancelled(self) -> None:
        """確認工作是否已被 backend 或使用者標記為 cancelled。"""
        if self.store.is_cancelled(job_id=self.job.job_id):
            raise JobCancelledError(f"job {self.job.job_id} was cancelled")
