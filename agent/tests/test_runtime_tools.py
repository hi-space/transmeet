import pytest

from runtime import tools
from runtime.workspace import ToolPathError, create_workspace


@pytest.fixture
def ws(tmp_path):
    return create_workspace("s" * 33, base=tmp_path)


def names(ts):
    return {t.name for t in ts}


def by_name(ws, *wanted):
    return {t.name: t for t in tools.make_tools(ws, wanted)}


def test_make_tools_filters_by_name(ws):
    assert names(tools.make_tools(ws, ["bash", "read_file"])) == {"bash", "read_file"}
    with pytest.raises(KeyError):
        tools.make_tools(ws, ["nope"])


def test_write_read_edit_roundtrip(ws):
    t = by_name(ws, "write_file", "read_file", "edit_file")
    assert "wrote" in t["write_file"].invoke({"path": "a.txt", "content": "hello world"})
    assert t["read_file"].invoke({"path": "a.txt"}) == "hello world"
    assert "ok" in t["edit_file"].invoke({"path": "a.txt", "old": "world", "new": "there"})
    assert (ws.root / "a.txt").read_text() == "hello there"


def test_write_creates_subdirectories(ws):
    t = by_name(ws, "write_file")
    t["write_file"].invoke({"path": "diagrams/x/y.svg", "content": "<svg/>"})
    assert (ws.root / "diagrams" / "x" / "y.svg").exists()


def test_edit_file_requires_exactly_one_match(ws):
    t = by_name(ws, "write_file", "edit_file")
    t["write_file"].invoke({"path": "a.txt", "content": "x x"})
    out = t["edit_file"].invoke({"path": "a.txt", "old": "x", "new": "y"})
    assert "2" in out and "error" in out.lower()
    assert (ws.root / "a.txt").read_text() == "x x"
    assert "error" in t["edit_file"].invoke({"path": "a.txt", "old": "zz", "new": "y"}).lower()
    assert "error" in t["edit_file"].invoke({"path": "missing.txt", "old": "a", "new": "b"}).lower()


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "skills/../../x", ""])
def test_paths_outside_workspace_rejected(ws, bad):
    t = by_name(ws, "read_file", "write_file")
    assert "error" in t["read_file"].invoke({"path": bad}).lower()
    assert "error" in t["write_file"].invoke({"path": bad, "content": "x"}).lower()
    with pytest.raises(ToolPathError):
        ws.resolve(bad)


def test_read_missing_file_is_error_string(ws):
    t = by_name(ws, "read_file")
    assert "not found" in t["read_file"].invoke({"path": "nope.txt"})


def test_bash_runs_in_workspace_and_truncates(ws):
    t = by_name(ws, "bash")
    assert t["bash"].invoke({"command": "pwd"}).strip().endswith(ws.root.name)
    out = t["bash"].invoke({"command": "python3 -c \"print('a'*50000)\""})
    assert len(out) < tools.BASH_OUTPUT_LIMIT + 200 and "truncated" in out


def test_bash_reports_stderr_and_exit_code(ws):
    t = by_name(ws, "bash")
    out = t["bash"].invoke({"command": "echo oops >&2; exit 3"})
    assert "oops" in out and "exit code 3" in out


def test_bash_timeout(ws):
    t = by_name(ws, "bash")
    assert "timed out" in t["bash"].invoke({"command": "sleep 5", "timeout_s": 1})


def test_load_skill_lists_bundle(ws):
    t = by_name(ws, "load_skill")
    out = t["load_skill"].invoke({"name": "meeting-diagrams"})
    assert "Meeting Diagrams" in out
    assert "skills/meeting-diagrams/scripts/timeline_svg.py" in out
    assert "error" in t["load_skill"].invoke({"name": "nope"}).lower()


def test_workspace_has_skills_link_and_diagrams_dir(ws):
    assert (ws.root / "skills" / "meeting-minutes-page" / "SKILL.md").exists()
    assert (ws.root / "diagrams").is_dir()
    # 스킬 파일은 링크를 통해 읽을 수 있어야 한다 (resolve 가 skills_dir 하위를 허용)
    assert ws.read_text("skills/meeting-minutes-page/SKILL.md").startswith("---")


def test_create_workspace_sanitizes_session_id(tmp_path):
    ws = create_workspace("a/b:c d", base=tmp_path)
    assert ws.root.parent == tmp_path and "/" not in ws.root.name and ":" not in ws.root.name
