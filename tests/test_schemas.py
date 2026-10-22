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


def test_patch_path_backslash_normalized():
    p = FilePatch(path="a\\b.py", content="x")
    assert p.path == "a/b.py"


def test_patch_list_accepts_files():
    pl = PatchList(files=[FilePatch(path="m.py", content="y = 1")])
    assert pl.files[0].content == "y = 1"
