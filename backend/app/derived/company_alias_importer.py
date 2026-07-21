from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from backend.app.transforms.text import clean_text

REQUIRED_COLUMNS = ("申請人代碼", "公司名稱", "別稱")


def normalize_lookup(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    return " ".join(text.casefold().split())


def load_alias_rows(path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return load_xlsx_alias_rows(path)
    if suffix == ".csv":
        return load_csv_alias_rows(path)
    raise ValueError(f"Unsupported company alias file format: {path.suffix}")


def load_csv_alias_rows(path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "cp950", "big5", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return normalize_alias_rows(csv.DictReader(handle))
        except UnicodeError:
            continue
    raise UnicodeError(f"Cannot decode company alias CSV: {path}")


def load_xlsx_alias_rows(path: Path) -> list[dict[str, str]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    worksheet = workbook.worksheets[0]
    rows = worksheet.iter_rows(values_only=True)
    try:
        headers = [str(value).strip() if value is not None else "" for value in next(rows)]
    except StopIteration:
        return []
    records = []
    for row in rows:
        record = {header: row[index] if index < len(row) else None for index, header in enumerate(headers) if header}
        records.append(record)
    return normalize_alias_rows(records)


def normalize_alias_rows(records: Any) -> list[dict[str, str]]:
    rows = []
    seen = set()
    for record in records:
        company_code = clean_text(record.get("申請人代碼"))
        company_name = clean_text(record.get("公司名稱"))
        alias_name = clean_text(record.get("別稱"))
        if not company_name or not alias_name:
            continue
        key = (company_code or "", company_name, alias_name)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "company_code": company_code,
                "company_name": company_name,
                "alias_name": alias_name,
            }
        )
    return rows


def import_company_aliases(path: Path, dry_run: bool = False) -> dict[str, Any]:
    rows = load_alias_rows(path)
    summary = {
        "file": str(path),
        "file_format": path.suffix.lstrip(".").lower(),
        "rows": len(rows),
        "status": "dry_run" if dry_run else "pending",
    }
    if dry_run:
        return summary

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database import. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO derived_layer.company_aliases (
                    "申請人代碼", "公司名稱", "別稱", source_file
                )
                VALUES (
                    %(company_code)s, %(company_name)s, %(alias_name)s, %(source_file)s
                )
                ON CONFLICT ("申請人代碼", "公司名稱", "別稱") DO UPDATE
                SET
                    source_file = EXCLUDED.source_file,
                    imported_at = now()
                """,
                [{**row, "source_file": str(path)} for row in rows],
            )
        conn.commit()
    summary["status"] = "imported"
    return summary


def register_known_code_variants(
    pairs: list[tuple[str | None, str | None]],
    source_label: str = "variant_intake",
    connect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """已知 WIPS code 的新名稱變體補入唯一對照表，沿用既有正規化公司名稱。

    規則（2026-07-21 報表收尾定案）：
    - code 在 company_aliases 對到唯一「公司名稱」→ 新變體補一列別稱，公司名稱沿用既有值；
      既有別稱（normalize 後相同）跳過。
    - code 不在表中（unknown_code）或對到多個公司名稱（conflicting_code）→ 進 manual_review，
      不自行合併、不寫表。
    - 只寫 derived_layer.company_aliases；不碰 raw/core 原始專利值。
    """
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database import. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    inserted = 0
    skipped = 0
    manual: list[dict[str, str]] = []
    with psycopg.connect(**(connect_kwargs or get_connection_kwargs())) as conn:
        rows = conn.execute(
            'SELECT "申請人代碼", "公司名稱", "別稱" FROM derived_layer.company_aliases '
            'WHERE "申請人代碼" IS NOT NULL'
        ).fetchall()
        # code → 既有公司名稱集合／normalize 後別稱集合
        names_by_code: dict[str, set[str]] = {}
        aliases_by_code: dict[str, set[str]] = {}
        for code, name, alias in rows:
            names_by_code.setdefault(code, set()).add(name)
            norm = normalize_lookup(alias)
            if norm:
                aliases_by_code.setdefault(code, set()).add(norm)

        for raw_code, raw_variant in pairs:
            code = clean_text(raw_code)
            variant = clean_text(raw_variant)
            if not code or not variant:
                continue
            names = names_by_code.get(code)
            if names is None:
                manual.append({"company_code": code, "alias_name": variant, "reason": "unknown_code"})
                continue
            if len(names) > 1:
                manual.append({"company_code": code, "alias_name": variant, "reason": "conflicting_code"})
                continue
            norm_variant = normalize_lookup(variant)
            if norm_variant in aliases_by_code.get(code, set()):
                skipped += 1
                continue
            canonical_name = next(iter(names))  # 唯一既有正規化公司名稱，直接沿用
            conn.execute(
                'INSERT INTO derived_layer.company_aliases ("申請人代碼", "公司名稱", "別稱", source_file) '
                "VALUES (%s, %s, %s, %s) "
                'ON CONFLICT ("申請人代碼", "公司名稱", "別稱") DO NOTHING',
                (code, canonical_name, variant, source_label),
            )
            aliases_by_code.setdefault(code, set()).add(norm_variant)
            inserted += 1
        conn.commit()

    return {"inserted": inserted, "skipped_existing": skipped, "manual_review": manual}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import company alias table into derived_layer.company_aliases.")
    parser.add_argument("path", type=Path, help="Path to company alias CSV/XLSX.")
    parser.add_argument("--dry-run", action="store_true", help="Read and normalize without writing to database.")
    args = parser.parse_args()
    summary = import_company_aliases(args.path, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
