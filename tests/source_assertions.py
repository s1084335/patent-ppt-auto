"""原始碼斷言的共用工具：**只看會被執行的部分**。

## 為什麼需要這支

2026-08-18 一天內同型錯誤發生**四次**——測試斷言「某字串不得出現在原始碼」，
結果被**自己寫的說明文字**餵飽：

| # | 場合 | 被誤判的來源 |
|---|---|---|
| 1 | migration 契約測試 | SQL 裡的 `-- UNION` 註解 |
| 2 | 受理局註記測試 | Python 註解引述舊字串「存活家族共」 |
| 3 | 範本期程測試 | JSON 的 `_排序說明` 引述「短期 0–3 個月」 |
| 4 | `_compose` 測試 | Python 註解寫「`slide_roadmap` 暫留供追溯」 |

四次都是同一個形狀：**修好一件事之後，在旁邊寫下「原本是什麼」**——
那是好的註解習慣，卻讓「不得出現」型的斷言誤報。

⚠ 這也是「假性通過」的鏡像：正向斷言（`assertIn`）會被註解餵飽而假綠，
反向斷言（`assertNotIn`）會被註解絆倒而假紅。**兩者都要剝**。

用法：

```python
from tests.source_assertions import executable_source

src = executable_source(inspect.getsource(func))
self.assertNotIn("slide_roadmap", src)
```
"""
from __future__ import annotations

import re


def strip_python_comments(src: str) -> str:
    """去掉 Python 的 `#` 註解（保留字串字面內的 `#`）。

    ⚠ 不做完整詞法分析——用 `tokenize` 才嚴謹，但對「檢查某識別字有沒有被呼叫」
    這個用途，逐行切 `#` 已足夠且不會誤刪程式碼；唯一的取捨是字串裡帶 `#` 的行
    會被截斷，那類行本來就不該拿來做識別字斷言。
    """
    out = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # 行內註解：只在 `#` 前後都不像字串內容時切（保守做法：出現在引號後就不切）
        idx = line.find("#")
        if idx != -1 and line.count('"', 0, idx) % 2 == 0 and line.count("'", 0, idx) % 2 == 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def strip_docstrings(src: str) -> str:
    """去掉三引號區塊（docstring 與長註解）。"""
    return re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "", src)


def executable_source(src: str) -> str:
    """只留會被執行的程式：剝掉 docstring 與 `#` 註解。"""
    return strip_python_comments(strip_docstrings(src))


def strip_sql_comments(sql: str) -> str:
    """去掉 SQL 的 `--` 行註解與 `/* */` 區塊註解。"""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def content_only(data: dict) -> dict:
    """去掉範本裡 `_xxx說明` 這類給人看的註記鍵，只留內容欄位。"""
    return {k: v for k, v in data.items() if not str(k).startswith("_")}
