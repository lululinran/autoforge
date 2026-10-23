"""generator.py — 项目生成器（M1 仅 oneshot 策略；M2 增加 plan_test_code）。

生成与修复是同一原语：上下文 → {"files": [{path, content}]}。
生成即第 0 轮提案，后续硬化交给判卷-修复闭环。
"""

from __future__ import annotations

from pydantic import BaseModel

from autoforge.llm import LLMClient
from autoforge.schemas import coerce_patch_list

GENERATION_SYSTEM_PROMPT = """你是资深 Python 工程师，从零构建一个小型 Python CLI 工具。
只输出严格 JSON（不带 markdown 围栏、不带任何解释文字），格式：
{"files": [{"path": "相对路径", "content": "完整文件内容"}]}
硬性要求：
1. 仅使用 Python 标准库。运行环境禁网、无法安装第三方包；测试框架只用 pytest。目标 Python 版本 3.11（沙箱运行环境）。
2. 必须包含 pytest 测试文件（test_*.py），每个核心行为至少一个用例；测试不得联网、不得读写项目外路径。
3. 入口模块必须支持 `python <入口> --help` 且正常退出（退出码 0）。
4. content 必须是该文件的完整最终内容（可直接运行），禁止省略、占位符或"其余不变"。
5. 按职责把代码拆成根目录下的多个模块文件，只输出本项目需要的文件。
6. 项目采用扁平布局：所有 .py 文件（含入口与测试）平铺在项目根目录，不创建子目录或包；入口文件必须命名为 main.py（验收的自动冒烟只识别 main.py/cli.py/__main__.py）；测试用 from <模块名> import ... 导入。
"""


class GenerationResult(BaseModel):
    """一次生成的产物与账目。"""

    files: dict[str, str]
    strategy: str
    usage: dict
    degraded: bool = False  # M2：plan 失败降级 oneshot 时置 True


def generate_oneshot(idea: str, client: LLMClient) -> GenerationResult:
    """单发策略：一次调用产出全部文件（含测试）。"""
    messages = [
        {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": f"# 需求\n{idea}\n\n请输出完整项目 JSON。"},
    ]
    obj, usage = client.chat_json(messages)
    patches = coerce_patch_list(obj)
    return GenerationResult(
        files={p.path: p.content for p in patches.files},
        strategy="oneshot", usage=usage)
