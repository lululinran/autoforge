"""generator 离线测试：prompt 约束注入、oneshot 解析与容错。"""

import json

import pytest

from autoforge.generator import GENERATION_SYSTEM_PROMPT, generate_oneshot
from tests.test_llm import FakeResponse, make_client


def test_system_prompt_states_hard_constraints():
    for kw in ("标准库", "pytest", "--help", "完整", "扁平", "3.11"):
        assert kw in GENERATION_SYSTEM_PROMPT


def test_generate_oneshot_returns_files_dict():
    payload = {"files": [
        {"path": "calc/main.py", "content": "def add(a, b):\n    return a + b\n"},
        {"path": "calc/test_main.py", "content": "from calc.main import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"},
    ]}
    c = make_client([FakeResponse(json.dumps(payload))])
    r = generate_oneshot("一个加法 CLI", c)
    assert r.strategy == "oneshot" and r.degraded is False
    assert set(r.files) == {"calc/main.py", "calc/test_main.py"}
    assert "def add" in r.files["calc/main.py"]
    assert r.usage["completion_tokens"] == 50


def test_generate_oneshot_accepts_bare_list():
    c = make_client([FakeResponse(json.dumps([{"path": "a.py", "content": "x = 1"}]))])
    r = generate_oneshot("idea", c)
    assert r.files == {"a.py": "x = 1"}


def test_generate_oneshot_unsafe_path_rejected():
    c = make_client([FakeResponse(json.dumps({"files": [{"path": "../evil.py", "content": "x"}]}))])
    with pytest.raises(ValueError, match="安全的仓库相对路径"):
        generate_oneshot("idea", c)


def test_generate_oneshot_missing_files_key():
    c = make_client([FakeResponse('{"nope": 1}')])
    with pytest.raises(ValueError, match="files"):
        generate_oneshot("idea", c)


def test_generate_oneshot_empty_files_rejected():
    c = make_client([FakeResponse(json.dumps({"files": []}))])
    with pytest.raises(ValueError, match="为空"):
        generate_oneshot("idea", c)


def test_generate_oneshot_non_dict_entry_rejected():
    c = make_client([FakeResponse(json.dumps({"files": ["main.py"]}))])
    with pytest.raises(ValueError, match="必须是对象"):
        generate_oneshot("idea", c)


def test_generate_oneshot_duplicate_paths_rejected():
    payload = {"files": [
        {"path": "main.py", "content": "x = 1"},
        {"path": "main.py", "content": "x = 2"},
    ]}
    c = make_client([FakeResponse(json.dumps(payload))])
    with pytest.raises(ValueError, match="重复路径"):
        generate_oneshot("idea", c)
