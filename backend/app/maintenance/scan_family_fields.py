"""離線掃描 WIPS 匯出檔的國家佈局相關欄位——只讀檔、不寫 DB。

用途：在資料匯入 DB 前，先驗證同族明細/EPC/状态欄位的解析覆蓋率，
例如 data/raw 的 850 檔（EP 樣本，驗 EPC 解析）與 407 檔（全欄位，驗同族明細）。
複用 wips_importer 的檔案載入與 family_layout 的純函式，保證與正式管線同一套邏輯。

CLI: python -m backend.app.maintenance.scan_family_fields --path <xlsx>
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.importers.wips_importer import load_source_rows
from backend.app.mappings.legal_status import normalize_legal_status
from backend.app.mappings.wips import canonical_field_name
from backend.app.transforms.family_layout import (
    build_family_country_dataset,
    classify_ep_contribution,
    parse_family_country_counts,
    split_pipe_codes,
)

# canonical（簡體）表頭 → family_layout 的 canonical key。
# 檔案表頭經 canonical_field_name 正規化後比對，中英/繁簡介面匯出皆可吃。
HEADER_TO_KEY = {
    "WIPS同族ID": "family_id",
    "WIPS同族各国家文献数量(申请为准)": "family_counts",
    "EPC有效国家[EP]": "epc_valid",
    "EPC无效国家[EP]": "epc_invalid",
    "国家代码": "country_code",
    "状态[US,JP,KR,CN,EP,CA,AU]": "legal_status",
}

# patent_id 用途只是 surrogate 家族 id 的字串來源，依專利號優先序取第一個非空。
PATENT_ID_HEADERS = ("授权公告号", "审查的公告号", "未审查的公开号", "申请号")


def map_headers(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """把檔案實際表頭映射到 canonical key，回傳 (實際表頭→key, 缺少的 key)。"""
    mapping: dict[str, str] = {}
    for header in headers:
        canonical = canonical_field_name(header)
        key = HEADER_TO_KEY.get(canonical)
        if key and key not in mapping.values():
            mapping[header] = key
    missing = sorted(set(HEADER_TO_KEY.values()) - set(mapping.values()))
    return mapping, missing


def pick_patent_id(record: dict[str, Any], headers_by_canonical: dict[str, str]) -> Any:
    """依專利號四欄優先序取一個可識別值（找不到就用列號）。"""
    for canonical in PATENT_ID_HEADERS:
        header = headers_by_canonical.get(canonical)
        if header is not None:
            value = record.get(header)
            if value is not None and str(value).strip():
                return str(value).strip()
    return f"row{record.get('_row_number')}"


def infer_country_from_number(patent_id: Any) -> str | None:
    """從專利號字首推斷受理局（如 "EP4491667 B1" → "EP"）。

    只給離線掃描用：精簡匯出檔可能沒有 国家代码 欄；
    正式管線（DB）一定有 country_code，不走這個推斷。
    """
    text = str(patent_id).strip().upper()
    if len(text) >= 2 and text[:2].isalpha() and (len(text) == 2 or not text[2].isalpha()):
        return text[:2]
    return None


def scan_file(path: Path, assume_country: str | None = None) -> dict[str, Any]:
    """掃描單一 WIPS 匯出檔，回傳解析覆蓋率與品質統計。

    assume_country：檔案沒有 国家代码 欄時的受理局預設值
    （如 850 EP 樣本檔——精簡匯出且專利號無字首，無從推斷）。
    """
    _sheet_names, _selected_sheet, records, headers = load_source_rows(path)
    mapping, missing_keys = map_headers(headers)
    headers_by_canonical = {canonical_field_name(h): h for h in headers}

    status_counter: Counter[str] = Counter()
    unknown_status_values: Counter[str] = Counter()
    ep_kind_counter: Counter[str] = Counter()
    family_counts_bad_tokens: Counter[str] = Counter()
    family_counts_nonblank = 0
    epc_valid_nonblank = 0

    rows_for_dataset: list[dict[str, Any]] = []
    for record in records:
        row: dict[str, Any] = {key: None for key in HEADER_TO_KEY.values()}
        for header, key in mapping.items():
            value = record.get(header)
            row[key] = str(value) if value is not None else None
        row["patent_id"] = pick_patent_id(record, headers_by_canonical)
        if not (row.get("country_code") or "").strip():
            row["country_code"] = infer_country_from_number(row["patent_id"]) or assume_country
        rows_for_dataset.append(row)

        raw_status = (row.get("legal_status") or "").strip()
        normalized = normalize_legal_status(raw_status)
        status_counter[normalized] += 1
        if normalized == "unknown" and raw_status:
            unknown_status_values[raw_status] += 1

        counts_raw = row.get("family_counts")
        if counts_raw and counts_raw.strip():
            family_counts_nonblank += 1
            _counts, bad = parse_family_country_counts(counts_raw)
            for token in bad:
                family_counts_bad_tokens[token] += 1

        country = (row.get("country_code") or "").strip().upper()
        if country == "EP":
            valid_codes = split_pipe_codes(row.get("epc_valid"))
            if valid_codes:
                epc_valid_nonblank += 1
            kind, _codes = classify_ep_contribution(
                valid_codes, invalid_is_blank=not split_pipe_codes(row.get("epc_invalid"))
            )
            ep_kind_counter[kind] += 1

    dataset = build_family_country_dataset(rows_for_dataset)
    return {
        "path": str(path),
        "row_count": len(records),
        "mapped_headers": mapping,
        "missing_keys": missing_keys,
        "status_normalized_counts": dict(status_counter),
        "unknown_status_values": dict(unknown_status_values.most_common(20)),
        "family_counts_nonblank_rows": family_counts_nonblank,
        "family_counts_bad_tokens": dict(family_counts_bad_tokens.most_common(20)),
        "ep_rows_by_epc_kind": dict(ep_kind_counter),
        "ep_rows_with_valid_states": epc_valid_nonblank,
        "layout_summary": dataset.summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan a WIPS export file for family/EPC/status field coverage (read-only).")
    parser.add_argument("--path", required=True, type=Path, help="WIPS export file (.xlsx/.csv/.txt)")
    parser.add_argument(
        "--assume-country",
        default=None,
        help="fallback jurisdiction code when the file lacks a 国家代码 column (e.g. EP)",
    )
    args = parser.parse_args()
    report = scan_file(args.path, assume_country=args.assume_country)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
