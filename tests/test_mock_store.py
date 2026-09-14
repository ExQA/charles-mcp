from pathlib import Path

import pytest

from charles_mcp.mocks.store import (
    ARCHIVE_DIR_NAME,
    MockPathConflictError,
    MockPathError,
    MockStore,
    normalize_host,
    split_request_path,
)


def test_write_places_file_under_host_and_request_path(tmp_path: Path) -> None:
    store = MockStore(tmp_path)

    record, archived = store.write("API.Example.com", "/api/v1/profile", '{"a": 1}\n')

    assert archived is None
    assert record.host == "api.example.com"
    assert record.path == "/api/v1/profile"
    file = tmp_path / "api.example.com" / "api" / "v1" / "profile"
    assert file.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert record.file == str(file)


def test_overwrite_archives_previous_version(tmp_path: Path) -> None:
    store = MockStore(tmp_path)
    store.write("api.example.com", "/profile", "old")

    _, archived = store.write("api.example.com", "/profile", "new")

    assert archived is not None
    assert Path(archived).read_text(encoding="utf-8") == "old"
    assert Path(archived).is_relative_to(tmp_path / ARCHIVE_DIR_NAME / "api.example.com")
    assert store.read("api.example.com", "/profile")[1] == "new"


def test_remove_archives_and_prunes_empty_directories(tmp_path: Path) -> None:
    store = MockStore(tmp_path)
    store.write("api.example.com", "/api/v1/profile", "x")

    archived = store.remove("api.example.com", "/api/v1/profile")

    assert Path(archived).read_text(encoding="utf-8") == "x"
    assert not (tmp_path / "api.example.com" / "api").exists()
    assert (tmp_path / "api.example.com").is_dir()
    assert store.list_mocks() == []


def test_remove_missing_mock_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        MockStore(tmp_path).remove("api.example.com", "/nope")


def test_list_skips_archive_hidden_files_and_filters_by_host(tmp_path: Path) -> None:
    store = MockStore(tmp_path)
    store.write("a.example.com", "/one", "1")
    store.write("a.example.com", "/one", "2")  # creates an archive entry
    store.write("b.example.com", "/nested/two", "2")
    (tmp_path / "a.example.com" / ".DS_Store").write_text("junk")

    assert [(r.host, r.path) for r in store.list_mocks()] == [
        ("a.example.com", "/one"),
        ("b.example.com", "/nested/two"),
    ]
    assert [r.path for r in store.list_mocks("b.example.com")] == ["/nested/two"]


def test_query_string_is_stripped_and_reported() -> None:
    segments, query = split_request_path("/items?page=2&sort=asc")

    assert segments == ["items"]
    assert query == "page=2&sort=asc"


@pytest.mark.parametrize(
    "path",
    ["items", "/items/", "/a//b", "/a/../b", "/./a", "/a\\b", "/a\x00b", ""],
)
def test_unsafe_or_ambiguous_paths_are_rejected(tmp_path: Path, path: str) -> None:
    with pytest.raises(MockPathError):
        MockStore(tmp_path).locate("api.example.com", path)


@pytest.mark.parametrize(
    "host",
    ["https://api.example.com", "api.example.com:443", "../etc", "_archive", "a/b", ""],
)
def test_invalid_hosts_are_rejected(host: str) -> None:
    with pytest.raises(MockPathError):
        normalize_host(host)


def test_file_blocks_nested_mock_below_it(tmp_path: Path) -> None:
    store = MockStore(tmp_path)
    store.write("api.example.com", "/users", "[]")

    with pytest.raises(MockPathConflictError, match="/users is a file"):
        store.write("api.example.com", "/users/42", "{}")


def test_directory_blocks_mock_with_same_path(tmp_path: Path) -> None:
    store = MockStore(tmp_path)
    store.write("api.example.com", "/users/42", "{}")

    with pytest.raises(MockPathConflictError, match="already a directory"):
        store.write("api.example.com", "/users", "[]")


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "mocks"
    (root / "api.example.com").mkdir(parents=True)
    (root / "api.example.com" / "link").symlink_to(outside)

    with pytest.raises(MockPathError, match="escapes"):
        MockStore(root).write("api.example.com", "/link/file", "x")
    assert list(outside.iterdir()) == []
