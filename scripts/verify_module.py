"""模組驗證流程的可執行版（AGENTS.md「模組驗證流程」的工具面）。

🔴 為什麼要有這支：那套流程的價值全在**界定範圍**——全庫 ruff 報 742 個問題、
整檔覆蓋率 22%，兩個數字都判斷不了「這次做的東西好不好」。要看的是
**本次新增行**的問題數與覆蓋率，而那需要把工具輸出與 `git diff` 的行號做交集。
手算會漏、每次重打指令會不一致，故收斂成一支。

用法（在專案根目錄）：

    uv run python scripts/verify_module.py --base HEAD~1 --tests tests/test_a.py tests/test_b.py

參數：
  --base    比較基準（預設 HEAD~1）；要驗一整個分支就給分支點
  --tests   要跑的測試檔（覆蓋率以這些測試為準）
  --paths   靜態分析與覆蓋率的來源範圍（預設 backend/app）
  --skip    跳過某階段：lint／coverage／complexity

⚠ 本腳本只**量測與回報**，不修改任何程式；門檻未達標時以退出碼 1 表示，
判斷與修正仍由人／agent 決定（見 AGENTS.md 的四項驗收門檻）。
⚠ 工具一律 `uv run --with` 暫時性執行，不裝進專案環境。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from pathlib import Path

# ⚠ Windows 主控台預設 cp950，含 ≥／≤ 這類符號會 UnicodeEncodeError 而中斷輸出
# ——本專案既有的坑（見 work-log 2026-08-04）。輸出一律轉 UTF-8。
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SEP = chr(92)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 驗收門檻（AGENTS.md「模組驗證流程」）。改門檻要連同該節一起改，不得只改這裡。
LINT_MAX_NEW_ISSUES = 0
COVERAGE_MIN_PERCENT = 90.0
COMPLEXITY_MAX_RANK = "B"  # A–B（CC ≤ 10）


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """跑外部指令並回傳結果（不 raise；由呼叫端判讀退出碼）。"""
    return subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


def added_lines(base: str) -> dict[str, set[int]]:
    """本次新增行：{相對路徑: {行號}}。

    ⚠ 用 `-U0`：帶上下文的話未改動的行也會被算進來，範圍就失真了。
    """
    diff = run(["git", "diff", base, "-U0"]).stdout
    out: dict[str, set[int]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            out.setdefault(current, set())
        elif line.startswith("@@") and current:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) or 1)
                out[current].update(range(start, start + count))
    return out


def _match_key(path: str, added: dict[str, set[int]]) -> str | None:
    """把工具輸出的絕對路徑對回 diff 的相對路徑。"""
    normalized = path.replace(SEP, "/")
    return next((key for key in added if normalized.endswith(key)), None)


def check_lint(paths: list[str], added: dict[str, set[int]]) -> tuple[bool, str]:
    """靜態分析：只算落在本次新增行的問題（既有問題另計、不混報）。"""
    result = run(["uv", "run", "--no-project", "--python", "3.12", "--with", "ruff",
                  "ruff", "check", "--output-format", "json", *paths])
    try:
        issues = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return False, f"ruff 輸出無法解析：{(result.stderr or '')[:200]}"
    mine = []
    for issue in issues:
        key = _match_key(issue["filename"], added)
        row = (issue.get("location") or {}).get("row")
        if key and row in added[key]:
            mine.append(f"{key}:{row} {issue['code']} {issue['message'][:60]}")
    ok = len(mine) <= LINT_MAX_NEW_ISSUES
    lines = [f"新增行問題 {len(mine)}／全庫 {len(issues)}（門檻 ≤{LINT_MAX_NEW_ISSUES}）"]
    lines.extend(f"    {item}" for item in mine[:20])
    return ok, "\n".join(lines)


def check_coverage(tests: list[str], source: str,
                   added: dict[str, set[int]]) -> tuple[bool, str]:
    """測試完整性：算**新增行**覆蓋率（整檔覆蓋率含大量既有碼，看不出新功能品質）。"""
    run(["uv", "run", "--with", "coverage", "python", "-m", "coverage", "run",
         f"--source={source}", "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider"])
    report = PROJECT_ROOT / ".verify_coverage.json"
    run(["uv", "run", "--with", "coverage", "python", "-m", "coverage", "json",
         "-o", str(report)])
    if not report.exists():
        return False, "coverage 沒有產出資料（測試是否真的執行到？）"
    data = json.loads(report.read_text(encoding="utf-8"))
    report.unlink(missing_ok=True)
    total_new = total_hit = 0
    detail: list[str] = []
    for path, info in sorted(data["files"].items()):
        key = _match_key(path, added)
        if not key or key.startswith("tests/"):
            continue
        executable = set(info["executed_lines"]) | set(info["missing_lines"])
        new_exec = executable & added[key]
        if not new_exec:
            continue
        hit = new_exec & set(info["executed_lines"])
        total_new += len(new_exec)
        total_hit += len(hit)
        detail.append(f"    {key}: {len(hit)}/{len(new_exec)}"
                      f" = {len(hit) / len(new_exec) * 100:.0f}%")
        missed = sorted(new_exec - set(info["executed_lines"]))
        if missed:
            detail.append(f"        未覆蓋行：{missed}")
    if not total_new:
        return True, "本次無新增可執行行（純文件／設定變更）"
    percent = total_hit / total_new * 100
    ok = percent >= COVERAGE_MIN_PERCENT
    return ok, "\n".join(
        [f"新增行覆蓋率 {total_hit}/{total_new} = {percent:.0f}%"
         f"（門檻 ≥{COVERAGE_MIN_PERCENT:.0f}%）", *detail])


def check_complexity(added: dict[str, set[int]]) -> tuple[bool, str]:
    """圈複雜度：只看**本次動到的函式**（函式行段與新增行有交集者）。

    🔴 2026-08-05 本腳本自我驗證時抓到的缺陷：原本列出動到的**檔案**裡所有
    C 級以上函式——`chart_runner` 本來就有 D(25)／D(22) 兩支既有函式，
    這道門檻於是永遠不會過，等於形同虛設。與 lint／coverage 同一個原則：
    **範圍要界定到本次新增的行**，否則量到的是專案歷史、不是這次的品質。
    ⚠ 用 radon 的 JSON 輸出（含 endline）才算得出函式行段；`-s` 純文字沒有結束行。
    """
    files = [key for key in added
             if key.endswith(".py") and not key.startswith("tests/")]
    if not files:
        return True, "本次未動到 .py"
    result = run(["uv", "run", "--no-project", "--python", "3.12", "--with", "radon",
                  "radon", "cc", "--json", *files])
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return False, f"radon 輸出無法解析：{(result.stderr or '')[:200]}"
    over: list[str] = []
    touched = 0
    for path, blocks in data.items():
        key = _match_key(path, added)
        if not key or not isinstance(blocks, list):
            continue
        for block in blocks:
            span = set(range(block.get("lineno", 0), block.get("endline", 0) + 1))
            if not added[key] & span:
                continue  # 這支函式本次沒動到
            touched += 1
            if str(block.get("rank", "A")) > COMPLEXITY_MAX_RANK:
                over.append(f"{key}:{block['lineno']} {block.get('name')} "
                            f"- {block.get('rank')} ({block.get('complexity')})")
    body = "\n".join(f"    {item}" for item in over[:20]) or "    （無）"
    return not over, (f"本次動到的函式 {touched} 支，超過 {COMPLEXITY_MAX_RANK} 級者 "
                      f"{len(over)} 支：\n{body}")


def main() -> int:
    parser = argparse.ArgumentParser(description="模組驗證流程（AGENTS.md 同名章節）")
    parser.add_argument("--base", default="HEAD~1", help="比較基準（預設 HEAD~1）")
    parser.add_argument("--tests", nargs="+", required=True, help="要跑的測試檔")
    parser.add_argument("--paths", nargs="+", default=["backend/app"], help="靜態分析範圍")
    parser.add_argument("--source", default="backend.app", help="覆蓋率來源套件")
    parser.add_argument("--skip", nargs="*", default=[], help="跳過 lint／coverage／complexity")
    args = parser.parse_args()

    added = added_lines(args.base)
    if not added:
        print(f"⚠ {args.base} 之後沒有變更，無從驗證")
        return 1

    results: list[tuple[str, bool, str]] = []
    if "lint" not in args.skip:
        results.append(("靜態分析", *check_lint(args.paths, added)))
    if "complexity" not in args.skip:
        results.append(("圈複雜度", *check_complexity(added)))
    if "coverage" not in args.skip:
        results.append(("測試覆蓋", *check_coverage(args.tests, args.source, added)))

    print("\n" + "=" * 60)
    for name, ok, detail in results:
        print(f"[{'通過' if ok else '未達標'}] {name}")
        print(detail)
    print("=" * 60)
    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print(f"未達標：{'、'.join(failed)}"
              f"（功能正確性與回歸測試請另依 AGENTS.md 執行並自行判讀）")
        return 1
    print("量測項全數達標（功能正確性與回歸測試請另依 AGENTS.md 執行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
