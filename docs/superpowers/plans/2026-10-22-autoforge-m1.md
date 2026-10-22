# autoforge M1 实施计划（骨架 + oneshot 端到端）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建全新 autoforge 仓库，迁移沙箱/闭环核心资产，自研 oneshot 生成器，`autoforge run` 一条命令端到端可用（生成 → 沙箱判卷 → 修复 → 报告）。

**Architecture:** src-layout 单包 `autoforge`，七个模块中 M1 落地六个（cli/llm/schemas/generator/sandbox/loop）；统一 LLM 客户端收口所有模型调用（可注入 fake transport 做离线测试）；会话产物落 `runs/<ts>/`。

**Tech Stack:** Python 3.11、openai、docker、pydantic v2、pyyaml、pytest。

**旧仓库资产位置（迁移源，只读参考）：** `/Users/lulinran/Desktop/meta/autoforge/`

**约定：** 所有命令在 `/Users/lulinran/Desktop/autoforge/` 仓库根执行；Python 一律用 `.venv/bin/python`；提交信息末尾带 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

---

## 文件结构（M1 结束时的样子）

```
autoforge/
├── pyproject.toml              # Task 1
├── .gitignore                  # Task 1
├── README.md                   # Task 9
├── config.example.yaml         # Task 9
├── src/autoforge/
│   ├── __init__.py             # Task 1
│   ├── schemas.py              # Task 2（旧 schemas + 旧 llm_fixer 的 FilePatch/PatchList 收编）
│   ├── llm.py                  # Task 3（新写：统一客户端 + 宽松 JSON 解析 + 配置）
│   ├── sandbox.py              # Task 4（旧 executor_sandbox 平移）
│   ├── generator.py            # Task 5（新写：oneshot 策略）
│   ├── loop.py                 # Task 6（旧 auto_fix_loop/llm_fixer 改造迁移）
│   └── cli.py                  # Task 7（旧 autoforge.py 改造迁移）
└── tests/
    ├── conftest.py             # Task 1
    ├── test_schemas.py         # Task 2
    ├── test_llm.py             # Task 3
    ├── test_sandbox_parse.py   # Task 4
    ├── test_generator.py       # Task 5
    ├── test_loop.py            # Task 6
    ├── test_cli.py             # Task 7
    └── test_e2e_fake.py        # Task 8（docker-gated）
```

---

### Task 1: 仓库骨架与可安装包

**Files:**
- Create: `pyproject.toml`、`.gitignore`、`src/autoforge/__init__.py`、`tests/conftest.py`

- [ ] **Step 1: 创建 venv 与目录**

```bash
cd /Users/lulinran/Desktop/autoforge
python3.11 -m venv .venv
mkdir -p src/autoforge tests
```

- [ ] **Step 2: 写 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "autoforge"
version = "0.1.0"
description = "一句话需求 → 生成 → 禁网沙箱验收 → LLM 修复闭环"
requires-python = ">=3.11"
dependencies = [
    "openai>=1.40",
    "docker>=7",
    "pydantic>=2.7",
    "pyyaml>=6",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
autoforge = "autoforge.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 3: 写 .gitignore**

```
__pycache__/
*.pyc
.pytest_cache/
.venv/
.wheels/
runs/
eval_results/
```

- [ ] **Step 4: 写 src/autoforge/__init__.py**

```python
"""autoforge — 一句话需求 → 生成 → 禁网沙箱验收 → LLM 修复闭环。"""

__version__ = "0.1.0"
```

- [ ] **Step 5: 写 tests/conftest.py 与 tests/__init__.py（src 路径注入，测试不依赖安装方式）**

`tests/conftest.py`：

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

`tests/__init__.py`：空文件（使 `from tests.test_llm import ...` 跨文件复用测试替身成立）。

- [ ] **Step 6: 安装并验证**

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -c "import autoforge; print(autoforge.__version__)"
```

Expected: 输出 `0.1.0`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore src/autoforge/__init__.py tests/conftest.py tests/__init__.py
git commit -m "feat: 仓库骨架——src layout + 可安装包 + console_script

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: schemas 迁移（数据契约收编）

**Files:**
- Create: `src/autoforge/schemas.py`
- Test: `tests/test_schemas.py`

- [ ] **Step 1: 迁移测试（先写测试）**

`tests/test_schemas.py` 内容 = 旧仓库 `tests/test_schemas.py` 全文 + 旧 `tests/test_llm_fixer.py` 中路径安全相关三个用例收编，合并后如下：

```python
"""schemas 契约测试：模型构造、路径安全、TestReport 语义（全离线）。"""

import pytest
from pydantic import ValidationError

from autoforge.schemas import Failure, FilePatch, PatchList, TestReport


def test_failure_defaults():
    f = Failure(test_name="t")
    assert f.error_type == "Unknown" and f.message == "" and f.traceback == ""


def test_test_report_all_passed():
    r = TestReport(passed=3, total=3, failed=0)
    assert r.all_passed
    r2 = TestReport(passed=0, total=2, failed=2)
    assert not r2.all_passed  # passed==0 时不算全绿


def test_patch_path_normalization():
    p = FilePatch(path="./a//b.py", content="x")
    assert p.path == "a/b.py"


def test_reject_unsafe_paths():
    for bad in ("/etc/passwd", "../evil.py", "a/../../b.py", "", "."):
        with pytest.raises(ValidationError):
            FilePatch(path=bad, content="x")


def test_patch_list_accepts_files():
    pl = PatchList(files=[FilePatch(path="m.py", content="y = 1")])
    assert pl.files[0].content == "y = 1"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_schemas.py -v
```

Expected: FAIL（`ModuleNotFoundError: No module named 'autoforge.schemas'`）

- [ ] **Step 3: 迁移实现**

`src/autoforge/schemas.py`：把旧仓库 `schemas.py` 全文复制过来，再把旧 `llm_fixer.py` 里的 `FilePatch`/`PatchList`（含 `_clean_path` validator）搬进来，合并为：

```python
"""schemas.py — 系统各环节之间的数据契约（pydantic）。

TestReport/Failure/FixRequest：沙箱判卷结果与修复请求；
FilePatch/PatchList：LLM 提案的标准文件补丁格式（生成与修复共用同一原语）。
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class Failure(BaseModel):
    """单个失败用例。"""

    test_name: str
    error_type: str = "Unknown"
    message: str = ""
    traceback: str = ""


class TestReport(BaseModel):
    """沙箱跑完一轮测试后的完整报告。"""

    passed: int
    total: int
    failed: int
    failures: List[Failure] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    exit_code: Optional[int] = None
    timed_out: bool = False

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.passed > 0


class FixRequest(BaseModel):
    """把失败信息打包回传给 LLM 的修复请求。"""

    target_files: List[str]
    failure_summary: str
    suggested_fix: str = ""
    retry_round: int = 0


class FilePatch(BaseModel):
    """LLM 返回的单文件补丁（生成与修复共用）。"""

    path: str
    content: str

    @field_validator("path")
    @classmethod
    def _clean_path(cls, v: str) -> str:
        p = v.strip().replace("\\", "/")
        parts = [seg for seg in p.split("/") if seg not in ("", ".")]
        if not parts or p.startswith("/") or ".." in parts:
            raise ValueError(f"必须是安全的仓库相对路径: {v!r}")
        return "/".join(parts)


class PatchList(BaseModel):
    files: list[FilePatch]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_schemas.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoforge/schemas.py tests/test_schemas.py
git commit -m "feat: schemas 数据契约——旧 schemas + FilePatch/PatchList 收编为统一补丁原语

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: llm.py 统一客户端

**Files:**
- Create: `src/autoforge/llm.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1: 写失败测试**

`tests/test_llm.py`：

```python
"""llm 统一客户端离线测试：宽松 JSON 解析、length 重试、事件记录、配置加载。"""

import json

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
    obj, _ = c.chat_json([{"role": "user", "content": "hi"}], max_tokens=1000)
    assert obj == {"files": []}
    calls = c._transport.chat.completions.calls
    assert calls[0]["max_tokens"] == 1000
    assert calls[1]["max_tokens"] == 2000
    assert c.events[0]["ok"] is False and c.events[0]["retry"] == "length_parse"


def test_chat_json_empty_content_with_reasoning_retries_then_raises():
    c = make_client([
        FakeResponse("", finish="length", reasoning="x" * 10),
        FakeResponse("", finish="stop"),
    ])
    with pytest.raises(ValueError, match="空 content"):
        c.chat_json([{"role": "user", "content": "hi"}])
    assert len(c.events) == 2


def test_chat_json_bad_json_raises():
    c = make_client([FakeResponse("not json at all")])
    with pytest.raises(ValueError, match="解析 LLM JSON 失败"):
        c.chat_json([{"role": "user", "content": "hi"}])


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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_llm.py -v
```

Expected: FAIL（`No module named 'autoforge.llm`）

- [ ] **Step 3: 实现 src/autoforge/llm.py**

```python
"""llm.py — 全系统唯一的 LLM 调用入口（OpenAI 兼容方言）。

设计要点：
  - 所有模型调用（生成/修复/评测 judge）都走 chat_json，可靠性指标的数据源
    （self.events：每次尝试一条 {ok, retry, finish, tokens}）自动产生；
  - 宽松 JSON 解析：容忍 ```json 围栏与裸数组；
  - finish_reason=length（推理模型烧尽预算）→ max_tokens 翻倍重试一次；
  - transport 可注入，离线测试不发真实请求。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
DEFAULT_CONFIG_PATH = Path.home() / ".autoforge" / "config.yaml"


def parse_json_loose(text: str) -> Any:
    """容忍围栏的 JSON 解析；裸数组原样返回 list。"""
    m = _FENCE_RE.search(text)
    raw = m.group(1).strip() if m else text.strip()
    return json.loads(raw)


def load_config(config_path: str | Path | None = None,
                model_override: str | None = None) -> dict:
    """读 ~/.autoforge/config.yaml（AUTOFORGE_CONFIG 环境变量可覆盖路径）。

    返回 {api_key, base_url, model}；model_override 优先于配置文件。
    """
    path = Path(config_path or os.environ.get("AUTOFORGE_CONFIG") or DEFAULT_CONFIG_PATH)
    if not path.is_file():
        raise FileNotFoundError(f"配置不存在: {path}（参考仓库 config.example.yaml）")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    llm = cfg["llm"]
    llm["model"] = model_override or llm["model"]
    return llm


class LLMClient:
    """OpenAI 兼容端点上的结构化输出客户端。"""

    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout: int = 300, max_tokens: int = 16384, transport=None):
        if transport is None:
            from openai import OpenAI  # 延迟导入，离线测试不受影响
            transport = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._transport = transport
        self.model = model
        self.max_tokens = max_tokens
        self.events: list[dict] = []

    def chat_json(self, messages: list[dict], max_tokens: int | None = None
                  ) -> tuple[Any, dict]:
        """一次结构化调用，返回 (解析后的对象, usage)。

        每次尝试往 self.events 追加一条；length 截断自动翻倍预算重试一次。
        """
        budget = max_tokens or self.max_tokens
        for attempt in (1, 2):
            resp = self._transport.chat.completions.create(
                model=self.model, messages=messages,
                temperature=0.0, max_tokens=budget)
            choice = resp.choices[0]
            finish = choice.finish_reason
            text = choice.message.content or ""
            usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
                     "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0}
            ev = {"ok": False, "attempt": attempt, "finish": finish, **usage}

            if text.strip():
                try:
                    obj = parse_json_loose(text)
                except json.JSONDecodeError as exc:
                    if attempt == 1 and finish == "length":
                        ev["retry"] = "length_parse"
                        self.events.append(ev)
                        budget *= 2
                        continue
                    self.events.append(ev)
                    raise ValueError(
                        f"解析 LLM JSON 失败: {exc}\n输出开头 200 字符: {text[:200]!r}")
                ev["ok"] = True
                self.events.append(ev)
                return obj, usage

            reasoning_len = len(getattr(choice.message, "reasoning_content", "") or "")
            if attempt == 1 and finish == "length":
                ev["retry"] = "length_empty"
                self.events.append(ev)
                budget *= 2
                continue
            self.events.append(ev)
            raise ValueError(
                f"LLM 返回空 content: finish_reason={finish}, "
                f"reasoning_len={reasoning_len}。可换非推理档模型或加大 max_tokens。")
        raise ValueError("LLM 返回内容始终不完整（重试后仍为空/截断）")
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_llm.py -v
```

Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoforge/llm.py tests/test_llm.py
git commit -m "feat: llm 统一客户端——宽松 JSON、length 翻倍重试、事件记录、可注入 transport

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: sandbox 迁移（Docker 沙箱执行器）

**Files:**
- Create: `src/autoforge/sandbox.py`
- Test: `tests/test_sandbox_parse.py`

- [ ] **Step 1: 迁移测试**

`tests/test_sandbox_parse.py` = 旧仓库 `tests/test_sandbox_parse.py` 全文复制，仅改 import：

```python
from autoforge.sandbox import build_report, parse_failures
```

（`CANNED_ERRORS_STDOUT` 常量与两个用例原样保留——它是 2026-10-15 生产事故的回归样本。）

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_sandbox_parse.py -v
```

Expected: FAIL（`No module named 'autoforge.sandbox`）

- [ ] **Step 3: 迁移实现**

`src/autoforge/sandbox.py`：旧仓库 `executor_sandbox.py` 全文复制，应用以下精确修改（其余逐行保留，含 `main()` 调试入口）：

1. 模块 docstring 首行改为 `"""sandbox.py — 独立的 Docker 沙箱执行器（禁网判卷）。"""`（其余说明保留）
2. `REPO_ROOT = Path(__file__).resolve().parent` 改为：
   ```python
   REPO_ROOT = Path(__file__).resolve().parents[2]  # src/autoforge/sandbox.py → 仓库根
   ```
3. 文件末尾 `if __name__ == "__main__": sys.exit(main())` 保留（`python -m autoforge.sandbox` 可单独调试）

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_sandbox_parse.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoforge/sandbox.py tests/test_sandbox_parse.py
git commit -m "feat: sandbox 迁移——禁网容器判卷（只读挂载/离线 pytest/超时强杀/双区段解析）

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: generator（oneshot 策略）

**Files:**
- Create: `src/autoforge/generator.py`
- Test: `tests/test_generator.py`

- [ ] **Step 1: 写失败测试**

`tests/test_generator.py`：

```python
"""generator 离线测试：prompt 约束注入、oneshot 解析与容错。"""

import pytest

from autoforge.generator import GENERATION_SYSTEM_PROMPT, generate_oneshot
from tests.test_llm import FakeResponse, make_client


def test_system_prompt_states_hard_constraints():
    for kw in ("标准库", "pytest", "--help", "完整"):
        assert kw in GENERATION_SYSTEM_PROMPT


def test_generate_oneshot_returns_files_dict():
    payload = {"files": [
        {"path": "calc/main.py", "content": "def add(a, b):\n    return a + b\n"},
        {"path": "calc/test_main.py", "content": "from calc.main import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"},
    ]}
    import json
    c = make_client([FakeResponse(json.dumps(payload))])
    r = generate_oneshot("一个加法 CLI", c)
    assert r.strategy == "oneshot" and r.degraded is False
    assert set(r.files) == {"calc/main.py", "calc/test_main.py"}
    assert "def add" in r.files["calc/main.py"]
    assert r.usage["completion_tokens"] == 50


def test_generate_oneshot_accepts_bare_list():
    import json
    c = make_client([FakeResponse(json.dumps([{"path": "a.py", "content": "x = 1"}]))])
    r = generate_oneshot("idea", c)
    assert r.files == {"a.py": "x = 1"}


def test_generate_oneshot_unsafe_path_rejected():
    import json
    c = make_client([FakeResponse(json.dumps({"files": [{"path": "../evil.py", "content": "x"}]}))])
    with pytest.raises(Exception, match="安全的仓库相对路径"):
        generate_oneshot("idea", c)


def test_generate_oneshot_missing_files_key():
    c = make_client([FakeResponse('{"nope": 1}')])
    with pytest.raises(ValueError, match="files"):
        generate_oneshot("idea", c)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_generator.py -v
```

Expected: FAIL（`No module named 'autoforge.generator`）

- [ ] **Step 3: 实现 src/autoforge/generator.py**

```python
"""generator.py — 项目生成器（M1 仅 oneshot 策略；M2 增加 plan_test_code）。

生成与修复是同一原语：上下文 → {"files": [{path, content}]}。
生成即第 0 轮提案，后续硬化交给判卷-修复闭环。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from autoforge.llm import LLMClient
from autoforge.schemas import FilePatch, PatchList

GENERATION_SYSTEM_PROMPT = """你是资深 Python 工程师，从零构建一个小型 Python CLI 工具。
只输出严格 JSON（不带 markdown 围栏、不带任何解释文字），格式：
{"files": [{"path": "相对路径", "content": "完整文件内容"}]}
硬性要求：
1. 仅使用 Python 标准库。运行环境禁网、无法安装第三方包；测试框架只用 pytest。
2. 必须包含 pytest 测试文件（test_*.py），每个核心行为至少一个用例；测试不得联网、不得读写项目外路径。
3. 入口模块必须支持 `python <入口> --help` 且正常退出（退出码 0）。
4. content 必须是该文件的完整最终内容（可直接运行），禁止省略、占位符或"其余不变"。
5. 只输出本项目需要的文件，遵守合理的模块划分。
"""


class GenerationResult(BaseModel):
    """一次生成的产物与账目。"""

    files: dict[str, str]
    strategy: str
    usage: dict
    degraded: bool = False  # M2：plan 失败降级 oneshot 时置 True


def _coerce_patches(obj: Any) -> PatchList:
    files = obj["files"] if isinstance(obj, dict) else obj
    if not isinstance(files, list):
        raise ValueError(f"生成输出缺少可用的 files 列表: {str(obj)[:200]}")
    return PatchList(files=[FilePatch(**f) for f in files])


def generate_oneshot(idea: str, client: LLMClient) -> GenerationResult:
    """单发策略：一次调用产出全部文件（含测试）。"""
    messages = [
        {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": f"# 需求\n{idea}\n\n请输出完整项目 JSON。"},
    ]
    obj, usage = client.chat_json(messages)
    patches = _coerce_patches(obj)
    return GenerationResult(
        files={p.path: p.content for p in patches.files},
        strategy="oneshot", usage=usage)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_generator.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoforge/generator.py tests/test_generator.py
git commit -m "feat: oneshot 生成器——标准库约束 + 测试即规格的提案原语

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: loop 迁移（判卷-修复闭环）

**Files:**
- Create: `src/autoforge/loop.py`
- Test: `tests/test_loop.py`

- [ ] **Step 1: 写失败测试**

`tests/test_loop.py`（judge 用 monkeypatch 替身，全程离线）：

```python
"""loop 离线测试：冒烟映射、上下文裁剪、闭环状态机（judge 打桩）。"""

from pathlib import Path

from autoforge.loop import LLMFixer, build_tree, collect_context, run_loop_structured, smoke_failure
from autoforge.schemas import Failure, FilePatch, PatchList, TestReport


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


def test_build_tree_sorted(tmp_path):
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    assert build_tree(tmp_path).splitlines() == ["a.py", "b.py"]


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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_loop.py -v
```

Expected: FAIL（`No module named 'autoforge.loop`）

- [ ] **Step 3: 实现 src/autoforge/loop.py**

结构 = 旧仓库 `auto_fix_loop.py` + `llm_fixer.py` 合并改造。**从旧文件逐段搬运并按下述修改**：

从旧 `auto_fix_loop.py` 原样搬运（改 import 来源为 `autoforge.*`）：`MAX_CONTEXT_LINES/MAX_CONTEXT_FILES` 常量、`_norm_path`、`build_tree`、`collect_context`、`smoke_failure`、`judge`、`save_round_report`、`FixRequest` 组装逻辑。

从旧 `llm_fixer.py` 搬运：`FIX_SYSTEM_PROMPT`（旧名 SYSTEM_PROMPT，改名）、`apply_patches`。

新写/改写的部分如下（完整代码）：

```python
"""loop.py — 判卷-修复闭环（生成即第 0 轮提案，本模块负责后续所有轮）。

每轮：沙箱判卷（pytest+入口冒烟）→ 结构化失败 → LLM 补丁 → 守卫 → 复验。
会话产物落 <session_dir>/reports/round-N.json。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from autoforge.llm import LLMClient
from autoforge.schemas import Failure, FilePatch, FixRequest, PatchList, TestReport
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
        files = obj["files"] if isinstance(obj, dict) else obj
        if not isinstance(files, list):
            raise ValueError(f"修复输出缺少可用的 files 列表: {str(obj)[:200]}")
        return PatchList(files=[FilePatch(**f) for f in files]), usage


def run_loop_structured(session_dir: Path, workdir: Path, entry_cmd: str | None,
                        max_rounds: int, fixer: LLMFixer, budget_tokens: int,
                        timeout: int = 120, verbose: bool = True) -> dict:
    """判卷+修复闭环。返回 {status, rounds, token_total}。

    session_dir：会话根（reports/ 与最终报告落此处）；
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
```

从旧 `auto_fix_loop.py` 搬运时的精确差异清单：
- `judge()`、`save_round_report()`：逻辑原样，import 改 `autoforge.*`；`save_round_report` 落盘目录从旧 `session / "reports"` 改为 `session_dir / "rounds"`（与 spec §6 会话目录结构对齐），参数名统一 `session_dir`
- `smoke_failure`/`collect_context`/`build_tree`/`_norm_path`：逐行原样
- `apply_patches`：从旧 `llm_fixer.py:60-68` 原样搬运（写 workdir 副本，原产物永不触碰的语义由调用方保证）
- **删除**：`copy_session`、`load_llm_config`、`run_loop` CLI 包装、`main()`（这些职责移入 cli.py / llm.py）

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_loop.py -v
```

Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoforge/loop.py tests/test_loop.py
git commit -m "feat: 判卷-修复闭环迁移——LLMFixer 收口统一客户端，会话/工作目录分离

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: cli（run / judge / fix 子命令）

**Files:**
- Create: `src/autoforge/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

`tests/test_cli.py`：

```python
"""cli 离线测试：入口探测、报告汇总格式。判卷相关走 Task 8 的 docker-gated e2e。"""

import json

from autoforge.cli import create_run_dir, discover_entry, human_summary


def test_discover_entry_main_py(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "main.py").write_text("", encoding="utf-8")
    assert discover_entry(tmp_path) == "python pkg/main.py --help"


def test_discover_entry_none(tmp_path):
    assert discover_entry(tmp_path) is None


def test_create_run_dir_under_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # runs/ 建在当前目录（仓库根）
    rd = create_run_dir()
    assert rd.parent.name == "runs" and rd.is_dir()
    assert (rd / "workspace").is_dir()


def test_human_summary_contains_core_lines(tmp_path):
    final = {
        "idea": "demo", "model": "m",
        "generate": {"duration_s": 12.3, "exit_code": 0, "project_dir": "x"},
        "entry_cmd": "python main.py --help",
        "status": "all_green", "session_dir": str(tmp_path),
        "rounds": [{"round": 1, "pytest_failed": 0, "pytest_passed": 3,
                    "smoke_exit": 0, "applied_patches": []}],
        "token_total": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    s = human_summary(final)
    assert "all_green" in s and "3P" in s and "autoforge_report.json" in s
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: FAIL（`No module named 'autoforge.cli`）

- [ ] **Step 3: 实现 src/autoforge/cli.py**

```python
"""cli.py — autoforge 命令行入口。

子命令：
  run "<idea>"    全流程：生成(oneshot) → 判卷 → 修复 → 报告
  judge <dir>     只判卷（沙箱 pytest + 入口冒烟），不调修复 LLM
  fix <dir>       对任意已有项目判卷+修复（闭环 generator-agnostic 的正统入口）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from autoforge.generator import generate_oneshot
from autoforge.llm import LLMClient, load_config
from autoforge.loop import LLMFixer, apply_patches, run_loop_structured
from autoforge.sandbox import print_report, run_pytest_in_sandbox
from autoforge.schemas import FilePatch, PatchList

RUNS_ROOT = Path("runs")  # 相对当前目录；由 create_run_dir 建在仓库根

STATUS_EXIT = {"all_green": 0, "max_rounds_exhausted": 1, "budget_exhausted": 4,
               "fixer_error": 3, "generation_failed": 5}


def create_run_dir() -> Path:
    """新建 runs/<时间戳>/ 会话目录（含 workspace/）。"""
    session = RUNS_ROOT / time.strftime("%Y%m%d-%H%M%S")
    (session / "workspace").mkdir(parents=True, exist_ok=True)
    return session


def discover_entry(project_dir: Path) -> str | None:
    """自动探测入口：优先 main.py / cli.py，用 `python <入口> --help` 冒烟。"""
    for pat in ("*/main.py", "main.py", "*/cli.py", "*/__main__.py"):
        hits = sorted(p for p in project_dir.glob(pat) if "__pycache__" not in p.parts)
        if hits:
            rel = hits[0].relative_to(project_dir).as_posix()
            return f"python {rel} --help"
    return None


def human_summary(final: dict) -> str:
    lines = ["", "=" * 62, "autoforge 报告", "=" * 62]
    gen = final.get("generate")
    if gen:
        lines.append(f"生成: {gen['duration_s']}s | 策略 {gen.get('strategy')} | "
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
        if r["pytest_failed"] == 0 and r["smoke_exit"] == 0:
            line += " ✅"
        lines.append(line)
    lines.append(f"会话: {final['session_dir']}")
    lines.append(f"报告: {Path(final['session_dir']) / 'autoforge_report.json'}")
    return "\n".join(lines)


def _write_report(session_dir: Path, final: dict) -> None:
    (session_dir / "autoforge_report.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_run(args) -> int:
    cfg = load_config(args.config, args.model)
    client = LLMClient(api_key=cfg["api_key"], base_url=cfg["base_url"], model=cfg["model"])
    print(f"[autoforge] 模型: {cfg['model']} @ {cfg['base_url']}")
    session_dir = create_run_dir()
    workspace = session_dir / "workspace"
    print(f"[autoforge] 会话目录: {session_dir}")

    start = time.time()
    try:
        gen = generate_oneshot(args.idea, client)
    except Exception as exc:
        print(f"[autoforge] ❌ 生成失败: {exc}", file=sys.stderr)
        final = {"idea": args.idea, "model": cfg["model"], "generate": {"error": str(exc)},
                 "entry_cmd": None, "status": "generation_failed",
                 "session_dir": str(session_dir), "rounds": [], "token_total": {}}
        _write_report(session_dir, final)
        return STATUS_EXIT["generation_failed"]
    gen_events = list(client.events)
    (session_dir / "generation").mkdir(exist_ok=True)
    (session_dir / "generation" / "events.json").write_text(
        json.dumps(gen_events, ensure_ascii=False, indent=2), encoding="utf-8")
    apply_patches(workspace, PatchList(
        files=[FilePatch(path=p, content=c) for p, c in gen.files.items()]))
    gen_summary = {"strategy": gen.strategy, "duration_s": round(time.time() - start, 1),
                   "usage": gen.usage, "degraded": gen.degraded}
    print(f"[autoforge] 生成 {len(gen.files)} 个文件: {sorted(gen.files)}")

    entry = args.entry or discover_entry(workspace)
    print(f"[autoforge] 冒烟命令: {entry or '（未探测到入口，跳过冒烟）'}")

    gen_tokens = gen.usage.get("prompt_tokens", 0) + gen.usage.get("completion_tokens", 0)
    remaining = max(args.budget_tokens - gen_tokens, 0)
    result = run_loop_structured(session_dir, workspace, entry, args.max_rounds,
                                 LLMFixer(client), budget_tokens=remaining, timeout=args.timeout)
    final = {"idea": args.idea, "model": cfg["model"], "generate": gen_summary,
             "entry_cmd": entry, **result, "session_dir": str(session_dir)}
    _write_report(session_dir, final)
    print(human_summary(final))
    return STATUS_EXIT.get(final["status"], 1)


def cmd_judge(args) -> int:
    target = Path(args.dir).resolve()
    if not target.is_dir():
        print(f"[autoforge] 目录不存在: {target}", file=sys.stderr)
        return 2
    entry = args.entry or discover_entry(target)
    report = run_pytest_in_sandbox(target, timeout=args.timeout)
    print_report(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(f"[autoforge] 冒烟命令: {entry or '（未探测到，跳过）'}")
    return 1 if report["timed_out"] else 0


def cmd_fix(args) -> int:
    artifact = Path(args.dir).resolve()
    if not artifact.is_dir():
        print(f"[autoforge] 目录不存在: {artifact}", file=sys.stderr)
        return 2
    cfg = load_config(args.config, args.model)
    client = LLMClient(api_key=cfg["api_key"], base_url=cfg["base_url"], model=cfg["model"])
    session_dir = create_run_dir()
    workspace = session_dir / "workspace"
    shutil.copytree(artifact, workspace,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    print(f"[autoforge] 会话目录（可写副本）: {session_dir}\n[autoforge] 原目录保持只读: {artifact}")
    entry = args.entry or discover_entry(workspace)
    result = run_loop_structured(session_dir, workspace, entry, args.max_rounds,
                                 LLMFixer(client), budget_tokens=args.budget_tokens,
                                 timeout=args.timeout)
    final = {"idea": None, "model": cfg["model"], "generate": None,
             "entry_cmd": entry, **result, "session_dir": str(session_dir)}
    _write_report(session_dir, final)
    print(human_summary(final))
    return STATUS_EXIT.get(final["status"], 1)


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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: 4 passed

- [ ] **Step 5: 全量测试回归**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: 36 passed（schemas 5 + llm 9 + sandbox 2 + generator 5 + loop 11 + cli 4）

- [ ] **Step 6: Commit**

```bash
git add src/autoforge/cli.py tests/test_cli.py
git commit -m "feat: cli——run/judge/fix 子命令，全链路预算闸门（生成段预扣）

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: 端到端冒烟（fake LLM + 真沙箱，docker-gated）

**Files:**
- Test: `tests/test_e2e_fake.py`

- [ ] **Step 1: 写 e2e 测试**

`tests/test_e2e_fake.py`：

```python
"""端到端冒烟：fake transport 剧本 + 真实 Docker 沙箱。

剧本：生成一个带 bug 的项目（1 个失败测试）→ 第 1 轮判卷失败 →
fixer 返回修正文件 → 第 2 轮全绿。验证 cli.run 全链路（含报告落盘）。
"""

import json
import shutil

import pytest

from autoforge import cli
from tests.test_llm import FakeResponse, FakeTransport

docker_available = shutil.which("docker") is not None

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
    monkeypatch.setattr(cli, "LLMClient", lambda **k: CfgClient())

    code = cli.main(["run", "一个加法 CLI", "--max-rounds", "2",
                     "--budget-tokens", "100000"])
    assert code == 0

    report_path = sorted((tmp_path / "runs").glob("*/autoforge_report.json"))[-1]
    final = json.loads(report_path.read_text(encoding="utf-8"))
    assert final["status"] == "all_green"
    assert len(final["rounds"]) == 2
    assert final["rounds"][0]["pytest_failed"] >= 1
    assert final["rounds"][1]["pytest_failed"] == 0
    assert final["rounds"][0]["applied_patches"] == ["main.py"]
    assert final["generate"]["strategy"] == "oneshot"
```

- [ ] **Step 2: 跑测试（需 Docker Desktop 运行中）**

```bash
.venv/bin/python -m pytest tests/test_e2e_fake.py -v
```

Expected: 1 passed（Docker 未运行则 SKIPPED——此时先 `open -a Docker` 再跑；首次运行会下载 pytest wheels 到 `.wheels/` 与拉取 `python:3.11-slim` 镜像）

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_fake.py
git commit -m "test: 端到端冒烟——fake LLM 剧本走通 run 全链路（真沙箱）

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: README 与配置样例

**Files:**
- Create: `README.md`、`config.example.yaml`

- [ ] **Step 1: 写 config.example.yaml**

```yaml
# 复制到 ~/.autoforge/config.yaml（或设 AUTOFORGE_CONFIG 指向本文件）
llm:
  api_key: sk-xxxx
  base_url: https://your-openai-compatible-relay.example/v1
  model: deepseek-v4-flash
```

- [ ] **Step 2: 写 README.md**

````markdown
# autoforge

> 一句话需求 → 生成 → 禁网沙箱验收 → LLM 修复闭环。

**设计哲学：不做更强的生成，做更硬的验证。** LLM 生成的代码不可信，
所以确定性放在验证侧——每个产物都要在禁网 Docker 沙箱里过
pytest + 入口冒烟，失败结构化回传 LLM 迭代修复，直到全绿或预算耗尽。

```
需求 ─[生成]→ 项目文件 ─→ 禁网沙箱判卷（pytest + 入口冒烟）
                         ─→ 失败？→ 结构化失败报告 → LLM 补丁 → 复验 ─┐
                         └──────────────────────────────────────────────┘
```

生成与修复是同一原语（上下文 → `{"files": [...]}`），生成即第 0 轮提案。

## 安装

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp config.example.yaml ~/.autoforge/config.yaml  # 填入你的 OpenAI 兼容端点
```

沙箱依赖 Docker；首次运行会自动下载 pytest wheels 与 python:3.11-slim 镜像。

## 使用

```bash
autoforge run "一个记账 CLI，支持 add/list/summary 子命令"   # 全流程
autoforge judge ./some_project                              # 只判卷
autoforge fix ./some_project                                # 判卷+修复（任意项目）
```

常用参数：`--max-rounds`（默认 3）、`--budget-tokens`（默认 300k，全链路）、
`--model`、`--timeout`（沙箱单命令秒数）。

会话产物落 `runs/<时间戳>/`：生成事件、每轮判卷与补丁记录、最终报告
`autoforge_report.json`。

## 测试

```bash
.venv/bin/python -m pytest tests/ -v     # 离线单测
.venv/bin/python -m pytest tests/test_e2e_fake.py -v   # 端到端（需 Docker）
```

## 路线

- [x] M1 骨架 + oneshot 生成 + 端到端闭环
- [ ] M2 三段流水生成（plan → tests → code）+ 测试完整性守卫 + 修复记忆
- [ ] M3 三层评测（可靠性 / 种子 bug 修复基准 / 端到端需求基准）

设计文档见 `docs/superpowers/specs/`。
````

- [ ] **Step 3: Commit**

```bash
git add README.md config.example.yaml
git commit -m "docs: README 与配置样例——定位、用法、路线图

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## 验收清单（M1 完成定义）

- [ ] `.venv/bin/python -m pytest tests/ -v` 全绿（Docker 运行时含 e2e）
- [ ] `autoforge run "..."` 真实模型端到端可跑（人工验证一次，产物在 runs/）
- [ ] `autoforge judge` / `autoforge fix` 可用
- [ ] 仓库内无任何旧框架痕迹（grep 验证：`grep -ri metagpt src/ tests/ README.md` 无命中）
- [ ] git log 每个 commit 可独立回放
