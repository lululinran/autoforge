"""llm.py — 全系统唯一的 LLM 调用入口（OpenAI 兼容方言）。

设计要点：
  - 所有模型调用（生成/修复/评测 judge）都走 chat_json，可靠性指标的数据源
    （self.events：每次尝试一条 {ok, retry/error, finish, tokens}）自动产生；
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
            transport = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                               max_retries=2)
        self._transport = transport
        self.model = model
        self.max_tokens = max_tokens
        self.events: list[dict] = []

    def chat_json(self, messages: list[dict], max_tokens: int | None = None
                  ) -> tuple[Any, dict]:
        """一次结构化调用，返回 (解析后的对象, 跨尝试累计 usage)。

        每次尝试往 self.events 追加一条（含该次尝试自己的 token 数与失败种类）；
        length 截断自动翻倍预算重试一次，重试烧掉的 token 计入返回的累计 usage。
        """
        budget = max_tokens or self.max_tokens
        total = {"prompt_tokens": 0, "completion_tokens": 0}
        for attempt in (1, 2):
            resp = self._transport.chat.completions.create(
                model=self.model, messages=messages,
                temperature=0.0, max_tokens=budget)
            choice = resp.choices[0]
            finish = choice.finish_reason
            text = choice.message.content or ""
            usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
                     "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0}
            total["prompt_tokens"] += usage["prompt_tokens"]
            total["completion_tokens"] += usage["completion_tokens"]
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
                    ev["error"] = "parse"
                    self.events.append(ev)
                    raise ValueError(
                        f"解析 LLM JSON 失败: {exc}\n输出开头 200 字符: {text[:200]!r}")
                ev["ok"] = True
                self.events.append(ev)
                return obj, total

            reasoning_len = len(getattr(choice.message, "reasoning_content", "") or "")
            if attempt == 1 and finish == "length":
                ev["retry"] = "length_empty"
                self.events.append(ev)
                budget *= 2
                continue
            ev["error"] = "empty"
            self.events.append(ev)
            raise ValueError(
                f"LLM 返回空 content: finish_reason={finish}, "
                f"reasoning_len={reasoning_len}。可换非推理档模型或加大 max_tokens。")
        raise ValueError("LLM 返回内容始终不完整（重试后仍为空/截断）")
