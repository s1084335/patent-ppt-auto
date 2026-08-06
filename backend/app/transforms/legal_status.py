"""法律狀態桶——`legal_status` 原值收斂的唯一定義處（2026-08-07 使用者定案）。

## 為什麼需要收斂

WIPS 的 `legal_status` 是**自由字面**（2026-08-07 DB 實查）：簡體（`授权`／
`审查中`／`放弃`）、同一件事三種寫法（`到期(Non-payment of Renewal / Annual fee)`／
`到期(Expiration of the term)`／`到期(Termination of patent right due to unpaid
annual fee)`）、以及 None（7 件新型無狀態值）。直接 GROUP BY 原值會把
「已失效」拆成三列、把簡體字印上簡報。

## 四桶

| 桶 | 收斂自 | 語意 |
|---|---|---|
| 已授權 | 授权／授權／已核准 | 權利存續中 |
| 審查中 | 审查中／審查中／申请／申請／已申請／已公開 | 尚未取得權利 |
| 已失效 | 到期(任何括號寫法)／放弃／放棄／无效／無效／撤回／核駁／已失效／屆滿失效 | 權利已不存續（仍具前案價值） |
| 未知 | None／空白／未見過的字面 | 資料缺——**誠實呈現，不得歸進實桶**（吞掉會虛增授權率） |

⚠ 「已核准」等九項＝TW 人工登錄值域（openspec `add-tw-legal-status-curation`，
Codex 線）。該 change 的 pending/alive/dead/unknown 彙總即對應本表四桶——
**登錄端只消費本模組，不得另寫 mapping**（同一份知識兩個落點必分岔）。

⚠ 消費端（報表 pivot、堆疊段序、圖例、narrative）一律 import 本模組，
不得各自寫字面比對——散開後 WIPS 換寫法時只會改到一處，另一處靜默錯。
"""
from __future__ import annotations

BUCKET_GRANTED = "已授權"
BUCKET_PENDING = "審查中"
BUCKET_DEAD = "已失效"
BUCKET_UNKNOWN = "未知"

# 呈現契約：堆疊段序＝圖例序＝表格欄序。固定 tuple，不得隨 dict 序漂。
STATUS_BUCKET_ORDER: tuple[str, ...] = (
    BUCKET_GRANTED, BUCKET_PENDING, BUCKET_DEAD, BUCKET_UNKNOWN,
)

# 前綴比對表（⚠ 到期後面跟著各種括號說明，用 startswith 才收得齊）。
_PREFIX_BUCKETS: tuple[tuple[str, str], ...] = (
    ("授权", BUCKET_GRANTED), ("授權", BUCKET_GRANTED),
    ("已核准", BUCKET_GRANTED),
    ("审查中", BUCKET_PENDING), ("審查中", BUCKET_PENDING),
    # ⚠ 「已申請」「已公開」要排在「申请/申請」前綴之前無所謂（startswith 都會中
    # 「已申請」→需明列：它不以「申請」開頭），但「已失效」必須明列——
    # 它不以「到期」開頭，漏了會落進未知。
    ("已申請", BUCKET_PENDING), ("已公開", BUCKET_PENDING),
    ("申请", BUCKET_PENDING), ("申請", BUCKET_PENDING),
    ("到期", BUCKET_DEAD),
    ("放弃", BUCKET_DEAD), ("放棄", BUCKET_DEAD),
    ("无效", BUCKET_DEAD), ("無效", BUCKET_DEAD),
    ("撤回", BUCKET_DEAD), ("核駁", BUCKET_DEAD),
    ("已失效", BUCKET_DEAD), ("屆滿失效", BUCKET_DEAD),
)


def status_bucket(value: object) -> str:
    """原值 → 四桶之一；空值與未見過的字面一律回「未知」，不猜、不炸。"""
    text = str(value or "").strip()
    if not text:
        return BUCKET_UNKNOWN
    for prefix, bucket in _PREFIX_BUCKETS:
        if text.startswith(prefix):
            return bucket
    return BUCKET_UNKNOWN
