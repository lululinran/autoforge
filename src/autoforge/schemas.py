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

    __test__ = False

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
