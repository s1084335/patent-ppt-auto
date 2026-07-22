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


# curation 裁決標記：company_aliases.source_file 帶此前綴＝該代碼已完成顯示名裁決
# （含「保留原文」的裁決），名稱治理管線不再把它列入待中文化建議。
CURATION_SOURCE_PREFIX = "display_name_curation"


def govern_company_names(
    pairs: list[tuple[str | None, str | None]],
    source_label: str = "variant_intake",
    connect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """名稱治理管線核心：變體註冊＋待中文化偵測，匯入與 sweep 共用同一套邏輯。

    輸入 (code, name) 集合（2026-07-21 定案「單一名稱治理管線」）：
    - code 在 company_aliases 對到唯一「公司名稱」→ 新變體補一列別稱，公司名稱沿用既有值；
      既有別稱（normalize 後相同）跳過。
    - code 不在表中（unknown_code）或對到多個公司名稱（conflicting_code）→ 進 manual_review，
      不自行合併、不寫表。
    - needs_zh_name：本批涉及且已在對照表的 code 中，canonical 顯示名不含 CJK 字元
      （尚無市場慣用中文名）且未經 curation 裁決者——該代碼不存在
      source_file LIKE 'display_name_curation%' 的列；「保留原文」也是正式裁決，不重複浮現。
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
    needs_zh: list[dict[str, str]] = []
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

        batch_codes: set[str] = set()
        for raw_code, raw_variant in pairs:
            code = clean_text(raw_code)
            variant = clean_text(raw_variant)
            if not code or not variant:
                continue
            batch_codes.add(code)
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

        # 待中文化偵測：只看本批涉及的 code；canonical 無 CJK（[一-鿿]）且該代碼
        # 沒有任何 curation 裁決列（source_file LIKE 'display_name_curation%'）才浮現。
        if batch_codes:
            zh_rows = conn.execute(
                'SELECT DISTINCT ca."申請人代碼", ca."公司名稱" '
                "FROM derived_layer.company_aliases ca "
                'WHERE ca."申請人代碼" = ANY(%s) '
                "  AND ca.\"公司名稱\" !~ '[一-鿿]' "
                "  AND NOT EXISTS ("
                "      SELECT 1 FROM derived_layer.company_aliases d "
                '      WHERE d."申請人代碼" = ca."申請人代碼" AND d.source_file LIKE %s'
                "  ) "
                "ORDER BY 1, 2",
                (sorted(batch_codes), f"{CURATION_SOURCE_PREFIX}%"),
            ).fetchall()
            needs_zh = [{"company_code": c, "company_name": n} for c, n in zh_rows]
        conn.commit()

    return {
        "inserted": inserted,
        "skipped_existing": skipped,
        "manual_review": manual,
        "needs_zh_name": needs_zh,
    }


def register_known_code_variants(
    pairs: list[tuple[str | None, str | None]],
    source_label: str = "variant_intake",
    connect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """已知 WIPS code 的新名稱變體補入唯一對照表（薄包裝，委派名稱治理管線）。

    保留原簽章供 wips_importer 匯入時呼叫（importers 不需改）；實際邏輯統一在
    govern_company_names，匯入摘要因此自動帶出 needs_zh_name（新公司進庫即浮現待中文化）。
    """
    return govern_company_names(pairs, source_label=source_label, connect_kwargs=connect_kwargs)


def apply_confirmed_display_names(
    mapping: dict[str, dict[str, Any]],
    source_label: str,
    connect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """套用使用者已裁決的公司顯示名（curation 落地機制；載體＝DB 本身，不經 CSV）。

    mapping = {申請人代碼: {"canonical": 顯示名, "aliases": [變體, ...]}}
    規則（2026-07-21 公司顯示名原則定案）：
    - canonical 自身也納入別稱，確保顯示名字面可精確命中。
    - 批內先按 normalize_lookup 去重（同 lookup key 保留第一個字面，含跨 code），
      避免大小寫／空白變體在同批內撞 ux_company_aliases_lookup_confirmed。
    - lookup key 已存在（任何 review_status；以 DB 端 alias_lookup_key 同一運算式比對）
      → UPDATE 該列 re-canonicalize：改掛新顯示名／代碼／source_file，review_status
      一律轉 'confirmed'；不插新列，故不會撞唯一索引。
    - lookup key 不存在 → INSERT 一列 confirmed（source_type='manual'＝人工裁決）。
    - 只寫 derived_layer.company_aliases；原始專利表不碰。
    回傳 {"inserted", "updated", "dedup_dropped"}。
    """
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database import. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    # 批內去重：唯一索引是全表範圍，去重也必須跨 code 全批共用同一 key 空間
    entries: list[tuple[str, str, str]] = []  # (code, canonical, 別稱字面)
    seen_keys: set[str] = set()
    dedup_dropped = 0
    for raw_code, spec in mapping.items():
        code = clean_text(raw_code)
        canonical = clean_text(spec.get("canonical"))
        if not code or not canonical:
            continue
        for raw_alias in [canonical, *spec.get("aliases", [])]:
            alias = clean_text(raw_alias)
            if not alias:
                continue
            key = normalize_lookup(alias)
            if key in seen_keys:
                dedup_dropped += 1
                continue
            seen_keys.add(key)
            entries.append((code, canonical, alias))

    inserted = 0
    updated = 0
    with psycopg.connect(**(connect_kwargs or get_connection_kwargs())) as conn:
        for code, canonical, alias in entries:
            # 以 DB 端與 generated 欄完全相同的正規化運算式找既有列，
            # 多列同 key（review_required 重複）時優先更新 confirmed 列，其餘不動。
            row = conn.execute(
                "SELECT id FROM derived_layer.company_aliases "
                r"WHERE alias_lookup_key = lower(regexp_replace(btrim(%s), '\s+', ' ', 'g')) "
                "ORDER BY (review_status = 'confirmed') DESC, id LIMIT 1",
                (alias,),
            ).fetchone()
            if row:
                conn.execute(
                    'UPDATE derived_layer.company_aliases SET "公司名稱" = %s, "申請人代碼" = %s, '
                    "source_file = %s, review_status = 'confirmed', source_type = 'manual', "
                    "updated_at = now() WHERE id = %s",
                    (canonical, code, source_label, row[0]),
                )
                updated += 1
            else:
                conn.execute(
                    'INSERT INTO derived_layer.company_aliases ("申請人代碼", "公司名稱", "別稱", '
                    "source_file, source_type, review_status) "
                    "VALUES (%s, %s, %s, %s, 'manual', 'confirmed')",
                    (code, canonical, alias, source_label),
                )
                inserted += 1
        conn.commit()

    return {"inserted": inserted, "updated": updated, "dedup_dropped": dedup_dropped}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import company alias table into derived_layer.company_aliases.")
    parser.add_argument("path", type=Path, help="Path to company alias CSV/XLSX.")
    parser.add_argument("--dry-run", action="store_true", help="Read and normalize without writing to database.")
    args = parser.parse_args()
    summary = import_company_aliases(args.path, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
