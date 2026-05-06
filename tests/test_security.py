"""Security guards on tools that read filesystem state from skill data.

`get_skill_instructions` reads the file referenced by `skill_path`. The
field is data, so a hostile or buggy seed could put `../../etc/passwd` in
there and try to coax the tool into reading something outside the repo.
The tool refuses any path that doesn't resolve under the repo root.
"""

import os

from pymongo import MongoClient


def _insert_skill_with_path(path_value: str):
    """Bypass the seed and the validator's optional skill_path field by
    inserting a doc that satisfies the required-fields constraint. Returns
    the inserted skill_id so tests can clean up if needed."""
    db = MongoClient(os.environ["MONGODB_URI"])[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    sid = "skill:test-malicious"
    db.skills.delete_one({"_id": sid})
    db.skills.insert_one({
        "_id": sid,
        "name": "Malicious Skill",
        "version": "0.0.1",
        "lifecycle": "active",
        "input":  {"type": "x"},
        "output": {"type": "y"},
        "dependencies": [],
        "input_type": "x",
        "output_type": "y",
        "skill_path": path_value,
    })
    return sid


def test_get_skill_instructions_refuses_path_traversal(seeded, call):
    sid = _insert_skill_with_path("../../etc/passwd")
    r = call(seeded.get_skill_instructions, skill_id=sid)
    assert "error" in r
    assert "escapes trusted roots" in r["error"]


def test_get_skill_instructions_refuses_absolute_path(seeded, call):
    sid = _insert_skill_with_path("/etc/passwd")
    r = call(seeded.get_skill_instructions, skill_id=sid)
    # Either the resolve-and-relative_to check rejects it, or the file
    # simply isn't present under the repo. Either outcome is safe; the
    # important thing is that no /etc/passwd content is returned.
    assert "error" in r
    assert "content" not in r


def test_get_skill_instructions_relative_path_within_repo_is_allowed(seeded, call):
    """If the file actually exists under the repo root, the tool should
    return its content. README.md is shipped, so use it as a test fixture."""
    sid = _insert_skill_with_path("README.md")
    r = call(seeded.get_skill_instructions, skill_id=sid)
    assert "error" not in r
    assert r["skill_path"] == "README.md"
    assert "content" in r
    assert r["content"].startswith("#")
