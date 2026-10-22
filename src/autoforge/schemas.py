"""schemas.py — 系统各环节之间的数据契约（pydantic）。

TestReport/Failure/FixRequest：沙箱判卷结果与修复请求；
FilePatch/PatchList：LLM 提案的标准文件补丁格式（生成与修复共用同一原语）。
"""

from typing import Any, List, Optional

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


def coerce_patch_list(obj: Any) -> PatchList:
    """把 LLM 输出（{"files": [...]} 或裸数组）规整为 PatchList，统一守卫。"""
    files = obj.get("files") if isinstance(obj, dict) else obj
    if not isinstance(files, list):
        raise ValueError(f"输出缺少可用的 files 列表: {str(obj)[:200]}")
    if not files:
        raise ValueError(f"输出 files 列表为空: {str(obj)[:200]}")
    if not all(isinstance(f, dict) for f in files):
        raise ValueError(f"files 列表元素必须是对象: {str(files)[:200]}")
    patches = PatchList(files=[FilePatch(**f) for f in files])
    paths = [p.path for p in patches.files]
    dups = sorted({p for p in paths if paths.count(p) > 1})
    if dups:
        raise ValueError(f"输出包含重复路径: {dups[:20]}")
    return patches
