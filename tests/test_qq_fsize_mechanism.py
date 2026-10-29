"""Offline mechanism contracts; never alter this pytest process's resource limits."""
import errno
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from tools import qq_fsize_mechanism as subject


@pytest.mark.parametrize("error,expected", [(None, "resized"), (errno.EFBIG, "efbig"),
                                            (errno.EACCES, "other_os_error")])
def test_resize_categories(error, expected):
    """Keep failures numeric/fixed and do not disclose OS messages or paths."""
    with mock.patch.object(subject.os, "ftruncate", create=True) as truncate, \
            mock.patch.object(subject.os, "fstat", return_value=SimpleNamespace(st_size=0)):
        if error:
            truncate.side_effect = OSError(error, "private-path")
        assert subject.resize(7) == {"outcome": expected, "size": 0}
        truncate.assert_called_once_with(7, 32768)


def test_no_execution_without_consent(monkeypatch):
    """Refuse absent consent before creating evidence or children."""
    monkeypatch.setattr(subject.sys, "platform", "linux")
    with mock.patch.object(subject, "observe") as observe:
        with pytest.raises(SystemExit):
            subject.main([])
        observe.assert_not_called()


def test_exclusive_evidence(monkeypatch, tmp_path):
    """Existing evidence is never overwritten and no experiment is repeated."""
    monkeypatch.setattr(subject.sys, "platform", "linux")
    artifact = tmp_path / "evidence.json"
    artifact.write_text("original", encoding="utf-8")
    monkeypatch.setattr(subject, "ARTIFACT", artifact)
    with mock.patch.object(subject, "observe") as observe:
        with pytest.raises(FileExistsError):
            subject.main(["--execute"])
        observe.assert_not_called()
    assert artifact.read_text(encoding="utf-8") == "original"


def test_actual_disposable_child_contrast():
    """Pin measured mechanism only, without claiming a QQ allocation or fatal cause."""
    report = json.loads(subject.ARTIFACT.read_text(encoding="utf-8"))
    assert report["parent_limit_unchanged"] is True
    assert report["qq_executed"] is report["qq_limits_changed"] is report["qq_root_cause_proven"] is False
    assert report["child_sigxfsz_ignored_for_errno_only"] is True
    assert report["observations"] == [
        {"kind": kind, "child_limit_bytes": limit,
         "outcome": "efbig" if limit == 16384 else "resized",
         "size": 0 if limit == 16384 else 32768}
        for kind in ("tmpfs", "memfd") for limit in (16384, 65536)]
