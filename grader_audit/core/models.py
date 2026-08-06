"""Strict schemas for the normative task and patch manifests (Sections 27.7 and 27.8).

All models reject unknown fields (``extra="forbid"``). Manifest loading is done
through Pydantic so every field rule in Section 27.7 is enforced at parse time.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from grader_audit.core.path_rules import classify_container_path, classify_repository_relative

SCHEMA_VERSION = "1.0"

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEBAB_CASE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class Split(StrEnum):
    DEVELOPMENT = "development"
    FROZEN_EVAL = "frozen_eval"


class PatchSplit(StrEnum):
    DEVELOPMENT = "development"
    FROZEN_EVAL = "frozen_eval"
    ADAPTIVE = "adaptive"


class PatchLabel(StrEnum):
    VALID = "valid"
    INVALID = "invalid"


class ValidSubtype(StrEnum):
    GOLD = "gold"
    ALTERNATIVE = "alternative"
    UNUSUAL_VALID = "unusual_valid"


class InvalidSubtype(StrEnum):
    REWARD_HACK = "reward_hack"
    OVERFIT = "overfit"
    INCOMPLETE = "incomplete"
    SCOPE_VIOLATION = "scope_violation"
    RUNTIME_MANIPULATION = "runtime_manipulation"


class PatchAuthor(StrEnum):
    HUMAN_CURATED = "human_curated"
    MODEL_GENERATED = "model_generated"
    UPSTREAM_GOLD = "upstream_gold"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_safe_relative(value: str) -> str:
    reason = classify_repository_relative(value)
    if reason is not None:
        raise ValueError(f"invalid repository-relative path {value!r}: {reason}")
    return value


def _require_safe_container(value: str) -> str:
    reason = classify_container_path(value)
    if reason is not None:
        raise ValueError(f"invalid container path {value!r}: {reason}")
    return value


def _require_full_commit_sha(value: str) -> str:
    if not _FULL_COMMIT_SHA.fullmatch(value):
        raise ValueError("commit SHA must be a full 40-character lowercase hex value")
    return value


def _require_full_sha256(value: str) -> str:
    if not _FULL_SHA256.fullmatch(value):
        raise ValueError("SHA-256 must be a full 64-character lowercase hex value")
    return value


def _require_valid_id(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError("id must match ^[a-z0-9][a-z0-9-]{2,63}$")
    return value


def _require_node_ids(value: list[str]) -> list[str]:
    for node_id in value:
        if not node_id or "\x00" in node_id:
            raise ValueError(f"invalid test node ID {node_id!r}")
    return value


class SourceInfo(StrictModel):
    repo_url: HttpUrl
    license_spdx: str = Field(min_length=1)
    license_file: str
    fix_commit: str
    baseline_commit: str
    vendored_tree_sha256: str

    @field_validator("license_file")
    @classmethod
    def validate_license_file(cls, value: str) -> str:
        return _require_safe_relative(value)

    @field_validator("fix_commit", "baseline_commit")
    @classmethod
    def validate_commits(cls, value: str) -> str:
        return _require_full_commit_sha(value)

    @field_validator("vendored_tree_sha256")
    @classmethod
    def validate_tree_sha(cls, value: str) -> str:
        return _require_full_sha256(value)


class RuntimeInfo(StrictModel):
    python: Literal["3.12"]
    requirements_lock: str
    build_timeout_seconds: int = Field(gt=0)
    command_timeout_seconds: int = Field(gt=0)
    memory_mb: int = Field(gt=0)
    pids_limit: int = Field(gt=0)

    @field_validator("requirements_lock")
    @classmethod
    def validate_requirements_lock(cls, value: str) -> str:
        return _require_safe_relative(value)


class WorkspaceInfo(StrictModel):
    source_dir: str
    container_root: str
    source_roots: list[str] = Field(min_length=1)
    visible_tests_dir: str
    visible_tests_target: str
    expose_redacted_manifest: bool
    editable_globs: list[str] = Field(min_length=1)
    immutable_workspace_globs: list[str] = Field(min_length=1)
    expected_change_globs: list[str] = Field(default_factory=list)
    generated_artifact_globs: list[str] = Field(default_factory=list)

    @field_validator("source_dir", "visible_tests_dir", "visible_tests_target")
    @classmethod
    def validate_relative_paths(cls, value: str) -> str:
        return _require_safe_relative(value)

    @field_validator("container_root")
    @classmethod
    def validate_container_root(cls, value: str) -> str:
        return _require_safe_container(value)

    @field_validator("source_roots", "editable_globs", "immutable_workspace_globs")
    @classmethod
    def validate_relative_globs(cls, value: list[str]) -> list[str]:
        return [_require_safe_relative(item) for item in value]


class NaiveGrading(StrictModel):
    argv: list[str] = Field(min_length=1)
    cwd: str
    timeout_seconds: int = Field(gt=0)

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("argv entries must be non-empty strings")
        return value

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        return _require_safe_container(value)


class HardenedGrading(StrictModel):
    tests_dir: str
    expected_nodeids: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)

    @field_validator("tests_dir")
    @classmethod
    def validate_tests_dir(cls, value: str) -> str:
        return _require_safe_relative(value)

    @field_validator("expected_nodeids")
    @classmethod
    def validate_node_ids(cls, value: list[str]) -> list[str]:
        return _require_node_ids(value)


class OracleGrading(StrictModel):
    tests_dir: str
    expected_nodeids: list[str] = Field(min_length=1)

    @field_validator("tests_dir")
    @classmethod
    def validate_tests_dir(cls, value: str) -> str:
        return _require_safe_relative(value)

    @field_validator("expected_nodeids")
    @classmethod
    def validate_node_ids(cls, value: list[str]) -> list[str]:
        return _require_node_ids(value)


class GradingInfo(StrictModel):
    naive: NaiveGrading
    hardened_v1: HardenedGrading
    oracle: OracleGrading


class ValidationInfo(StrictModel):
    baseline_expected_failing_nodeids: list[str] = Field(min_length=1)
    gold_patch_id: str

    @field_validator("baseline_expected_failing_nodeids")
    @classmethod
    def validate_node_ids(cls, value: list[str]) -> list[str]:
        return _require_node_ids(value)

    @field_validator("gold_patch_id")
    @classmethod
    def validate_gold_patch_id(cls, value: str) -> str:
        return _require_valid_id(value)


class TaskManifest(StrictModel):
    schema_version: Literal["1.0"]
    id: str
    title: str = Field(min_length=1)
    split: Split
    source: SourceInfo
    runtime: RuntimeInfo
    workspace: WorkspaceInfo
    grading: GradingInfo
    validation: ValidationInfo

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _require_valid_id(value)


class PatchManifest(StrictModel):
    schema_version: Literal["1.0"]
    id: str
    task_id: str
    label: PatchLabel
    subtype: ValidSubtype | InvalidSubtype
    split: PatchSplit
    attack_family: str | None = None
    description: str = Field(min_length=1)
    why_labeled: str = Field(min_length=1)
    expected_stress: str | None = None
    author: PatchAuthor

    @field_validator("id", "task_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _require_valid_id(value)

    @model_validator(mode="after")
    def check_label_subtype_consistency(self) -> Self:
        if self.label is PatchLabel.VALID:
            if not isinstance(self.subtype, ValidSubtype):
                raise ValueError(
                    "valid patches must use a valid subtype (gold, alternative, unusual_valid)"
                )
            if self.attack_family is not None:
                raise ValueError("valid patches must have attack_family: null")
        else:
            if not isinstance(self.subtype, InvalidSubtype):
                raise ValueError("invalid patches must use an invalid subtype")
            if self.attack_family is None:
                raise ValueError("invalid patches must declare an attack_family")
            if not _KEBAB_CASE.fullmatch(self.attack_family):
                raise ValueError("attack_family must be a kebab-case identifier")
        return self


def load_task_manifest_yaml(text: str) -> TaskManifest:
    """Parse and strictly validate a ``task.yaml`` document."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("task manifest must be a YAML mapping")
    return TaskManifest.model_validate(data)


def load_patch_manifest_yaml(text: str) -> PatchManifest:
    """Parse and strictly validate a ``patch.yaml`` document."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("patch manifest must be a YAML mapping")
    return PatchManifest.model_validate(data)
