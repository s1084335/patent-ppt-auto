"""可反駁性引用：`[欄位=值]`（tasks §9.5／§9.8，2026-08-19）。

## 一條原則

> **每一句你寫的判斷，都要指得出「如果哪一個引擎欄位是別的值，這句話就不成立」。**

⚠ 這不是新發明——它是本專案**已經證明有效**的那個機制的抽象化：
「發現欄逐字引用引擎字串」之所以守得住，正因為它的反駁條件是機械的
（字串不符即紅）。把同一個形狀推廣到所有判斷。

給 CLI 的操作句：
**不要說「規則錯了」，要說「規則沒看 `X` 欄位，而這個主題 `X = Y`」。**

## 三個場合共用同一種格式（🔴 唯一定義處）

| CLI 要做的事 | 必須附上 | 怎麼驗（零語意判斷） |
|---|---|---|
| 寫判讀 | ≥1 個 `[欄位=值]` | 欄位在該主題的引擎輸出裡存在，值逐字相符 |
| **加**行動（規則沒觸發） | 同上 ＋ 指出規則沒看這個欄位 | 同上 |
| **否決**行動（規則觸發了） | 同上 ＋ 指出規則看錯哪裡 | 同上 |

⚠ 三處各寫一種格式的話，其中一種一定會比較鬆。

## ⚠ 這套機制守不住什麼

三問的第二問**不過**：CLI 可以引用一個**真實存在**的欄位值，而那個值跟它的
主張毫無關係。這套機制驗的是**形狀**，不是**推理**。

它比純自由敘述強的地方在於：因為引擎**已經算過一個判定**，CLI 的偏離是
「與某一條具名規則不同意」，這在**累積**上可稽核——同一條規則反覆被同一種引用
推翻，不是規則錯就是 CLI 在鑽同一個洞，兩者都看得出來。
單次守不住，不得宣稱閘門擋得住亂寫。

## ⚠ 引用字串是內部語言

渲染前要移除或轉成讀者語言（§9.6b：內部判定不外洩）。
"""
from __future__ import annotations

import re
from typing import Any

#: 引用格式。⚠ 欄位名限英數與底線——放寬成任意字元會把「[表 2]」這種
#: 一般方括號誤判成引用，然後正常文字全部被拿去驗，整份紅。
_CITATION = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)=([^\]]+)\]")


def cite(field: str, value: Any) -> str:
    """產生一個引用。⚠ 與 `parse_citations` 互為反向，兩邊不得各自演進。"""
    return f"[{field}={value}]"


def parse_citations(text: str) -> list[tuple[str, str]]:
    """取出文字裡所有 `[欄位=值]`。"""
    return [(m.group(1), m.group(2).strip()) for m in _CITATION.finditer(text or "")]


def verify_citations(text: str, row: dict[str, Any]) -> list[str]:
    """驗引用：欄位要存在、值要逐字相符；完全沒有引用也算問題。

    ⚠ 數值比較走**字串**：引擎給 `3`（int）、CLI 寫 `"3"`，
    不轉成字串比的話合法引用會被誤擋，而 CLI 會被逼著去改一個本來正確的東西。
    """
    citations = parse_citations(text)
    if not citations:
        return ["沒有引用任何引擎欄位——判斷要指得出「哪個欄位是別的值就不成立」"]
    bad: list[str] = []
    for field, value in citations:
        if field not in row:
            bad.append(f"引用了不存在的欄位「{field}」——引擎輸出裡沒有它")
            continue
        actual = row[field]
        if str(actual) != str(value):
            bad.append(
                f"引用「{field}={value}」與引擎不符，實際是「{actual}」")
    return bad


def check_deviations(deviations: list[dict[str, Any]],
                     row: dict[str, Any]) -> list[str]:
    """檢查 CLI 對引擎判定的偏離（§9.7e-1）。

    ⚠ **回問題清單，不拋例外、不做拒絕**——擋了就變回天花板：規則沒涵蓋的
    真實機會永遠不會出現，那正是形式鎖的核心機制。呼叫端據此**留痕**，
    不是據此擋人。

    ⚠ 偏離次數本身是規則品質的訊號：同一條規則反覆被推翻，不是規則錯就是
    CLI 在鑽同一個洞。純規則設計沒有這個回饋迴路，這是它最大的盲點。
    """
    out: list[str] = []
    for d in deviations or []:
        action = str(d.get("action") or "?")
        reason = str(d.get("reason") or "")
        if not reason.strip():
            out.append(f"偏離「{action}」沒有寫理由")
            continue
        problems = verify_citations(reason, row)
        for p in problems:
            out.append(f"偏離「{action}」：{p}")
    return out
