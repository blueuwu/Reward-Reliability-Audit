"""Adversarial tests for ``report`` (Sections 27.16-27.18)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import cast

import pytest
import yaml

from grader_audit.core.hashing import sha256_bytes
from grader_audit.core.outcomes import (
    Changes,
    EnvironmentInfo,
    ErrorInfo,
    GitInfo,
    GraderInfo,
    OutcomeStatus,
    PatchInfo,
    ProcessInfo,
    ResultInfo,
    TaskInfo,
    WorkspaceHashes,
)
from grader_audit.core.reporting import (
    ReportError,
    load_experiment,
    run_report,
    verify_experiment,
    wilson_interval,
)
from grader_audit.core.results import (
    EvaluationRecord,
    ValidationRecord,
    ValidationRun,
    serialize_record,
)

EXP_ID = "exp-001"
_MANIFEST_SHA = "a" * 64
_GOLD_META = "b" * 64
_GOLD_DIFF = "c" * 64
_ATK_META = "d" * 64
_ATK_DIFF = "e" * 64


def _record(
    grader: str,
    patch_id: str,
    *,
    label: str,
    split: str = "development",
    phase: str = "controlled",
    status: str = OutcomeStatus.COMPLETED.value,
    meta_sha: str,
    diff_sha: str,
    pre_grade: str,
    pristine: str,
    stdout: bytes = b"ok\n",
    artifact_rel: str,
    attack_family: str | None = None,
    project_root: Path,
    exp_dir: Path,
    reason_codes: list[str] | None = None,
) -> EvaluationRecord:
    reasons = reason_codes or ([] if status == OutcomeStatus.COMPLETED.value else ["infra"])
    reward = (1.0 if label == "valid" else 0.0) if status == OutcomeStatus.COMPLETED.value else None
    error = (
        None
        if status == OutcomeStatus.COMPLETED.value
        else ErrorInfo(code="infra", message="x")
    )
    process = ProcessInfo(
        argv=["python", "-m", "pytest", "-q"],
        cwd="/workspace",
        exit_code=0 if status == OutcomeStatus.COMPLETED.value else 1,
        timed_out=False,
        stdout_path=artifact_rel,
        stderr_path=None,
        stdout_sha256=sha256_bytes(stdout),
        stderr_sha256=None,
        stdout_truncated=False,
        stderr_truncated=False,
        stdout_bytes=len(stdout),
        stderr_bytes=0,
        duration_seconds=0.1,
    )
    return EvaluationRecord(
        schema_version="1.0",
        run_id=uuid.uuid4().hex,
        experiment_id=EXP_ID,
        timestamp_utc="2026-08-06T00:00:00+00:00",
        status=status,
        phase=phase,
        validation_case=None,
        repeat_index=0,
        git=GitInfo(data_commit="9" * 40, grader_frozen_commit=None, worktree_dirty=False),
        grader=GraderInfo(name=grader, version="v1"),
        task=TaskInfo(id="task-a", split=split, manifest_sha256=_MANIFEST_SHA),
        patch=PatchInfo(
            id=patch_id,
            label=label,
            subtype="gold" if label == "valid" else "reward_hack",
            attack_family=attack_family,
            metadata_sha256=meta_sha,
            diff_sha256=diff_sha,
        ),
        environment=EnvironmentInfo(
            python="3.12.13", pytest="9.1.1", hud="0.6.12", docker_image_digest="sha256:" + "0" * 64
        ),
        workspace=WorkspaceHashes(
            pristine_sha256=pristine, pre_grade_sha256=pre_grade, post_grade_sha256="f" * 64
        ),
        result=ResultInfo(
            reward=reward,
            accepted=(reward == 1.0) if reward is not None else None,
            reason_codes=reasons,
            warnings=[],
            duration_seconds=0.1,
        ),
        process=process,
        test_evidence=None,
        changes=Changes(),
        error=error,
    )


def _write_record(exp_dir: Path, record: EvaluationRecord) -> Path:
    assert record.patch is not None
    target = (
        exp_dir / record.grader.name / record.task.split / record.task.id
        / f"{record.patch.id}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(serialize_record(record))
    return target


def _artifact_path(exp_dir: Path, grader: str, patch: str, content: bytes) -> str:
    name = f"{grader}-{patch}.stdout"
    (exp_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (exp_dir / "artifacts" / name).write_bytes(content)
    return f"results/raw/{EXP_ID}/artifacts/{name}"


def _plan_cell(grader: str, patch_id: str, *, meta: str, diff: str) -> dict[str, object]:
    return {
        "grader": grader,
        "task_id": "task-a",
        "patch_id": patch_id,
        "split": "development",
        "phase": "controlled",
        "task_manifest_sha256": _MANIFEST_SHA,
        "patch_metadata_sha256": meta,
        "patch_diff_sha256": diff,
    }


def _build_experiment(tmp_path: Path) -> Path:
    project_root = tmp_path
    exp_dir = project_root / "results" / "raw" / EXP_ID
    pre = "1" * 64
    pristine = "2" * 64
    gold_out = b"gold ok\n"
    atk_out = b"attack ok\n"
    records = [
        _record(
            "naive", "gold", label="valid", meta_sha=_GOLD_META, diff_sha=_GOLD_DIFF,
            pre_grade=pre, pristine=pristine, stdout=gold_out,
            artifact_rel=_artifact_path(exp_dir, "naive", "gold", gold_out),
            project_root=project_root, exp_dir=exp_dir,
        ),
        _record(
            "hardened_v1", "gold", label="valid", meta_sha=_GOLD_META, diff_sha=_GOLD_DIFF,
            pre_grade=pre, pristine=pristine, stdout=gold_out,
            artifact_rel=_artifact_path(exp_dir, "hardened_v1", "gold", gold_out),
            project_root=project_root, exp_dir=exp_dir,
        ),
        _record(
            "naive", "attack", label="invalid", meta_sha=_ATK_META, diff_sha=_ATK_DIFF,
            pre_grade=pre, pristine=pristine, stdout=atk_out,
            attack_family="import-shadowing",
            artifact_rel=_artifact_path(exp_dir, "naive", "attack", atk_out),
            project_root=project_root, exp_dir=exp_dir,
        ),
        _record(
            "hardened_v1", "attack", label="invalid", meta_sha=_ATK_META, diff_sha=_ATK_DIFF,
            pre_grade=pre, pristine=pristine, stdout=atk_out,
            attack_family="import-shadowing",
            artifact_rel=_artifact_path(exp_dir, "hardened_v1", "attack", atk_out),
            project_root=project_root, exp_dir=exp_dir,
        ),
    ]
    for record in records:
        _write_record(exp_dir, record)
    plan = [
        _plan_cell("naive", "gold", meta=_GOLD_META, diff=_GOLD_DIFF),
        _plan_cell("hardened_v1", "gold", meta=_GOLD_META, diff=_GOLD_DIFF),
        _plan_cell("naive", "attack", meta=_ATK_META, diff=_ATK_DIFF),
        _plan_cell("hardened_v1", "attack", meta=_ATK_META, diff=_ATK_DIFF),
    ]
    (exp_dir / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "experiment_id": EXP_ID,
                "timestamp_utc": "2026-08-06T00:00:00+00:00",
                "plan": {"controlled": plan, "validation": []},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return exp_dir


def _project_root(exp_dir: Path) -> Path:
    return exp_dir.parent.parent.parent


def _load(exp_dir: Path) -> None:
    root = _project_root(exp_dir)
    loaded = load_experiment(root, exp_dir)
    verify_experiment(root, loaded)


def _add_validation(
    exp_dir: Path,
    *,
    repeat_index: int = 1,
    manifest_sha: str = _MANIFEST_SHA,
    planned_manifest_sha: str = _MANIFEST_SHA,
    path_repeat_index: int | None = None,
    add_plan: bool = True,
    artifact_rel: str | None = None,
) -> Path:
    process = None
    if artifact_rel is not None:
        process = ProcessInfo(
            argv=["python", "-m", "pytest"],
            cwd="/workspace",
            exit_code=0,
            timed_out=False,
            stdout_path=artifact_rel,
            stderr_path=None,
            stdout_sha256=sha256_bytes(b"validation\n"),
            stderr_sha256=None,
            stdout_truncated=False,
            stderr_truncated=False,
            stdout_bytes=11,
            stderr_bytes=0,
            duration_seconds=0.1,
        )
    record = ValidationRecord(
        schema_version="1.0",
        run_id=uuid.uuid4().hex,
        experiment_id=EXP_ID,
        timestamp_utc="2026-08-06T00:00:00+00:00",
        git=GitInfo(data_commit="9" * 40, grader_frozen_commit=None, worktree_dirty=False),
        task=TaskInfo(id="task-a", split="development", manifest_sha256=manifest_sha),
        environment=EnvironmentInfo(
            python="3.12.13",
            pytest="9.1.1",
            hud="0.6.12",
            docker_image_digest="sha256:" + "0" * 64,
        ),
        validation_case="gold",
        repeat_index=repeat_index,
        runs={
            "naive": ValidationRun(
                grader=GraderInfo(name="naive", version="v1"),
                status="completed",
                reward=1.0,
                accepted=True,
                workspace=WorkspaceHashes(
                    pristine_sha256="2" * 64,
                    pre_grade_sha256="1" * 64,
                    post_grade_sha256="f" * 64,
                ),
                process=process,
            )
        },
        stable=True,
    )
    if add_plan:
        metadata_path = exp_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["plan"]["validation"].append(
            {
                "task_id": "task-a",
                "split": "development",
                "task_manifest_sha256": planned_manifest_sha,
                "validation_case": "gold",
                "repeat_index": repeat_index,
            }
        )
        metadata_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    path_index = path_repeat_index if path_repeat_index is not None else repeat_index
    target = exp_dir / "validation" / "development" / "task-a" / "gold" / f"{path_index}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(record.serialize())
    return target


def _bind_all_annotations(exp_dir: Path, annotations_root: Path) -> None:
    """Write confirmed annotations with bound raw-record hashes per grader."""
    import yaml

    for record_file in exp_dir.rglob("*.json"):
        if record_file.name == "metadata.json" or "validation" in record_file.parts:
            continue
        record = EvaluationRecord.model_validate(
            json.loads(record_file.read_text(encoding="utf-8"))
        )
        patch = record.patch
        if patch is None:
            continue
        ann = annotations_root / EXP_ID / record.task.id / f"{patch.id}.yaml"
        ann.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, object] = {}
        if ann.is_file():
            loaded = yaml.safe_load(ann.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = dict(cast(dict[str, object], loaded))
        data["disposition"] = "confirmed"
        data["truth_label"] = patch.label
        data["recorded_patch_hashes"] = {
            "metadata_sha256": patch.metadata_sha256,
            "diff_sha256": patch.diff_sha256,
        }
        existing_raw = data.get("recorded_raw_record_hashes")
        raw: dict[str, object] = (
            dict(cast(dict[str, object], existing_raw))
            if isinstance(existing_raw, dict)
            else {}
        )
        raw[record.grader.name] = sha256_bytes(record_file.read_bytes())
        data["recorded_raw_record_hashes"] = raw
        ann.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Wilson interval
# ---------------------------------------------------------------------------


def test_wilson_interval_fixture() -> None:
    interval = wilson_interval(10, 20)
    assert interval.point == pytest.approx(0.5)
    assert interval.low == pytest.approx(0.2993, abs=1e-3)
    assert interval.high == pytest.approx(0.7007, abs=1e-3)
    nz = wilson_interval(0, 0)
    assert nz.point is None and nz.low is None and nz.high is None


# ---------------------------------------------------------------------------
# Valid experiment
# ---------------------------------------------------------------------------


def test_complete_experiment_passes(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _load(exp_dir)


def test_report_renders_complete(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _bind_all_annotations(exp_dir, tmp_path / "results" / "annotations")
    out = tmp_path / "results" / "summaries" / "exp-001.md"
    text = run_report(
        project_root=_project_root(exp_dir),
        input_dir=exp_dir,
        output_path=out,
        final=True,
        frozen_tag="grader-v1-frozen",
        protected_tree_sha256="0" * 64,
    )
    assert "## Status: COMPLETE" in text
    assert "False reward rate" in text
    assert "95% Wilson" in text
    assert "| development | 1 | 1 |" in text
    assert text.count("| gold | 0 / 1 |") == 2
    assert text.count("| import-shadowing | 1 | 1 |") == 2
    assert (tmp_path / "results" / "report.md").read_text(encoding="utf-8") == text


def test_missing_record_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    (exp_dir / "naive" / "development" / "task-a" / "gold.json").unlink()
    with pytest.raises(ReportError):
        _load(exp_dir)


def test_extra_record_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _load(exp_dir)
    extra = _record(
        "naive", "stray", label="invalid", meta_sha="a" * 64, diff_sha="b" * 64,
        pre_grade="1" * 64, pristine="2" * 64,
        artifact_rel=_artifact_path(exp_dir, "naive", "stray", b"stray\n"),
        project_root=_project_root(exp_dir), exp_dir=exp_dir,
    )
    _write_record(exp_dir, extra)
    with pytest.raises(ReportError):
        _load(exp_dir)


def test_duplicate_plan_cell_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _load(exp_dir)
    plan_path = exp_dir / "metadata.json"
    metadata = json.loads(plan_path.read_text(encoding="utf-8"))
    metadata["plan"]["controlled"].append(
        _plan_cell("naive", "gold", meta=_GOLD_META, diff=_GOLD_DIFF)
    )
    plan_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    with pytest.raises(ReportError, match="duplicate planned cell"):
        _load(exp_dir)


def test_validation_plan_complete_passes(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _add_validation(exp_dir)
    _load(exp_dir)


def test_duplicate_validation_plan_cell_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _add_validation(exp_dir)
    metadata_path = exp_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["plan"]["validation"].append(dict(metadata["plan"]["validation"][0]))
    metadata_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    with pytest.raises(ReportError, match="duplicate planned validation cell"):
        _load(exp_dir)


def test_missing_validation_record_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    path = _add_validation(exp_dir)
    path.unlink()
    with pytest.raises(ReportError, match="validation plan incomplete"):
        _load(exp_dir)


def test_extra_validation_record_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _add_validation(exp_dir, add_plan=False)
    with pytest.raises(ReportError, match="validation record not in plan"):
        _load(exp_dir)


def test_validation_manifest_mismatch_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _add_validation(exp_dir, manifest_sha="0" * 64)
    with pytest.raises(ReportError, match="validation task manifest hash mismatch"):
        _load(exp_dir)


def test_validation_record_wrong_path_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _add_validation(exp_dir, path_repeat_index=2)
    with pytest.raises(ReportError, match="validation record at wrong path"):
        _load(exp_dir)


def test_validation_artifact_outside_experiment_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    outside = tmp_path / "outside-validation.txt"
    outside.write_bytes(b"validation\n")
    _add_validation(exp_dir, artifact_rel="outside-validation.txt")
    with pytest.raises(ReportError, match="artifact outside experiment directory"):
        _load(exp_dir)


def test_evaluation_record_cannot_smuggle_validation_phase(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    path = exp_dir / "naive" / "development" / "task-a" / "gold.json"
    record = EvaluationRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
    record.phase = "validation"
    record.validation_case = "gold"
    record.repeat_index = 1
    path.write_bytes(serialize_record(record))
    with pytest.raises(ReportError, match="outside the validation tree"):
        load_experiment(_project_root(exp_dir), exp_dir)


def test_wrong_location_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    (exp_dir / "naive" / "development" / "task-a" / "gold.json").rename(
        exp_dir / "naive" / "development" / "task-a" / "wrong.json"
    )
    with pytest.raises(ReportError, match="wrong path"):
        _load(exp_dir)


def test_artifact_hash_mismatch_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    (exp_dir / "artifacts" / "naive-gold.stdout").write_bytes(b"tampered")
    with pytest.raises(ReportError, match="artifact hash mismatch"):
        _load(exp_dir)


def test_artifact_outside_experiment_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _load(exp_dir)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"out")
    tampered = _record(
        "naive", "gold", label="valid", meta_sha=_GOLD_META, diff_sha=_GOLD_DIFF,
        pre_grade="1" * 64, pristine="2" * 64,
        artifact_rel="outside.txt",
        project_root=_project_root(exp_dir), exp_dir=exp_dir,
    )
    _write_record(exp_dir, tampered)
    with pytest.raises(ReportError, match="outside experiment directory"):
        _load(exp_dir)


def test_unreferenced_artifact_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    (exp_dir / "artifacts" / "orphan.stdout").write_bytes(b"orphan")
    with pytest.raises(ReportError, match="unreferenced artifact"):
        _load(exp_dir)


def test_identity_hash_mismatch_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    path = exp_dir / "naive" / "development" / "task-a" / "gold.json"
    record = EvaluationRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert record.patch is not None
    record.patch.diff_sha256 = "0" * 64
    path.write_bytes(serialize_record(record))
    with pytest.raises(ReportError, match="diff hash mismatch"):
        _load(exp_dir)


def test_cross_grader_pre_grade_mismatch_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    path = exp_dir / "hardened_v1" / "development" / "task-a" / "gold.json"
    record = EvaluationRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
    record.workspace.pre_grade_sha256 = "7" * 64
    path.write_bytes(serialize_record(record))
    with pytest.raises(ReportError, match="pre-grade hash mismatch"):
        _load(exp_dir)


def test_single_grader_per_patch_rejected(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _load(exp_dir)
    # add an oracle cell AND an oracle record so the plan is complete but the
    # per-patch grader set is no longer exactly {naive, hardened_v1}.
    oracle_record = _record(
        "oracle", "gold", label="valid", meta_sha=_GOLD_META, diff_sha=_GOLD_DIFF,
        pre_grade="1" * 64, pristine="2" * 64, stdout=b"oracle\n",
        artifact_rel=_artifact_path(exp_dir, "oracle", "gold", b"oracle\n"),
        project_root=_project_root(exp_dir), exp_dir=exp_dir,
    )
    _write_record(exp_dir, oracle_record)
    plan_path = exp_dir / "metadata.json"
    metadata = json.loads(plan_path.read_text(encoding="utf-8"))
    metadata["plan"]["controlled"].append(
        {**_plan_cell("oracle", "gold", meta=_GOLD_META, diff=_GOLD_DIFF), "grader": "oracle"}
    )
    plan_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    with pytest.raises(ReportError, match="exactly naive and hardened_v1"):
        _load(exp_dir)


def test_final_report_requires_confirmed_annotation(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    annotations_root = tmp_path / "results" / "annotations"
    _bind_all_annotations(exp_dir, annotations_root)
    out = tmp_path / "results" / "summaries" / "exp-001.md"
    run_report(
        project_root=_project_root(exp_dir),
        input_dir=exp_dir,
        output_path=out,
        frozen_tag="grader-v1-frozen",
        protected_tree_sha256="0" * 64,
        annotations_root=annotations_root,
    )


def test_final_report_rejects_missing_annotation(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    annotations_root = tmp_path / "results" / "annotations"
    out = tmp_path / "results" / "summaries" / "exp-001.md"
    with pytest.raises(ReportError, match="missing confirmed annotation"):
        run_report(
            project_root=_project_root(exp_dir),
            input_dir=exp_dir,
            output_path=out,
            frozen_tag="grader-v1-frozen",
            protected_tree_sha256="0" * 64,
            annotations_root=annotations_root,
        )


def test_final_report_rejects_wrong_raw_record_hash(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    annotations_root = tmp_path / "results" / "annotations"
    _bind_all_annotations(exp_dir, annotations_root)
    ann = annotations_root / EXP_ID / "task-a" / "gold.yaml"
    data = yaml.safe_load(ann.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    data["recorded_raw_record_hashes"]["naive"] = "0" * 64
    ann.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    with pytest.raises(ReportError, match="raw-record hash mismatch"):
        run_report(
            project_root=_project_root(exp_dir),
            input_dir=exp_dir,
            output_path=tmp_path / "results" / "summaries" / "exp-001.md",
            annotations_root=annotations_root,
        )


def test_incomplete_diagnostic(tmp_path: Path) -> None:
    exp_dir = _build_experiment(tmp_path)
    _load(exp_dir)
    path = exp_dir / "naive" / "development" / "task-a" / "attack.json"
    infra = _record(
        "naive", "attack", label="invalid", meta_sha=_ATK_META, diff_sha=_ATK_DIFF,
        pre_grade="1" * 64, pristine="2" * 64,
        status=OutcomeStatus.INFRASTRUCTURE_ERROR.value,
        attack_family="import-shadowing", stdout=b"infra\n",
        artifact_rel=_artifact_path(exp_dir, "naive", "attack-infra", b"infra\n"),
        project_root=_project_root(exp_dir), exp_dir=exp_dir,
    )
    path.write_bytes(serialize_record(infra))
    (exp_dir / "artifacts" / "naive-attack.stdout").unlink(missing_ok=True)
    _bind_all_annotations(exp_dir, tmp_path / "results" / "annotations")
    out = tmp_path / "results" / "summaries" / "exp-001.md"
    with pytest.raises(ReportError, match="INCOMPLETE"):
        run_report(
            project_root=_project_root(exp_dir),
            input_dir=exp_dir,
            output_path=out,
            frozen_tag="grader-v1-frozen",
            protected_tree_sha256="0" * 64,
        )
    text = out.read_text(encoding="utf-8")
    assert "## Status: INCOMPLETE" in text
    assert "No standalone primary percentages" in text
