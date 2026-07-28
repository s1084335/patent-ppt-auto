"""座標單一來源契約測試（2026-07-28）。

背景：前端要做投影片縮圖預覽（所見即所得），必須讀得到與 build_ppt 完全相同的座標。
若座標同時散在 renderer 程式與前端各一份，必然分岔——故硬性規定：
**所有版面座標只能存在 theme.json 的 `geometry`，renderer 內不得出現座標數字字面值。**

檢查方式採 AST（不是字串比對）：解析 build_ppt.py，走訪每個 renderer 函式，
找出「被當成座標傳遞」的數字字面值。判準是實際的語法位置（`Inches(...)` 引數、
`left=`／`top=`／`width=`／`height=` 關鍵字引數、`_add_band`／`_add_table` 的位置引數），
不是「原始碼裡有沒有出現某個數字」。
"""

from __future__ import annotations

import ast
from pathlib import Path

SKILL_DIR = Path("D:/力山/.agents/skills/patent-report-ppt")
BUILDER_PATH = SKILL_DIR / "scripts" / "build_ppt.py"
THEME_PATH = SKILL_DIR / "theme.json"

# 受檢查的組版函式：所有 renderer 與共用組版輔助。
LAYOUT_FUNCTIONS = {
    "_render_cover",
    "_render_header",
    "_render_chart_with_narrative",
    "_render_direction",
    "_render_table",
    "_render_table_with_narrative",
    "_render_narrative_only",
    "_add_table",
    "_add_watermark",
}

# 會把位置引數當座標用的組版輔助（依序 left, top, width, height）。
POSITIONAL_GEOMETRY_CALLS = {
    "_add_band": (1, 2, 3, 4),  # (slide, theme, left, top, width, height, color)
}

GEOMETRY_KEYWORDS = {"left", "top", "width", "height"}


def _is_number(node: ast.AST) -> bool:
    """判斷節點是否為數字字面值（含 -0.5 這種一元負號）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_number(node.operand)
    return False


def _callee_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _hardcoded_coordinates(func: ast.FunctionDef) -> list[tuple[int, str]]:
    """回傳該函式內被當座標用的數字字面值 [(行號, 說明), ...]。

    只認三種真正落到版面的語法位置，避免把字級、色碼等非座標數字誤判：
    1. `Inches(<數字>)`
    2. `left=/top=/width=/height=` 關鍵字引數為數字
    3. `_add_band(...)` 等輔助函式的座標位置引數為數字
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)

        if name == "Inches":
            for arg in node.args:
                if _is_number(arg):
                    found.append((arg.lineno, f"Inches({ast.unparse(arg)})"))

        if name in POSITIONAL_GEOMETRY_CALLS:
            for pos in POSITIONAL_GEOMETRY_CALLS[name]:
                if pos < len(node.args) and _is_number(node.args[pos]):
                    arg = node.args[pos]
                    found.append((arg.lineno, f"{name}() 第 {pos} 位置引數 = {ast.unparse(arg)}"))

        for kw in node.keywords:
            if kw.arg in GEOMETRY_KEYWORDS and _is_number(kw.value):
                found.append((kw.value.lineno, f"{name}({kw.arg}={ast.unparse(kw.value)})"))
    return found


def _layout_function_nodes() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(BUILDER_PATH.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in LAYOUT_FUNCTIONS
    }


def test_layout_functions_are_all_present():
    """先確認受檢函式都存在——避免函式改名後檢查靜默失效（假性通過）。"""
    nodes = _layout_function_nodes()
    missing = sorted(LAYOUT_FUNCTIONS - set(nodes))
    assert missing == [], f"受檢組版函式不存在（改名？）：{missing}"


def test_ast_detector_actually_catches_hardcoded_coordinates():
    """檢查器自身的有效性測試：餵一段有硬編座標的程式，必須抓得到。

    這一條防的是「檢查器寫壞了，永遠回傳空清單」造成的假性通過。
    """
    sample = ast.parse(
        "def _render_cover(slide, theme, spec, ctx):\n"
        "    slide.shapes.add_picture(p, Inches(0.5), Inches(top), width=Inches(7.6))\n"
        "    _add_band(slide, theme, 8.3, top, 4.5, 4.3, 'accent_soft')\n"
        "    _add_text(slide, theme, t, left=8.5, top=top, width=4.1, height=3.9)\n"
    )
    func = next(n for n in ast.walk(sample) if isinstance(n, ast.FunctionDef))
    hits = _hardcoded_coordinates(func)
    rendered = {desc for _, desc in hits}
    assert "Inches(0.5)" in rendered
    assert "Inches(7.6)" in rendered
    assert any("_add_band() 第 2 位置引數" in d for d in rendered)
    assert "_add_text(left=8.5)" in rendered
    # 非座標（變數 top）不得誤報。
    assert not any("top)" in d and "Inches(top)" in d for d in rendered)


def test_no_hardcoded_coordinates_in_renderers():
    """所有組版函式內不得有座標數字字面值；座標唯一來源為 theme.json geometry。"""
    nodes = _layout_function_nodes()
    offenders: list[str] = []
    for name in sorted(nodes):
        for lineno, desc in _hardcoded_coordinates(nodes[name]):
            offenders.append(f"{name} L{lineno}: {desc}")
    assert offenders == [], (
        "renderer 內仍有硬編座標，必須抽進 theme.json 的 geometry：\n  "
        + "\n  ".join(offenders)
    )
