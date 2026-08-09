"""選圖資料包 producer／materializer（P2 第 2 節，tasks 2.2）。

把使用者在前端選定的圖表，從報表版本目錄打包成 immutable 的
`SelectedChartBundle`：圖片複製進受控唯讀工作目錄、數據取同版本的 rows slice、
checksum 同時綁圖片與數據。

⚠ 為什麼要 materialize 而不是直接給報表目錄路徑（design.md 第 1 點）：
- CLI 只能看到**列入 manifest 的檔案**——直接指向報表目錄會讓它看到未選的圖。
- 圖片與數據必須能證明來自同一版本；報表目錄可能在規劃期間被重產。

⚠ 形狀由 `planning_contracts.validate_chart_bundle` 驗——本模組不另定義欄位規則。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


class ChartBundleError(RuntimeError):
    """選圖資料包無法成立（identity 不存在、圖檔缺失等）——一律 fail loud。"""


def _index_report_data(report_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """chart_identity（`report_key:variant_key`）→ 該圖的檔名與資料來源鍵。"""
    index: dict[str, dict[str, Any]] = {}
    for section in report_data.get("sections") or []:
        report_key = str(section.get("report_key") or "")
        for variant in section.get("variants") or []:
            variant_key = str(variant.get("variant_key") or "default")
            index[f"{report_key}:{variant_key}"] = {
                "report_key": report_key,
                "variant_key": variant_key,
                "file": str(variant.get("file") or ""),
                "title": section.get("title") or report_key,
            }
    return index


def _rows_for(report_data: dict[str, Any], report_key: str) -> list[dict[str, Any]]:
    """該報表的結構化數據：chart_rows 優先，退回 reports/family_reports 的 rows。"""
    rows = (report_data.get("chart_rows") or {}).get(report_key)
    if rows:
        return rows
    for bucket in ("reports", "family_reports"):
        entry = (report_data.get(bucket) or {}).get(report_key) or {}
        if entry.get("rows"):
            return entry["rows"]
    return []


def _profile_lineage(run_dir: Path, ppt_file: str) -> dict[str, dict[str, str] | None]:
    """同一 identity 的 web／PPT 兩份圖各自的路徑與 checksum。

    ⚠ 缺 web profile 時留 `None` 而不是省略這個鍵：2026-08-09 之前的每個報表
    版本都只有一份圖，靜默省略會讓「這版沒有 web 圖」與「忘了記錄」長得一樣。
    ⚠ 也不因此讓打包失敗——使用者仍該產得出簡報，只是 lineage 要看得出缺哪一份。
    """
    from backend.app.reports.chart_profiles import profile_filename

    lineage: dict[str, dict[str, str] | None] = {}
    for profile in ("ppt", "web"):
        path = run_dir / profile_filename(ppt_file, profile)
        lineage[profile] = {
            "path": path.name,
            "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        } if path.exists() else None
    return lineage


def build_selected_bundles(
    run_dir: Path,
    selected_identities: list[str],
    work_dir: Path,
) -> list[dict[str, Any]]:
    """產出選圖資料包並 materialize 到 work_dir；同時寫 bundle_manifest.json。

    回傳的每一項都符合 `planning_contracts` 的 SelectedChartBundle 契約。
    """
    data_path = run_dir / "report_data.json"
    if not data_path.exists():
        # ⚠ 明確錯誤而非裸 FileNotFoundError：snapshot_id 只有目錄名，
        # 父目錄由呼叫端決定——訊息要說清楚找的是哪裡（2026-08-09 驗收踩到）。
        raise ChartBundleError(
            f"報表版本目錄缺 report_data.json：{data_path}"
            "（snapshot_id 是否屬於這個輸出根目錄？）")
    report_data = json.loads(data_path.read_text(encoding="utf-8"))
    index = _index_report_data(report_data)
    population = report_data.get("population") or {}
    version = run_dir.name

    work_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = work_dir / "charts"
    charts_dir.mkdir(exist_ok=True)

    bundles: list[dict[str, Any]] = []
    for identity in selected_identities:
        meta = index.get(identity)
        if meta is None:
            raise ChartBundleError(
                f"選圖 identity {identity!r} 不在本報表版本（{version}）——"
                f"可用：{sorted(index)}")
        source = run_dir / meta["file"]
        if not source.exists():
            raise ChartBundleError(
                f"{identity} 的圖檔不存在：{source}——圖片與數據必須成對，不得只給數據")
        target = charts_dir / source.name
        shutil.copy2(source, target)

        rows = _rows_for(report_data, meta["report_key"])
        digest = hashlib.sha256()
        digest.update(source.read_bytes())
        digest.update(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        bundles.append({
            "chart_identity": identity,
            "report_key": meta["report_key"],
            "variant_key": meta["variant_key"],
            "title": meta["title"],
            "image_path": str(target),
            "data_rows": rows,
            "population_note": str(population.get(meta["report_key"]) or ""),
            "version": version,
            # checksum 綁**圖片＋數據**：任一改變就換值，錯配當場現形。
            "checksum": digest.hexdigest(),
            # 雙 profile lineage（A2）：使用者在**網頁**看圖選圖，簡報用的是
            # 同一 identity 的 **PPT** profile——兩者是不同檔案。只記一個
            # checksum 的話，「網頁看到的那張有沒有跟著換版本」無從查核，
            # 兩個 profile 來自不同次產圖也沒有東西攔得住。
            "profile_lineage": _profile_lineage(run_dir, meta["file"]),
        })

    (work_dir / "bundle_manifest.json").write_text(
        json.dumps({"version": version, "charts": bundles}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return bundles
