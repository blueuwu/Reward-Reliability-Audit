"""Seed replay determinism and generator-version gate (Gate E item 5)."""

from __future__ import annotations

from pathlib import Path

from grader_v2.grading.generators import generate_docids_suite, generate_queries_suite
from grader_v2.grading.semantic import SemanticProfile, get_profile, write_generated_suite

PROFILES = (
    ("tinydb-missing-doc-ids", "tinydb-docids-v1", generate_docids_suite),
    ("tinydb-query-test-unhashable", "tinydb-query-freeze-v1", generate_queries_suite),
)


def _profile(task_id: str) -> SemanticProfile:
    profile = get_profile(task_id)
    assert profile is not None
    return profile


def test_profile_lookup() -> None:
    assert _profile("tinydb-missing-doc-ids").profile_id == "tinydb-docids-v1"
    assert _profile("tinydb-query-test-unhashable").profile_id == "tinydb-query-freeze-v1"
    assert get_profile("tomli-type-error") is None


def test_same_seed_same_suite() -> None:
    for task_id, _profile_id, _ in PROFILES:
        profile = _profile(task_id)
        a = profile.generate(12345)
        b = profile.generate(12345)
        assert a.filename == b.filename
        assert a.text == b.text
        assert a.expected_nodeids == b.expected_nodeids


def test_different_seed_different_suite() -> None:
    for task_id, _profile_id, _ in PROFILES:
        profile = _profile(task_id)
        a = profile.generate(1)
        b = profile.generate(2)
        assert a.text != b.text


def test_suite_is_deterministic_python(tmp_path: Path) -> None:
    for task_id, _profile_id, _ in PROFILES:
        profile = _profile(task_id)
        suite = profile.generate(20260807)
        assert "\n" in suite.text
        compile(suite.text, str(tmp_path / suite.filename), "exec")


def test_written_suite_sha_is_byte_stable(tmp_path: Path) -> None:
    profile = _profile("tinydb-missing-doc-ids")
    suite = profile.generate(9)
    first = write_generated_suite(tmp_path, suite)
    second = write_generated_suite(tmp_path, suite)
    assert first == second
    assert first[0].is_dir()
    assert len(first[1]) == 64


def test_replay_matches_recorded_nodeids(tmp_path: Path) -> None:
    profile = _profile("tinydb-query-test-unhashable")
    suite = profile.generate(20260807)
    assert suite.expected_nodeids == sorted(suite.expected_nodeids)
    assert all(name.startswith("test_semantic_queries.py::") for name in suite.expected_nodeids)


def test_generator_version_is_frozen_identifier() -> None:
    for task_id, profile_id, _ in PROFILES:
        profile = _profile(task_id)
        assert profile.generator_version.startswith(profile_id + "@")
        assert profile.generator_version.split("@")[-1].isdigit()
