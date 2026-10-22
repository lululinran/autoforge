"""loop 离线测试：冒烟映射、上下文裁剪、闭环状态机（judge 打桩）。"""

from pathlib import Path

import pytest

from autoforge.loop import (LLMFixer, build_tree, collect_context, judge,
                            run_loop_structured, smoke_failure)
from autoforge.schemas import Failure, FilePatch, FixRequest, PatchList, TestReport


def _report(failed=0, passed=2):
    return TestReport(passed=passed, total=passed + failed, failed=failed)


# ---- smoke_failure ----

def test_smoke_ok_returns_none():
    assert smoke_failure({"exit_code": 0, "timed_out": False,
                          "stdout": "", "stderr": ""}) is None


def test_smoke_nonzero_maps_error():
    f = smoke_failure({"exit_code": 1, "timed_out": False,
                       "stdout": "", "stderr": "ValueError: bad input\ntrace"})
    assert f is not None and f.test_name == "entrypoint_smoke"
    assert f.error_type == "ValueError"


def test_smoke_timeout():
    f = smoke_failure({"exit_code": -1, "timed_out": True, "stdout": "", "stderr": ""})
    assert f.error_type == "TimeoutError"


# ---- context ----

def test_collect_context_truncates_long_file(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n" * 500, encoding="utf-8")
    ctx = collect_context(tmp_path, [])
    assert "截断，共 500 行" in ctx["a.py"]
    assert ctx["a.py"].count("x = 1") == 400


def test_collect_context_caps_file_count(tmp_path):
    for i in range(40):
        (tmp_path / f"m{i:02d}.py").write_text(f"v = {i}\n", encoding="utf-8")
    ctx = collect_context(tmp_path, [])
    assert len(ctx) == 30


def test_collect_context_filters_ghost_paths_before_cap(tmp_path):
    """幽灵路径（traceback 提到但不存在）不得在截断时挤掉真实文件。"""
    (tmp_path / "zz_real.py").write_text("real = 1\n", encoding="utf-8")
    ghosts = " ".join(f"ghost{i:02d}.py" for i in range(35))
    fail = Failure(test_name="t", error_type="Error", message="m", traceback=ghosts)
    ctx = collect_context(tmp_path, [fail])
    assert list(ctx) == ["zz_real.py"]


def test_build_tree_sorted(tmp_path):
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    assert build_tree(tmp_path).splitlines() == ["a.py", "b.py"]


# ---- judge（run_pytest_in_sandbox 打桩） ----

def test_judge_zero_tests_collected_is_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "autoforge.loop.run_pytest_in_sandbox",
        lambda code_dir, timeout: {"passed": 0, "total": 0, "failed": 0,
                                   "failures": [], "stdout": "no tests ran"})
    report, smoke, failures = judge(tmp_path, None, timeout=30)
    assert [f.test_name for f in failures] == ["no_tests_collected"]
    assert failures[0].error_type == "NoTestsCollected"
    assert smoke == {}


# ---- LLMFixer.request_fix（client 打桩） ----

class _StubClient:
    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, messages):
        return self.payload, {"prompt_tokens": 3, "completion_tokens": 4}


def _fix_request():
    return FixRequest(target_files=[], failure_summary="boom", retry_round=1)


def test_fixer_request_fix_accepts_bare_list():
    fixer = LLMFixer(_StubClient([{"path": "mod.py", "content": "x = 1"}]))
    patches, usage = fixer.request_fix(_fix_request(), {}, "a.py")
    assert [p.path for p in patches.files] == ["mod.py"]
    assert usage == {"prompt_tokens": 3, "completion_tokens": 4}


def test_fixer_request_fix_rejects_missing_files():
    fixer = LLMFixer(_StubClient({"nope": 1}))
    with pytest.raises(ValueError, match="files"):
        fixer.request_fix(_fix_request(), {}, "")


# ---- run_loop_structured（judge 打桩） ----

class StubFixer(LLMFixer):
    def __init__(self, patches=None, usage=None, exc=None):
        self.patches = patches or PatchList(files=[])
        self.usage = usage or {"prompt_tokens": 10, "completion_tokens": 5}
        self.exc = exc

    def request_fix(self, fix_request, context_files, tree):
        if self.exc:
            raise self.exc
        return self.patches, self.usage


def _stub_judge(monkeypatch, seq):
    """按序返回 (report, smoke, failures)，耗尽后重复最后一个。"""
    calls = {"n": 0}

    def fake_judge(workdir, entry_cmd, timeout):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr("autoforge.loop.judge", fake_judge)
    return calls


def test_loop_green_first_round(tmp_path, monkeypatch):
    _stub_judge(monkeypatch, [(_report(0, 2), {"exit_code": 0}, [])])
    result = run_loop_structured(tmp_path, tmp_path, "python main.py --help",
                                 3, StubFixer(), budget_tokens=100000)
    assert result["status"] == "all_green"
    assert len(result["rounds"]) == 1
    assert (tmp_path / "rounds" / "round-1.json").is_file()


def test_loop_fixes_then_green(tmp_path, monkeypatch):
    fail = Failure(test_name="t1", error_type="AssertionError", message="m", traceback="tb")
    _stub_judge(monkeypatch, [
        (_report(1, 1), {"exit_code": 0}, [fail]),
        (_report(0, 2), {"exit_code": 0}, []),
    ])
    fixer = StubFixer(patches=PatchList(files=[FilePatch(path="main.py", content="ok")]))
    result = run_loop_structured(tmp_path, tmp_path, None, 3, fixer, budget_tokens=100000)
    assert result["status"] == "all_green"
    assert result["rounds"][0]["applied_patches"] == ["main.py"]
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "ok"


def test_loop_budget_exhausted(tmp_path, monkeypatch):
    fail = Failure(test_name="t1", error_type="AssertionError", message="m", traceback="tb")
    _stub_judge(monkeypatch, [(_report(1, 1), {"exit_code": 1}, [fail])])
    fixer = StubFixer(usage={"prompt_tokens": 99999, "completion_tokens": 99999})
    result = run_loop_structured(tmp_path, tmp_path, None, 3, fixer, budget_tokens=100)
    assert result["status"] == "budget_exhausted"
    assert "预算" in result["rounds"][-1].get("note", "")


def test_loop_fixer_error(tmp_path, monkeypatch):
    fail = Failure(test_name="t1", error_type="AssertionError", message="m", traceback="tb")
    _stub_judge(monkeypatch, [(_report(1, 1), {"exit_code": 1}, [fail])])
    fixer = StubFixer(exc=RuntimeError("api down"))
    result = run_loop_structured(tmp_path, tmp_path, None, 3, fixer, budget_tokens=100000)
    assert result["status"] == "fixer_error"


def test_loop_max_rounds(tmp_path, monkeypatch):
    fail = Failure(test_name="t1", error_type="AssertionError", message="m", traceback="tb")
    _stub_judge(monkeypatch, [(_report(1, 1), {"exit_code": 1}, [fail])])
    fixer = StubFixer()
    result = run_loop_structured(tmp_path, tmp_path, None, 2, fixer, budget_tokens=10**9)
    assert result["status"] == "max_rounds_exhausted"
    assert len(result["rounds"]) == 2
