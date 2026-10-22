"""loop.py — 判卷-修复闭环（生成即第 0 轮提案，本模块负责后续所有轮）。

每轮：沙箱判卷（pytest+入口冒烟）→ 结构化失败 → LLM 补丁 → apply → 复验。
会话产物落 <session_dir>/rounds/round-N.json。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from autoforge.llm import LLMClient
from autoforge.schemas import Failure, FixRequest, PatchList, TestReport, coerce_patch_list
from autoforge.sandbox import run_in_sandbox, run_pytest_in_sandbox

MAX_CONTEXT_LINES = 400
MAX_CONTEXT_FILES = 30

FIX_SYSTEM_PROMPT = """你是资深 Python 工程师，负责修复一个 CLI 项目的缺陷。
你会收到：项目文件树、关键文件的完整内容、以及测试/入口冒烟失败的完整报告。
请只输出严格的 JSON（不带 markdown 代码块、不带任何解释文字），格式：
{"files": [{"path": "相对路径", "content": "该文件的完整最终内容"}]}
要求：
1. content 必须是该文件的完整最终内容（可直接运行），禁止省略、占位符或"其余不变"。
2. 只返回需要新增或修改的文件，不要返回无关文件。
3. 遵循上下文里现有代码的分层与命名，保持与调用方（main.py / 测试）的签名兼容。
"""


class LLMFixer:
    """修复提案器：组装上下文 → 统一客户端 → PatchList。"""

    def __init__(self, client: LLMClient):
        self.client = client

    def build_user_prompt(self, fix_request: FixRequest,
                          context_files: dict[str, str], tree: str) -> str:
        parts = [f"# 项目文件树\n{tree}",
                 f"# 失败报告（第 {fix_request.retry_round} 轮）\n{fix_request.failure_summary}"]
        for path, content in context_files.items():
            parts.append(f"# 文件: {path}\n```python\n{content}\n```")
        parts.append("请输出修复 JSON。若失败涉及 import 的模块缺失或损坏，直接给出该模块的完整实现。")
        return "\n\n".join(parts)

    def request_fix(self, fix_request: FixRequest, context_files: dict[str, str],
                    tree: str) -> tuple[PatchList, dict]:
        messages = [
            {"role": "system", "content": FIX_SYSTEM_PROMPT},
            {"role": "user", "content": self.build_user_prompt(fix_request, context_files, tree)},
        ]
        obj, usage = self.client.chat_json(messages)
        return coerce_patch_list(obj), usage


def _norm_path(p: str) -> str:
    """traceback 里的路径是容器视角（/app/...），转成仓库相对路径。"""
    return p[len("/app/"):] if p.startswith("/app/") else p.lstrip("./")


def build_tree(workdir: Path) -> str:
    return "\n".join(sorted(
        str(p.relative_to(workdir)) for p in workdir.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ))


def collect_context(workdir: Path, failures: list[Failure]) -> dict[str, str]:
    """收集失败 traceback 里提到的文件 + 全项目 .py 扫描，作为修复上下文。"""
    wanted: set[str] = set()
    for f in failures:
        for m in re.finditer(r"[\w./-]+\.py", f.traceback + " " + f.message):
            wanted.add(_norm_path(m.group(0)))
    # 全项目 .py 都纳入上下文（带文件数上限），避免对特定项目布局硬编码
    for p in workdir.rglob("*.py"):
        if "__pycache__" not in p.parts:
            wanted.add(str(p.relative_to(workdir)))

    # 先过滤幽灵路径（traceback 提到但不存在，如标准库/容器路径），再截断上限，
    # 避免幽灵条目把真实文件挤出 MAX_CONTEXT_FILES 名额
    context: dict[str, str] = {}
    for rel in [r for r in sorted(wanted) if (workdir / r).is_file()][:MAX_CONTEXT_FILES]:
        lines = (workdir / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > MAX_CONTEXT_LINES:
            content = "\n".join(lines[:MAX_CONTEXT_LINES]) + f"\n# ...(截断，共 {len(lines)} 行)"
        else:
            content = "\n".join(lines)
        context[rel] = content
    return context


def smoke_failure(res: dict) -> Failure | None:
    """入口冒烟非零退出 → 转成 pseudo-Failure（test_name=entrypoint_smoke）。"""
    if res["exit_code"] == 0 and not res["timed_out"]:
        return None
    output = (res["stderr"] or "") + "\n" + (res["stdout"] or "")
    tail_lines = [ln for ln in output.splitlines() if ln.strip()][-40:]
    m = re.search(r"^([\w.]*(?:Error|Exception))\b[ :]", output, re.MULTILINE)
    return Failure(
        test_name="entrypoint_smoke",
        error_type=m.group(1) if m else ("TimeoutError" if res["timed_out"] else "NonZeroExit"),
        message=next((ln for ln in tail_lines if "Error" in ln or "error" in ln), ""),
        traceback="\n".join(tail_lines),
    )


def judge(workdir: Path, entry_cmd: str | None, timeout: int) -> tuple[TestReport, dict, list[Failure]]:
    """跑一轮判卷：pytest + 入口冒烟，返回 (TestReport, 冒烟原始结果, 汇总失败列表)。"""
    report = TestReport(**run_pytest_in_sandbox(workdir, timeout=timeout))
    failures = list(report.failures)
    if report.failed > 0 and not failures:
        # 兜底：failed>0 却没解析出失败块（如输出形状再变）——把原文末段整体回传，绝不漏报
        failures.append(Failure(
            test_name="pytest_unparsed_failures",
            error_type="TestFailure",
            message=f"pytest 报告 {report.failed} 个失败/错误，但未能解析出 FAILURES/ERRORS 区段",
            traceback=(report.stdout or "")[-3000:],
        ))
    if report.timed_out:
        # 超时强杀与「缺测试」必须分开：计数全 0 只是表象，这是疑似死循环/挂起
        failures.append(Failure(
            test_name="pytest_timeout",
            error_type="TimeoutError",
            message=f"pytest 超过 {timeout}s 被沙箱强杀（疑似死循环/挂起）",
            traceback=(report.stderr or "")[-1000:],
        ))
    if report.total == 0 and not report.timed_out:
        # 零收集也是失败：无测试 ≠ 通过，交给修复闭环补测试
        failures.append(Failure(
            test_name="no_tests_collected",
            error_type="NoTestsCollected",
            message="pytest 未收集到任何用例（项目缺少测试）",
            traceback=report.stdout[-1000:],
        ))
    smoke: dict = {}
    if entry_cmd:
        smoke = run_in_sandbox(workdir, entry_cmd, timeout=timeout)
        sf = smoke_failure(smoke)
        if sf:
            failures.append(sf)
    return report, smoke, failures


def save_round_report(session_dir: Path, rnd: int, report: TestReport, smoke: dict,
                      failures: list[Failure], fixed: list[str], usage: dict) -> Path:
    out = session_dir / "rounds" / f"round-{rnd}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "round": rnd,
        "pytest": report.model_dump(),
        "smoke": smoke,
        "failures": [f.model_dump() for f in failures],
        "applied_patches": fixed,
        "llm_usage": usage,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def apply_patches(workdir: Path, patches: PatchList) -> list[str]:
    """把补丁写入工作目录，返回写入的相对路径。"""
    written = []
    for f in patches.files:
        target = workdir / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content, encoding="utf-8")
        written.append(f.path)
    return written


def run_loop_structured(session_dir: Path, workdir: Path, entry_cmd: str | None,
                        max_rounds: int, fixer: LLMFixer, budget_tokens: int,
                        timeout: int = 120, verbose: bool = True) -> dict:
    """判卷+修复闭环。返回 {status, rounds, token_total}。

    session_dir：会话根（rounds/ 与最终报告落此处）；
    workdir：被判定的工作目录（run 场景=生成产物目录；fix 场景=输入项目的副本）。
    budget_tokens：修复阶段可用预算（生成段已由调用方预扣）。
    status ∈ all_green | max_rounds_exhausted | budget_exhausted | fixer_error
    """
    log = print if verbose else (lambda *a, **k: None)
    rounds: list[dict] = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    status = "max_rounds_exhausted"

    for rnd in range(1, max_rounds + 1):
        log(f"\n========== 第 {rnd}/{max_rounds} 轮：判卷 ==========")
        report, smoke, failures = judge(workdir, entry_cmd, timeout)
        log(f"[loop] pytest: {report.failed} failed / {report.passed} passed"
            f" | 冒烟: exit={smoke.get('exit_code')} | 失败合计: {len(failures)}")

        if not failures:
            rounds.append({"round": rnd, "pytest_failed": report.failed,
                           "pytest_passed": report.passed, "smoke_exit": smoke.get("exit_code"),
                           "failures": [], "applied_patches": [], "usage": {}})
            save_round_report(session_dir, rnd, report, smoke, [], [], {})
            status = "all_green"
            log("\n[loop] ✅ 全部通过（pytest + 入口冒烟）。")
            break

        if sum(total_usage.values()) >= budget_tokens:
            status = "budget_exhausted"
            rounds.append({"round": rnd, "pytest_failed": report.failed,
                           "pytest_passed": report.passed, "smoke_exit": smoke.get("exit_code"),
                           "failures": [f.model_dump() for f in failures],
                           "applied_patches": [], "usage": {},
                           "note": f"token 预算 {budget_tokens} 已用完，停止修复"})
            save_round_report(session_dir, rnd, report, smoke, failures, [], {})
            log(f"\n[loop] 💸 token 预算 {budget_tokens} 已用完，停止。")
            break

        fix_request = FixRequest(
            target_files=sorted({
                _norm_path(p) for f in failures
                for p in re.findall(r"[\w./-]+\.py", f.traceback + f.message)
                if (workdir / _norm_path(p)).is_file()
            }),
            failure_summary="\n\n".join(
                f"[{f.error_type}] {f.test_name}\n{f.message}\n{f.traceback}" for f in failures),
            suggested_fix="",
            retry_round=rnd,
        )
        context = collect_context(workdir, failures)
        log(f"[loop] 回传 LLM 修复（上下文 {len(context)} 个文件, "
            f"target={fix_request.target_files or '由模型判断'}）...")
        try:
            patches, usage = fixer.request_fix(fix_request, context, build_tree(workdir))
        except Exception as exc:
            log(f"[loop] ⚠️ LLM 修复调用失败: {exc}")
            rounds.append({"round": rnd, "pytest_failed": report.failed,
                           "pytest_passed": report.passed, "smoke_exit": smoke.get("exit_code"),
                           "failures": [f.model_dump() for f in failures],
                           "applied_patches": [], "usage": {"error": str(exc)}})
            save_round_report(session_dir, rnd, report, smoke, failures, [], {"error": str(exc)})
            status = "fixer_error"
            break
        for k in total_usage:
            total_usage[k] += usage.get(k, 0)
        fixed = apply_patches(workdir, patches)
        log(f"[loop] 已应用补丁: {fixed or '（模型未返回文件）'}")
        log(f"[loop] 累计 token: {total_usage}")
        rounds.append({"round": rnd, "pytest_failed": report.failed,
                       "pytest_passed": report.passed, "smoke_exit": smoke.get("exit_code"),
                       "failures": [f.model_dump() for f in failures],
                       "applied_patches": fixed, "usage": usage})
        save_round_report(session_dir, rnd, report, smoke, failures, fixed, usage)

    return {"status": status, "rounds": rounds, "token_total": total_usage}
