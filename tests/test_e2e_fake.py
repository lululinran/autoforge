"""端到端冒烟：fake transport 剧本 + 真实 Docker 沙箱。

剧本：生成一个带 bug 的项目（1 个失败测试）→ 第 1 轮判卷失败 →
fixer 返回修正文件 → 第 2 轮全绿。验证 cli.run 全链路（含报告/事件落盘、token 账本）。
"""

import json
import shutil

import pytest

from autoforge import cli
from tests.test_llm import FakeResponse, FakeTransport

def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        import docker
        docker.from_env(timeout=5).ping()
        return True
    except Exception:
        return False


docker_available = _docker_available()

GEN_FILES = {
    "files": [
        {"path": "main.py", "content": (
            "import argparse\n\n\ndef add(a, b):\n"
            "    return a - b  # bug: 应为 +\n\n\n"
            "def main():\n"
            "    parser = argparse.ArgumentParser(description='add CLI')\n"
            "    parser.add_argument('a', type=int)\n"
            "    parser.add_argument('b', type=int)\n"
            "    args = parser.parse_args()\n"
            "    print(add(args.a, args.b))\n\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n")},
        {"path": "test_main.py", "content": (
            "from main import add\n\n\ndef test_add():\n"
            "    assert add(1, 2) == 3\n"
            "    assert add(-1, 1) == 0\n")},
    ]
}

FIX_FILES = {
    "files": [
        {"path": "main.py", "content": (
            "import argparse\n\n\ndef add(a, b):\n"
            "    return a + b\n\n\n"
            "def main():\n"
            "    parser = argparse.ArgumentParser(description='add CLI')\n"
            "    parser.add_argument('a', type=int)\n"
            "    parser.add_argument('b', type=int)\n"
            "    args = parser.parse_args()\n"
            "    print(add(args.a, args.b))\n\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n")},
    ]
}


@pytest.mark.skipif(not docker_available, reason="需要 Docker")
def test_run_end_to_end_with_fake_llm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transport = FakeTransport([
        FakeResponse(json.dumps(GEN_FILES)),
        FakeResponse(json.dumps(FIX_FILES)),
    ])

    class CfgClient(cli.LLMClient):
        def __init__(self):
            super().__init__(api_key="k", base_url="http://x", model="fake",
                             transport=transport)

    monkeypatch.setattr(cli, "load_config",
                        lambda *a, **k: {"api_key": "k", "base_url": "http://x",
                                         "model": "fake"})
    monkeypatch.setattr(cli, "LLMClient", lambda *a, **k: CfgClient())

    code = cli.main(["run", "一个加法 CLI", "--max-rounds", "2",
                     "--budget-tokens", "100000"])
    assert code == 0

    report_path = sorted((tmp_path / "runs").glob("*/autoforge_report.json"))[-1]
    final = json.loads(report_path.read_text(encoding="utf-8"))
    assert final["status"] == "all_green"
    assert len(final["rounds"]) == 2
    assert final["entry_cmd"] == "python main.py --help"
    assert final["rounds"][0]["pytest_failed"] >= 1
    assert final["rounds"][0]["smoke_exit"] == 0
    assert final["rounds"][1]["pytest_failed"] == 0
    assert final["rounds"][1]["smoke_exit"] == 0
    assert final["rounds"][0]["applied_patches"] == ["main.py"]
    assert final["generate"]["strategy"] == "oneshot"
    # token 账本：生成 100/50 + 修复 100/50（fake usage），预扣后进入闭环
    assert final["generate"]["usage"] == {"prompt_tokens": 100,
                                          "completion_tokens": 50}
    assert final["token_total"] == {"prompt_tokens": 100,
                                    "completion_tokens": 50}
    session = report_path.parent
    assert (session / "events.json").is_file()
    assert (session / "generation" / "events.json").is_file()
    events = json.loads((session / "events.json").read_text(encoding="utf-8"))
    assert len(events) == 2 and all(e["ok"] for e in events)
    assert (session / "rounds" / "round-1.json").is_file()
    assert (session / "rounds" / "round-2.json").is_file()
    # 修复后的文件确实落盘且正确
    assert "return a + b" in (session / "workspace" / "main.py").read_text(encoding="utf-8")
