"""llm 统一客户端离线测试：宽松 JSON 解析、length 重试、事件记录、配置加载。"""

import pytest

from autoforge.llm import LLMClient, load_config, parse_json_loose


# ---- 测试替身：duck-typed OpenAI transport ----

class FakeMessage:
    def __init__(self, content, reasoning=""):
        self.content = content
        self.reasoning_content = reasoning


class FakeChoice:
    def __init__(self, content, finish, reasoning=""):
        self.message = FakeMessage(content, reasoning)
        self.finish_reason = finish


class FakeUsage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


class FakeResponse:
    def __init__(self, content, finish="stop", p=100, c=50, reasoning=""):
        self.choices = [FakeChoice(content, finish, reasoning)]
        self.usage = FakeUsage(p, c)


class FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        if not self._responses:
            raise AssertionError("FakeTransport 剧本耗尽：管线发起了未预期的额外 LLM 调用")
        return self._responses.pop(0)


class FakeTransport:
    def __init__(self, responses):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(responses)


def make_client(responses):
    return LLMClient(api_key="k", base_url="http://x", model="m",
                     transport=FakeTransport(responses))


# ---- parse_json_loose ----

def test_parse_plain():
    assert parse_json_loose('{"a": 1}') == {"a": 1}


def test_parse_fenced():
    assert parse_json_loose("废话\n```json\n{\"a\": 1}\n```\n结尾") == {"a": 1}


def test_parse_bare_list():
    assert parse_json_loose('[{"path": "a.py"}]') == [{"path": "a.py"}]


# ---- chat_json ----

def test_chat_json_ok_and_event():
    c = make_client([FakeResponse('{"files": []}')])
    obj, usage = c.chat_json([{"role": "user", "content": "hi"}])
    assert obj == {"files": []}
    assert usage == {"prompt_tokens": 100, "completion_tokens": 50}
    assert c.events[-1]["ok"] is True


def test_chat_json_length_retry_doubles_budget():
    c = make_client([
        FakeResponse('{"files": [{"path": "a.py", "con', finish="length"),
        FakeResponse('{"files": []}'),
    ])
    obj, usage = c.chat_json([{"role": "user", "content": "hi"}], max_tokens=1000)
    assert obj == {"files": []}
    calls = c._transport.chat.completions.calls
    assert calls[0]["max_tokens"] == 1000
    assert calls[1]["max_tokens"] == 2000
    assert c.events[0]["ok"] is False and c.events[0]["retry"] == "length_parse"
    assert usage == {"prompt_tokens": 200, "completion_tokens": 100}
    assert c.events[1]["ok"] is True


def test_chat_json_empty_content_with_reasoning_retries_then_raises():
    c = make_client([
        FakeResponse("", finish="length", reasoning="x" * 10),
        FakeResponse("", finish="stop"),
    ])
    with pytest.raises(ValueError, match="空 content"):
        c.chat_json([{"role": "user", "content": "hi"}])
    assert len(c.events) == 2
    assert c.events[-1]["error"] == "empty"


def test_chat_json_bad_json_raises():
    c = make_client([FakeResponse("not json at all")])
    with pytest.raises(ValueError, match="解析 LLM JSON 失败"):
        c.chat_json([{"role": "user", "content": "hi"}])
    assert c.events[-1]["error"] == "parse"


# ---- load_config ----

def test_load_config_from_path_and_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("llm:\n  api_key: k1\n  base_url: http://b\n  model: m1\n",
                        encoding="utf-8")
    assert load_config(cfg_file)["model"] == "m1"
    monkeypatch.setenv("AUTOFORGE_CONFIG", str(cfg_file))
    assert load_config()["api_key"] == "k1"
    assert load_config(cfg_file, model_override="m2")["model"] == "m2"


def test_load_config_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")
