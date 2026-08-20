"""app_layer.report_artifacts 的單一存取層（worker 寫、backend 讀）。

用途（2026-07-23 定案）：Railway 上 worker 與 backend 是**不同容器**、檔案系統不共享，
worker 產的 output/full_report_latest/<版本>/ backend 的報表端點一律讀不到。兩容器共用
同一個 PostgreSQL，故以本表當跨容器傳輸媒介（同 import_blobs 的解法，不同的是報表產物
是**長生命週期**的版本化產物，不是用完即刪的傳輸暫存）。

為何不塞 app_layer.workflow_outputs：那是 JSONB 版本化結構化結果，其
artifact_manifest_json 依契約**只描述**圖檔（key／hash），不放內容；把 20 張 SVG 塞進
JSONB 需 base64（+33%）且每次讀該 output 就整包拉回，做不到「asset 端點只取單張圖」。

契約：
- 一檔一列，主鍵 (version, filename)；同版本重跑同名檔 upsert（不留半新半舊）。
- 讀取一律單檔（read_file）；列版本（list_versions）只取 metadata，不碰 content。
- 版本目錄名即 version（report_trial_/analysis_ 前綴＋時間戳），與檔案系統落點同一套命名。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from backend.app.db.connection import get_pool


def upload_run_dir(run_dir: Path | str) -> int:
    """把一個報表版本目錄內的檔案逐檔寫進 DB，回傳寫入檔數。

    版本＝目錄名。只收目錄第一層的檔案（報表引擎不產子目錄）；逐檔一次 INSERT ... ON
    CONFLICT DO UPDATE，同版本重跑覆蓋同名檔。單張 SVG 量級小（數十 KB），逐檔整讀
    不需分塊。
    """
    run_dir = Path(run_dir)
    version = run_dir.name
    files = sorted(p for p in run_dir.iterdir() if p.is_file())
    if not files:
        return 0
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            for path in files:
                content = path.read_bytes()
                cur.execute(
                    """
                    INSERT INTO app_layer.report_artifacts
                        (version, filename, content, file_hash, byte_size)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (version, filename) DO UPDATE
                        SET content = EXCLUDED.content,
                            file_hash = EXCLUDED.file_hash,
                            byte_size = EXCLUDED.byte_size
                    """,
                    (version, path.name, content,
                     hashlib.sha256(content).hexdigest(), len(content)),
                )
        conn.commit()
    return len(files)


def list_filenames(version: str) -> set:
    """某版本的全部檔名集合（不撈 content）。

    效率契約：存在性判斷一律用本函式的一次查詢，不得為判斷存在而 read_file
    整個 blob（2026-07-31 content 端點 12 秒的教訓）。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT filename FROM app_layer.report_artifacts WHERE version = %s",
                (version,),
            )
            return {row[0] for row in cur.fetchall()}


def read_file(version: str, filename: str) -> bytes | None:
    """取回單一產物內容；不存在回 None（呼叫端才能明確回 404，不猜路徑）。

    效率契約：只撈這一列的 content，不因為要一張圖而把整版產物拉回。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM app_layer.report_artifacts "
                "WHERE version = %s AND filename = %s",
                (version, filename),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return bytes(row[0])


def list_versions() -> list[dict]:
    """列出 DB 內所有報表版本（新到舊），每筆只帶顯示用 metadata。

    效率契約：**不選產物內容**（report_data.json 可達 270KB）——版本一多也不會
    把它拉回。只認含 report_data.json 的版本（與檔案系統端 _run_dirs 判準一致）。

    🔴 2026-08-17：連 `version_meta.json` 一起帶回（~120B）。原本列表端點要過濾
    workspace 時，**逐版**去讀這個小檔，而 `_DbRunSource.read_bytes` 一次要兩趟
    （先查檔名集合、再讀內容）——48 個版本 ＝ 97 趟往返、開頁等 4.3 秒，且每產
    一份報表就再慢一點。⚠ 單筆便宜不等於總量便宜，這裡的成本在**趟數**不在位元組。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT version,
                       bool_or(filename = 'narratives.json') AS has_narratives,
                       (array_agg(content) FILTER (
                            WHERE filename = 'version_meta.json'))[1] AS meta
                FROM app_layer.report_artifacts
                GROUP BY version
                HAVING bool_or(filename = 'report_data.json')
                ORDER BY version DESC
                """
            )
            rows = cur.fetchall()
    return [
        {
            "version": row[0],
            "has_narratives": bool(row[1]),
            # 沒有 meta 的舊版本回 None——消費端據此判定「不歸屬任何 workspace」。
            "workspace_id": _meta_workspace_id(row[2]),
        }
        for row in rows
    ]


def _meta_workspace_id(raw) -> int | None:
    """從 version_meta.json 的位元組取 workspace_id；缺值或格式壞掉一律回 None。

    ⚠ 解析失敗不得讓整個版本清單掛掉——壞一筆 meta 的代價應該是「那一版不歸屬
    workspace」，不是報表頁整頁空白。
    """
    if raw is None:
        return None
    import json

    try:
        value = json.loads(bytes(raw).decode("utf-8")).get("workspace_id")
        return int(value) if value is not None else None
    except (ValueError, TypeError, UnicodeDecodeError, AttributeError):
        return None


def list_ppt_files(version: str) -> list[dict]:
    """列某報表版本下的所有 .pptx 檔清單（#10：PPT 版本掛在報表版本下，_rN 序號不覆蓋）。

    只回顯示用 metadata（filename／byte_size），**不選 content**——列清單不把 .pptx 內容
    撈回。沿 app_layer.report_artifacts 查，不新表；限定該 version，只取副檔名為 .pptx 者，
    依 filename 排序（同版本重跑的 _rN 序號自然遞增排列）。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT filename, byte_size
                FROM app_layer.report_artifacts
                WHERE version = %s AND filename LIKE '%%.pptx'
                ORDER BY filename
                """,
                (version,),
            )
            rows = cur.fetchall()
    return [{"filename": row[0], "byte_size": row[1]} for row in rows]


def list_files(version: str) -> list[dict]:
    """取某報表版本的**全部檔案（含 content）**，供跨容器落地成本機目錄。

    ⚠ 與 list_versions／list_ppt_files 的「不選 content」契約刻意不同：那兩支是列清單，
    本支的用途就是把整包搬到本機檔案系統（materialize_version），非拿內容不可。
    呼叫端只有 materialize_version 一處，不當一般查詢用——避免有人拿它去做列表而把
    整包產物拉回。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT filename, content FROM app_layer.report_artifacts "
                "WHERE version = %s ORDER BY filename",
                (version,),
            )
            rows = cur.fetchall()
    return [{"filename": row[0], "content": bytes(row[1])} for row in rows]


#: 本機報表快取保留幾個版本（2026-08-03 使用者定案）。
#: ⚠ 快取只是 DB 產物（`app_layer.report_artifacts`）的本機落地副本，
#: 刪了下次 materialize 會重建——真身不在這裡，清理是安全的。
REPORT_CACHE_KEEP = 5

#: 版本目錄的命名前綴（`report_trial_YYYYMMDD_HHMMSS`）。
#: ⚠ 只清符合這個形狀的目錄：同層若有人放了別的東西，不該被順手刪掉。
_CACHE_DIR_PREFIX = "report_trial_"


def prune_cache(cache_root: Path | str, keep: int = REPORT_CACHE_KEEP) -> list[str]:
    """只保留最近 `keep` 個版本目錄，回傳被刪掉的版本名。

    為什麼要有這個（2026-08-03）：`materialize_version` 每次落地一個版本卻**從不清**，
    實測累積 13 個版本約 28 MB，而且只會一直長。

    ⚠ 這與 `output/_verify/` 是同一個教訓（AGENTS.md：27 個目錄 90.9 MB）——
    「換落點不等於解決問題，沒設保留策略就只是換個地方繼續膨脹」。

    ⚠ 排序用**目錄名**不是 mtime：版本名本身帶時間戳（report_trial_YYYYMMDD_HHMMSS），
    而 mtime 會被「重新讀取舊版本」之類的操作改掉，導致刪錯。
    """
    root = Path(cache_root)
    if not root.is_dir():
        return []
    versions = sorted(d for d in root.iterdir()
                      if d.is_dir() and d.name.startswith(_CACHE_DIR_PREFIX))
    doomed = versions[:-keep] if keep > 0 else versions
    removed: list[str] = []
    for path in doomed:
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path.name)
    return removed


def materialize_version(version: str, cache_root: Path | str) -> Path:
    """把 DB 內某報表版本落地成 `<cache_root>/<version>/` 目錄，回傳該目錄路徑。

    用途（2026-07-27 待辦 9d）：`ai:narrative` 在**使用者本機 Companion** 執行，
    但報表由**容器內 worker** 產出、只存在 report_artifacts 表——runner 找本機
    output/full_report_latest/ 必然落空（實機 job 95 即此）。本函式把該版本整包搬到
    本機暫存目錄，CLI 就能照原本的方式讀 report_data.json 與圖檔。

    ⚠ 目錄名必須等於 version：下游 `resolve_run_dir` 以 `run_dir.name` 當版本號，
    寫進 narratives.json 的 based_on_version，名字不對整份解讀會被判過期。

    版本不存在（一個檔案都沒有）時 raise FileNotFoundError，不回空目錄——
    回空目錄會讓下游誤判成「報表沒內容」而產出空解讀，比直接失敗更難查。
    """
    files = list_files(version)
    if not files:
        raise FileNotFoundError(f"report_artifacts 內查無此版本的檔案：{version}")
    run_dir = Path(cache_root) / version
    run_dir.mkdir(parents=True, exist_ok=True)
    for item in files:
        (run_dir / item["filename"]).write_bytes(item["content"])
    # ⚠ 落地**之後**才清：先清可能把剛要用的版本也算進舊的，
    #   而且失敗時至少新版本已經完整。
    prune_cache(cache_root)
    return run_dir
