"""cli 离线测试：入口探测（含引号防护）、会话目录、报告汇总、loop_error 容错。"""

import json

from autoforge import cli


def test_discover_entry_main_py(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "main.py").write_text("", encoding="utf-8")
    assert cli.discover_entry(tmp_path) == "python pkg/main.py --help"


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


def test_cmd_fix_loop_error_writes_report_and_returns_6(tmp_path, monkeypatch):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class FakeFixer:
        def __init__(self, client):
            pass

    monkeypatch.setattr(cli, "load_config",
                        lambda *a, **k: {"api_key": "k", "base_url": "http://x",
                                         "model": "fake"})
    monkeypatch.setattr(cli, "LLMClient", lambda **kw: object())
    monkeypatch.setattr(cli, "LLMFixer", FakeFixer)
    monkeypatch.setattr(cli, "run_loop_structured",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("docker down")))

    code = cli.main(["fix", str(src), "--max-rounds", "2"])
    assert code == 6
    report_path = sorted((tmp_path / "runs").glob("*/autoforge_report.json"))[-1]
    final = json.loads(report_path.read_text(encoding="utf-8"))
    assert final["status"] == "loop_error"
    assert "docker down" in final["error"]
    assert (report_path.parent / "events.json").is_file()
