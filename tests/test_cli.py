"""cli 离线测试：入口探测（含引号防护、优先级）、会话目录、报告汇总、loop_error 容错。"""

import json

from autoforge import cli
from autoforge.generator import GenerationResult


def test_discover_entry_main_py(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "main.py").write_text("", encoding="utf-8")
    assert cli.discover_entry(tmp_path) == "python pkg/main.py --help"


def test_discover_entry_root_main_py_wins_over_nested(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    assert cli.discover_entry(tmp_path) == "python main.py --help"


def test_discover_entry_quotes_paths_with_spaces(tmp_path):
    (tmp_path / "my app").mkdir()
    (tmp_path / "my app" / "main.py").write_text("", encoding="utf-8")
    assert cli.discover_entry(tmp_path) == "python 'my app/main.py' --help"


def test_discover_entry_none(tmp_path):
    assert cli.discover_entry(tmp_path) is None


def test_create_run_dir_under_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # runs/ 建在当前目录（仓库根）
    rd = cli.create_run_dir()
    assert rd.parent.name == "runs" and rd.is_dir()
    assert (rd / "workspace").is_dir()


def test_human_summary_contains_core_lines(tmp_path):
    final = {
        "idea": "demo", "model": "m",
        "generate": {"duration_s": 12.3, "strategy": "oneshot",
                     "usage": {"prompt_tokens": 1, "completion_tokens": 2}},
        "entry_cmd": "python main.py --help",
        "status": "all_green", "session_dir": str(tmp_path),
        "rounds": [{"round": 1, "pytest_failed": 0, "pytest_passed": 3,
                    "smoke_exit": 0, "applied_patches": []}],
        "token_total": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    s = cli.human_summary(final)
    assert "all_green" in s and "3P" in s and "autoforge_report.json" in s


def test_human_summary_checkmark_when_smoke_skipped():
    final = {
        "idea": "demo", "model": "m", "generate": None,
        "entry_cmd": None, "status": "all_green", "session_dir": "/tmp/s",
        "rounds": [{"round": 1, "pytest_failed": 0, "pytest_passed": 2,
                    "smoke_exit": None, "applied_patches": []}],
        "token_total": {},
    }
    s = cli.human_summary(final)
    assert "✅" in s


def test_cmd_fix_loop_error_writes_report_and_returns_6(tmp_path, monkeypatch):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class FakeFixer:
        def __init__(self, client):
            pass

    captured = {}

    def fake_loop(*a, **kw):
        captured.update(kw)
        raise RuntimeError("docker down")

    monkeypatch.setattr(cli, "load_config",
                        lambda *a, **k: {"api_key": "k", "base_url": "http://x",
                                         "model": "fake"})
    monkeypatch.setattr(cli, "LLMClient", lambda **kw: object())
    monkeypatch.setattr(cli, "LLMFixer", FakeFixer)
    monkeypatch.setattr(cli, "run_loop_structured", fake_loop)

    code = cli.main(["fix", str(src), "--max-rounds", "2"])
    assert code == 6
    # fix 直通预算：不预扣生成 token，原样传 args.budget_tokens（默认 300000）
    assert captured["budget_tokens"] == 300000
    report_path = sorted((tmp_path / "runs").glob("*/autoforge_report.json"))[-1]
    final = json.loads(report_path.read_text(encoding="utf-8"))
    assert final["status"] == "loop_error"
    assert "docker down" in final["error"]
    assert (report_path.parent / "events.json").is_file()


def test_cmd_run_generation_failed_returns_5(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_config",
                        lambda *a, **k: {"api_key": "k", "base_url": "http://x",
                                         "model": "fake"})
    monkeypatch.setattr(cli, "LLMClient", lambda **kw: object())

    def boom(idea, client):
        raise RuntimeError("api 500")

    monkeypatch.setattr(cli, "generate_oneshot", boom)

    code = cli.main(["run", "写一个计算器"])
    assert code == 5
    report_path = sorted((tmp_path / "runs").glob("*/autoforge_report.json"))[-1]
    final = json.loads(report_path.read_text(encoding="utf-8"))
    assert final["status"] == "generation_failed"
    assert "api 500" in final["error"]  # 顶层 error 字段标准化
    assert "api 500" in final["generate"]["error"]  # 细节仍保留


def test_cmd_run_pre_deducts_generation_budget(tmp_path, monkeypatch):
    """run 预扣生成 token：修复阶段预算 = budget - 生成用量(1000P+500C)，下限 0。"""
    monkeypatch.chdir(tmp_path)

    class FakeFixer:
        def __init__(self, client):
            pass

    class FakeClient:
        def __init__(self):
            self.events = []

    captured = []

    def fake_loop(*a, **kw):
        captured.append(kw)
        return {"status": "all_green", "rounds": [], "token_total": {}}

    monkeypatch.setattr(cli, "load_config",
                        lambda *a, **k: {"api_key": "k", "base_url": "http://x",
                                         "model": "fake"})
    monkeypatch.setattr(cli, "LLMClient", lambda *a, **k: FakeClient())
    monkeypatch.setattr(cli, "generate_oneshot",
                        lambda idea, client: GenerationResult(
                            files={"main.py": "pass\n"}, strategy="oneshot",
                            usage={"prompt_tokens": 1000,
                                   "completion_tokens": 500},
                            degraded=False))
    monkeypatch.setattr(cli, "LLMFixer", FakeFixer)
    monkeypatch.setattr(cli, "run_loop_structured", fake_loop)

    assert cli.main(["run", "idea", "--budget-tokens", "1200"]) == 0
    assert cli.main(["run", "idea", "--budget-tokens", "5000"]) == 0
    # 生成 1500 token 预扣：1200-1500 截断为 0；5000-1500=3500
    assert [c["budget_tokens"] for c in captured] == [0, 3500]


def test_missing_config_returns_2(tmp_path, monkeypatch, capsys):
    """缺配置文件：run/fix 都走友好报错 + 退出码 2，不裸抛 traceback。"""

    def missing(*a, **k):
        raise FileNotFoundError("配置不存在: /x")

    monkeypatch.setattr(cli, "load_config", missing)
    assert cli.main(["run", "idea"]) == 2
    proj = tmp_path / "proj"
    proj.mkdir()
    assert cli.main(["fix", str(proj)]) == 2
    err = capsys.readouterr().err
    assert "配置错误" in err and "配置不存在: /x" in err
