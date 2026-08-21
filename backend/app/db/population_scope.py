"""母體範圍稽核：找出自行查 DB 的彙總，並要求它接母體或顯式豁免。

## 為什麼有這支

同型錯誤已出現三次，全部**不會報錯**，只是數字悄悄算錯：

| # | 位置 | 顯示 | 實際（滑雪機） |
|---|---|---|---|
| 1 | 報表引擎母體 | 61 件 | 55 件（2026-08-17 已修） |
| 2 | 受理局頁家族註記 | 187 | 48 |
| 3 | 封面三分法 | 281 件（設計 21） | 55 件（設計 11） |

⚠ 出現三次代表這是**系統性**的，逐次修不如立一道閘門一次抓完。

## 豁免的宣告方式

模組層宣告，理由**必須寫**（空字串不算）：

```python
POPULATION_SCOPE_EXEMPT = {
    "refresh_report_patent_base": "derived 重建：本來就是全庫，接母體反而錯",
}
```

⚠ 落點在**使用它的模組**而不是集中一份清單：集中清單會離程式碼愈來愈遠，
改了函式沒人記得回頭改清單。理由寫在旁邊，改的人看得到。

## 這道閘門的效力邊界（三問，見 deepen design §1.2）

| | 問題 | 本閘門 |
|---|---|---|
| 1 | 不看語意能判定嗎？ | ✅ 能——SQL 有沒有母體條件、有沒有登記豁免，純比對 |
| 2 | 滿足它的唯一途徑是不是把事情做對？ | ❌ **不是**——把函式塞進豁免表、理由隨便寫就過了。**代理指標** |
| 3 | 偏差是多出來還是缺席？ | ✅ **多出來的**——豁免表多一筆，diff 看得見，複核時會問「為什麼」 |

Q2 不過、Q3 過 → 屬「閘門擋結構、豁免由人複核」那一類。
⚠ **它保證「每個全庫彙總都被登記過」，不保證「登記的理由是對的」。**
豁免表變長是要被質疑的訊號，不是通關的捷徑。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re

#: 模組層豁免宣告的變數名。
EXEMPT_ATTR = "POPULATION_SCOPE_EXEMPT"

#: 掃描範圍：會產出報表數字或供 CLI 取證的層。
SCAN_DIRS = (
    "backend/app/reports",
    "backend/app/app_layer",
    "backend/app/derived",
    "backend/app/api",
    "backend/app/repositories",
    "backend/app/mcp_server",
)

#: 彙總的跡象（SQL 字面出現即算）。
_AGG = re.compile(
    r"\b(count\s*\(|sum\s*\(|avg\s*\(|min\s*\(|max\s*\(|"
    r"array_agg|jsonb_agg|string_agg|group\s+by)",
    re.IGNORECASE,
)

#: 母體條件的跡象。⚠ 這是**必要條件不是充分條件**——有 workspace_id 不代表真的
#: 用對了（第 2 例就是「有 workspace 概念但走 legacy 快照」）。閘門只擋「完全沒有」。
_SCOPED = re.compile(
    r"patent_ids|workspace_id|=\s*ANY\s*\(|patent_id\s+IN\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    """一個自行查 DB 且含彙總的函式。"""

    module: str
    func: str
    line: int
    scoped: bool
    exempt_reason: str | None

    @property
    def ok(self) -> bool:
        """接了母體，或登記了非空理由的豁免。"""
        return self.scoped or bool((self.exempt_reason or "").strip())

    def describe(self) -> str:
        return f"{self.module}:{self.line} {self.func}"


def _string_constants(node: ast.AST) -> str:
    """函式體內所有字串常數（f-string 取其字面片段）串成一段。"""
    parts: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            parts.append(sub.value)
        elif isinstance(sub, ast.JoinedStr):
            parts.extend(
                v.value for v in sub.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
    return "\n".join(parts)


def _self_executes_sql(node: ast.AST) -> bool:
    """函式體內有沒有自行 execute（cur.execute／conn.execute）。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in ("execute", "executemany"):
                return True
    return False


def _module_exemptions(tree: ast.Module) -> dict[str, str]:
    """讀模組層的豁免宣告。

    ⚠ 走 AST 不 import：import 會執行模組層程式碼（連 DB、讀環境變數），
    稽核工具不該有副作用。
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if EXEMPT_ATTR not in names or not isinstance(node.value, ast.Dict):
            continue
        out: dict[str, str] = {}
        for key, value in zip(node.value.keys, node.value.values):
            if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)):
                out[key.value] = value.value
        return out
    return {}


def scan(root: Path) -> list[Finding]:
    """掃出所有自行查 DB 且含彙總的函式。"""
    findings: list[Finding] = []
    for rel in SCAN_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            exemptions = _module_exemptions(tree)
            module = str(path.relative_to(root)).replace("\\", "/")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _self_executes_sql(node):
                    continue
                sql = _string_constants(node)
                if not _AGG.search(sql):
                    continue
                findings.append(Finding(
                    module=module,
                    func=node.name,
                    line=node.lineno,
                    scoped=bool(_SCOPED.search(sql)),
                    exempt_reason=exemptions.get(node.name),
                ))
    return findings


def violations(root: Path) -> list[Finding]:
    """既沒接母體、也沒登記豁免的——這些是閘門要擋的。"""
    return [f for f in scan(root) if not f.ok]
