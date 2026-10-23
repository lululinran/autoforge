"""cli.py — autoforge 命令行入口。

子命令：
  run "<idea>"    全流程：生成(oneshot) → 判卷 → 修复 → 报告
  judge <dir>     只判卷（沙箱 pytest + 入口冒烟），不调修复 LLM
  fix <dir>       对任意已有项目判卷+修复（闭环 generator-agnostic 的正统入口）
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import time
from pathlib import Path

from autoforge.generator import generate_oneshot
from autoforge.llm import LLMClient, load_config
from autoforge.loop import LLMFixer, apply_patches, run_loop_structured
from autoforge.sandbox import print_report, run_in_sandbox, run_pytest_in_sandbox
from autoforge.schemas import FilePatch, PatchList

RUNS_ROOT = Path("runs")  # 相对当前目录；由 create_run_dir 建在仓库根

STATUS_EXIT = {"all_green": 0, "max_rounds_exhausted": 1, "fixer_error": 3,
               "budget_exhausted": 4, "generation_failed": 5, "loop_error": 6}


def create_run_dir() -> Path:
    """新建 runs/<时间戳>/ 会话目录（含 workspace/）。"""
    session = RUNS_ROOT / time.strftime("%Y%m%d-%H%M%S")
    (session / "workspace").mkdir(parents=True, exist_ok=True)
    return session


def discover_entry(project_dir: Path) -> str | None:
    """自动探测入口：根 main.py 优先，再嵌套 main.py / cli.py / __main__.py。

    路径经 shlex.quote：文件名来自 LLM 产物，不允许注入冒烟命令。
    """
    for pat in ("main.py", "*/main.py", "cli.py", "*/cli.py",
                "__main__.py", "*/__main__.py"):
        hits = sorted(p for p in project_dir.glob(pat) if "__pycache__" not in p.parts)
        if hits:
            rel = hits[0].relative_to(project_dir).as_posix()
            return f"python {shlex.quote(rel)} --help"
    return None


def human_summary(final: dict) -> str:
    lines = ["", "=" * 62, "autoforge 报告", "=" * 62]
    gen = final.get("generate")
    if gen:
        lines.append(f"生成: {gen.get('duration_s')}s | 策略 {gen.get('strategy')} | "
                     f"token: {gen.get('usage', {}).get('prompt_tokens', 0)}P/"
                     f"{gen.get('usage', {}).get('completion_tokens', 0)}C")
    lines.append(f"判卷+修复: {len(final['rounds'])} 轮 | 状态: {final['status']} | "
                 f"修复 token: {final['token_total'].get('prompt_tokens', 0)}P/"
                 f"{final['token_total'].get('completion_tokens', 0)}C")
    for r in final["rounds"]:
        line = (f"  第{r['round']}轮 pytest {r['pytest_failed']}F/{r['pytest_passed']}P"
                f" 冒烟 exit={r['smoke_exit']}")
        if r.get("applied_patches"):
            line += f" → 修复 {r['applied_patches']}"
        if r["pytest_failed"] == 0 and r["smoke_exit"] in (0, None):
            line += " ✅"
        lines.append(line)
    lines.append(f"会话: {final['session_dir']}")
    lines.append(f"报告: {Path(final['session_dir']) / 'autoforge_report.json'}")
    return "\n".join(lines)


def _write_report(session_dir: Path, final: dict) -> None:
    (session_dir / "autoforge_report.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")


def _dump_events(session_dir: Path, client) -> None:
    (session_dir / "events.json").write_text(
        json.dumps(getattr(client, "events", []), ensure_ascii=False, indent=2),
        encoding="utf-8")


def _loop_error_exit(session_dir: Path, client, base: dict, exc: Exception) -> int:
    """闭环阶段异常的统一出口：events 落盘 + loop_error 报告 + exit 6。"""
    print(f"[autoforge] ❌ 闭环异常: {exc}", file=sys.stderr)
    _dump_events(session_dir, client)
    final = {**base, "status": "loop_error", "error": str(exc),
             "session_dir": str(session_dir), "rounds": [], "token_total": {}}
    _write_report(session_dir, final)
    print(f"[autoforge] 报告: {session_dir / 'autoforge_report.json'}")
    return STATUS_EXIT["loop_error"]


def _run_and_finalize(session_dir: Path, client, base: dict, workdir: Path,
                      entry: str | None, max_rounds: int, fixer: LLMFixer,
                      budget_tokens: int, timeout: int) -> int:
    """判卷+修复闭环与收尾（run/fix 共用）：loop_error 容错、events/报告落盘、人读汇总。"""
    try:
        result = run_loop_structured(session_dir, workdir, entry, max_rounds, fixer,
                                     budget_tokens=budget_tokens, timeout=timeout)
    except Exception as exc:
        return _loop_error_exit(session_dir, client, base, exc)
    _dump_events(session_dir, client)
    final = {**base, **result, "session_dir": str(session_dir)}
    _write_report(session_dir, final)
    print(human_summary(final))
    return STATUS_EXIT.get(final["status"], 1)


def cmd_run(args) -> int:
    try:
        cfg = load_config(args.config, args.model)
        client = LLMClient(api_key=cfg["api_key"], base_url=cfg["base_url"], model=cfg["model"])
    except FileNotFoundError as exc:
        print(f"[autoforge] 配置错误: {exc}", file=sys.stderr)
        return 2
    print(f"[autoforge] 模型: {cfg['model']} @ {cfg['base_url']}")
    session_dir = create_run_dir()
    workspace = session_dir / "workspace"
    print(f"[autoforge] 会话目录: {session_dir}")

    start = time.time()
    try:
        gen = generate_oneshot(args.idea, client)
    except Exception as exc:
        print(f"[autoforge] ❌ 生成失败: {exc}", file=sys.stderr)
        _dump_events(session_dir, client)
        final = {"idea": args.idea, "model": cfg["model"], "generate": {"error": str(exc)},
                 "error": str(exc), "entry_cmd": None, "status": "generation_failed",
                 "session_dir": str(session_dir), "rounds": [], "token_total": {}}
        _write_report(session_dir, final)
        print(f"[autoforge] 报告: {session_dir / 'autoforge_report.json'}")
        return STATUS_EXIT["generation_failed"]
    (session_dir / "generation").mkdir(exist_ok=True)
    (session_dir / "generation" / "events.json").write_text(
        json.dumps(list(client.events), ensure_ascii=False, indent=2), encoding="utf-8")
    gen_summary = {"strategy": gen.strategy, "duration_s": round(time.time() - start, 1),
                   "usage": gen.usage, "degraded": gen.degraded}
    print(f"[autoforge] 生成 {len(gen.files)} 个文件: {sorted(gen.files)}")

    try:
        apply_patches(workspace, PatchList(
            files=[FilePatch(path=p, content=c) for p, c in gen.files.items()]))
    except Exception as exc:
        return _loop_error_exit(session_dir, client,
                                {"idea": args.idea, "model": cfg["model"],
                                 "generate": gen_summary, "entry_cmd": None}, exc)
    entry = args.entry or discover_entry(workspace)
    print(f"[autoforge] 冒烟命令: {entry or '（未探测到入口，跳过冒烟）'}")

    gen_tokens = gen.usage.get("prompt_tokens", 0) + gen.usage.get("completion_tokens", 0)
    remaining = max(args.budget_tokens - gen_tokens, 0)
    base = {"idea": args.idea, "model": cfg["model"], "generate": gen_summary,
            "entry_cmd": entry}
    return _run_and_finalize(session_dir, client, base, workspace, entry,
                             args.max_rounds, LLMFixer(client), budget_tokens=remaining,
                             timeout=args.timeout)


def cmd_judge(args) -> int:
    target = Path(args.dir).resolve()
    if not target.is_dir():
        print(f"[autoforge] 目录不存在: {target}", file=sys.stderr)
        return 2
    entry = args.entry or discover_entry(target)
    print(f"[autoforge] 冒烟命令: {entry or '（未探测到，跳过）'}")
    try:
        report = run_pytest_in_sandbox(target, timeout=args.timeout)
        smoke = {"entry": entry, "exit_code": None, "timed_out": None}
        if entry is not None:
            res = run_in_sandbox(target, entry, timeout=args.timeout)
            smoke = {"entry": entry, "exit_code": res["exit_code"],
                     "timed_out": res["timed_out"]}
            head = "\n".join(res["stdout"].splitlines()[:5])
            print(f"[autoforge] 冒烟 exit={smoke['exit_code']}"
                  + (f"\n{head}" if head else ""))
    except RuntimeError as exc:
        print(f"[autoforge] ❌ 沙箱不可用: {exc}", file=sys.stderr)
        return 2
    print_report(report)
    if report["total"] == 0:
        print("[autoforge] ⚠️ 未收集到任何测试用例（不算通过）")
    if args.json:
        Path(args.json).write_text(
            json.dumps({**report, "no_tests_collected": report["total"] == 0,
                        "smoke": smoke}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    return 1 if report["timed_out"] else 0


def cmd_fix(args) -> int:
    artifact = Path(args.dir).resolve()
    if not artifact.is_dir():
        print(f"[autoforge] 目录不存在: {artifact}", file=sys.stderr)
        return 2
    try:
        cfg = load_config(args.config, args.model)
        client = LLMClient(api_key=cfg["api_key"], base_url=cfg["base_url"], model=cfg["model"])
    except FileNotFoundError as exc:
        print(f"[autoforge] 配置错误: {exc}", file=sys.stderr)
        return 2
    session_dir = create_run_dir()
    workspace = session_dir / "workspace"
    base = {"idea": None, "model": cfg["model"], "generate": None, "entry_cmd": None}
    try:
        shutil.copytree(artifact, workspace, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    except Exception as exc:
        return _loop_error_exit(session_dir, client, base, exc)
    print(f"[autoforge] 会话目录（可写副本）: {session_dir}\n[autoforge] 原目录保持只读: {artifact}")
    entry = args.entry or discover_entry(workspace)
    base["entry_cmd"] = entry
    return _run_and_finalize(session_dir, client, base, workspace, entry,
                             args.max_rounds, LLMFixer(client),
                             budget_tokens=args.budget_tokens, timeout=args.timeout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoforge",
                                     description="一句话需求 → 生成 → 禁网沙箱验收 → LLM 修复闭环")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="全流程")
    p_run.add_argument("idea")
    p_run.add_argument("--entry", default=None)
    p_run.add_argument("--max-rounds", type=int, default=3)
    p_run.add_argument("--budget-tokens", type=int, default=300000)
    p_run.add_argument("--model", default=None)
    p_run.add_argument("--timeout", type=int, default=120)
    p_run.add_argument("--config", default=None)
    p_run.set_defaults(func=cmd_run)

    p_judge = sub.add_parser("judge", help="只判卷")
    p_judge.add_argument("dir")
    p_judge.add_argument("--entry", default=None)
    p_judge.add_argument("--timeout", type=int, default=120)
    p_judge.add_argument("--json", default=None)
    p_judge.set_defaults(func=cmd_judge)

    p_fix = sub.add_parser("fix", help="对已有项目判卷+修复")
    p_fix.add_argument("dir")
    p_fix.add_argument("--entry", default=None)
    p_fix.add_argument("--max-rounds", type=int, default=3)
    p_fix.add_argument("--budget-tokens", type=int, default=300000)
    p_fix.add_argument("--model", default=None)
    p_fix.add_argument("--timeout", type=int, default=120)
    p_fix.add_argument("--config", default=None)
    p_fix.set_defaults(func=cmd_fix)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
