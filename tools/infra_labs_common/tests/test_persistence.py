from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra_labs_common.persistence import atomic_write_json


def test_writes_json_payload(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    atomic_write_json(path, {"b": 2, "a": 1}, tmp_prefix=".test-")
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": 2}


def test_output_is_sorted_and_indented(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    atomic_write_json(path, {"b": 2, "a": 1}, tmp_prefix=".test-")
    # sort_keys + indent=2 by default so the file is stable and diff-friendly.
    assert path.read_text(encoding="utf-8") == '{\n  "a": 1,\n  "b": 2\n}'


def test_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "out.json"
    atomic_write_json(path, {"k": "v"}, tmp_prefix=".test-")
    assert path.exists()


def test_accepts_str_path(tmp_path: Path) -> None:
    path = str(tmp_path / "out.json")
    atomic_write_json(path, ["x"], tmp_prefix=".test-")
    assert json.loads(Path(path).read_text(encoding="utf-8")) == ["x"]


def test_no_tmp_file_left_behind_on_success(tmp_path: Path) -> None:
    atomic_write_json(tmp_path / "out.json", {"k": "v"}, tmp_prefix=".test-")
    assert list(tmp_path.glob(".test-*.tmp")) == []


def test_tmp_file_cleaned_up_on_serialization_error(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    with pytest.raises(TypeError):
        atomic_write_json(path, {"bad": object()}, tmp_prefix=".test-")
    # The temp file must not be left behind after a failed serialization.
    assert list(tmp_path.glob(".test-*.tmp")) == []
    assert not path.exists()
