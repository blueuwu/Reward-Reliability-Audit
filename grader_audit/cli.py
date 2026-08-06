"""grader-audit command line interface.

Gates 1-3 implement ``doctor``, ``validate-manifests``, ``validate``,
``run-controlled``, ``build-images``, and ``label-patches`` per Section 27.15.
Gate 4 adds ``freeze``; later gates add ``run-heldout``, ``report``, and
``reproduce``.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from grader_audit.core.annotations import (
    AnnotationMismatchError,
    MissingAnnotationError,
    require_confirmed_annotation,
)
from grader_audit.core.docker_runner import DockerRunner
from grader_audit.core.doctor import DoctorReport, run_doctor
from grader_audit.core.freeze import FreezeError, run_freeze
from grader_audit.core.labeling import LabelingEvidence, label_task
from grader_audit.core.manifests import LoadedTask, discover_patches, discover_tasks
from grader_audit.core.models import PatchSplit, Split
from grader_audit.core.orchestrator import (
    check_development_corpus_minimums,
    check_task_corpus,
    plan_metadata,
    prepare_task,
    run_controlled,
    run_validation,
)
from grader_audit.core.recorder import ExperimentRecorder, validate_experiment_id
from grader_audit.images import build_task_image, resolve_task_image

app = typer.Typer(
    name="grader-audit",
    help=(
        "Audit reward reliability of coding-task graders in HUD environments. "
        "Implements the contract in CODEX_TASK_HUD_GRADER_RELIABILITY_AUDIT.md."
    ),
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

console = Console()

_EXIT_OK = 0
_EXIT_USAGE = 2
_EXIT_VALIDATION = 3
_EXIT_INFRA = 4
_EXIT_FREEZE = 5

_LABELING_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,63}$"


@app.callback()
def main() -> None:
    """Audit reward reliability of coding-task graders in HUD environments.

    Implements the contract in CODEX_TASK_HUD_GRADER_RELIABILITY_AUDIT.md.
    """


def _render_report(report: DoctorReport) -> None:
    table = Table(title="grader-audit doctor", show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status", justify="center")
    table.add_column("Detail")
    for check in report.checks:
        status = "[green]PASS[/green]" if check.ok else "[red]FAIL[/red]"
        table.add_row(check.description, status, check.detail)
    console.print(table)


@app.command()
def doctor() -> None:
    """Check the Section 27.1 prerequisites for the controlled audit."""
    report = run_doctor(Path.cwd())
    _render_report(report)
    if report.all_ok:
        console.print("[bold green]doctor: all prerequisites satisfied[/bold green]")
        raise typer.Exit(_EXIT_OK)
    console.print("[bold red]doctor: prerequisites not satisfied; exit 4[/bold red]")
    raise typer.Exit(_EXIT_INFRA)


def _split_enum(value: str) -> Split:
    try:
        return Split(value)
    except ValueError:
        raise typer.BadParameter(
            f"split must be one of {[item.value for item in Split]}, not {value!r}"
        ) from None


def _split_enum_or_all(value: str) -> str:
    if value == "all":
        return value
    _split_enum(value)
    return value


def _load_task_or_exit(tasks_dir: Path) -> list[LoadedTask]:
    try:
        return discover_tasks(tasks_dir)
    except FileNotFoundError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(_EXIT_USAGE) from None


def _corpus_check_or_exit(tasks: list[LoadedTask]) -> None:
    any_errors = False
    for task in tasks:
        errors = check_task_corpus(task)
        if errors:
            any_errors = True
            console.print(f"[bold red]{task.manifest.id}:[/bold red]")
            for error in errors:
                console.print(f"  - {error}")
    if any_errors:
        raise typer.Exit(_EXIT_VALIDATION)


@app.command("validate-manifests")
def validate_manifests_cmd(
    tasks_dir: Path = typer.Argument(..., help="Directory of task corpora"),
    require_minimums: bool = typer.Option(
        False, "--require-minimums", help="Also enforce the Section 27.5 corpus minimums"
    ),
) -> None:
    """Validate manifest schema, cross-references, and patch application (no Docker)."""
    tasks = _load_task_or_exit(tasks_dir)
    _corpus_check_or_exit(tasks)
    if require_minimums:
        errors = check_development_corpus_minimums(tasks)
        if errors:
            console.print("[bold red]corpus minimums not satisfied:[/bold red]")
            for error in errors:
                console.print(f"  - {error}")
            raise typer.Exit(_EXIT_VALIDATION)
    console.print(f"[bold green]validate-manifests: {len(tasks)} task(s) valid[/bold green]")
    raise typer.Exit(_EXIT_OK)


def _new_runner() -> DockerRunner:
    return DockerRunner()


def _resolve_task_image_or_exit(task: LoadedTask) -> str:
    try:
        return resolve_task_image(task)
    except FileNotFoundError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(_EXIT_USAGE) from None


@app.command()
def validate(
    tasks_dir: Path = typer.Argument(..., help="Directory of task corpora"),
    split: str = typer.Option(
        ..., "--split", callback=_split_enum, help="development | frozen_eval"
    ),
    repeat: int = typer.Option(3, "--repeat", min=1, help="Repeat count for baseline/gold checks"),
    experiment_id: str = typer.Option("", "--experiment-id", help="Result experiment id"),
    results_root: Path = typer.Option(
        Path("results"), "--results-root", help="Results root directory"
    ),
) -> None:
    """Run baseline and gold from clean workspaces for the requested repeat count."""
    split_enum = Split(split)
    exp_id = experiment_id or f"validate-{split_enum.value}-r{repeat}"
    try:
        validate_experiment_id(exp_id)
    except ValueError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(_EXIT_USAGE) from None
    tasks = _load_task_or_exit(tasks_dir)
    tasks = [task for task in tasks if task.manifest.split is split_enum]
    if not tasks:
        console.print(
            f"[bold red]error:[/bold red] no tasks with split {split_enum.value!r} in {tasks_dir}"
        )
        raise typer.Exit(_EXIT_USAGE) from None
    _corpus_check_or_exit(tasks)

    runner = _new_runner()
    recorder = ExperimentRecorder(results_root, exp_id)
    for task in tasks:
        image = _resolve_task_image_or_exit(task)
        console.print(f"[bold]{task.manifest.id}:[/bold] validating baseline/gold x{repeat} ...")
        summary = run_validation(
            task,
            repeat=repeat,
            recorder=recorder,
            runner=runner,
            image=image,
            project_root=Path.cwd(),
            split=split_enum.value,
        )
        for record in summary.records:
            case = record.validation_case
            status = "stable" if record.stable else "UNSTABLE"
            console.print(f"  - {case} repeat {record.repeat_index}: {status}")
        if not summary.stable:
            console.print(f"[bold red]{task.manifest.id}: baseline/gold not stable[/bold red]")
            for error in summary.errors:
                console.print(f"  - {error}")
            raise typer.Exit(_EXIT_VALIDATION)
    console.print(f"[bold green]validate: all {len(tasks)} task(s) stable[/bold green]")
    raise typer.Exit(_EXIT_OK)


def _parse_graders(value: str) -> list[str]:
    graders = [item.strip() for item in value.split(",") if item.strip()]
    allowed = {"naive", "hardened_v1"}
    if not graders:
        raise typer.BadParameter("--graders must be non-empty")
    for grader in graders:
        if grader not in allowed:
            raise typer.BadParameter(
                f"unsupported grader {grader!r}; choose from {sorted(allowed)}"
            )
    return graders


@app.command("run-controlled")
def run_controlled_cmd(
    tasks_dir: Path = typer.Option(..., "--tasks", help="Directory of task corpora"),
    graders: str = typer.Option("naive,hardened_v1", "--graders"),
    experiment_id: str = typer.Option(..., "--experiment-id"),
    results_root: Path = typer.Option(
        Path("results"), "--results-root", help="Results root directory"
    ),
) -> None:
    """Run all development patches under each requested grader from clean workspaces."""
    try:
        validate_experiment_id(experiment_id)
    except ValueError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(_EXIT_USAGE) from None
    tasks = _load_task_or_exit(tasks_dir)
    tasks = [task for task in tasks if task.manifest.split is Split.DEVELOPMENT]
    if not tasks:
        console.print("[bold red]error:[/bold red] no development tasks found")
        raise typer.Exit(_EXIT_USAGE)
    _corpus_check_or_exit(tasks)

    # Refuse held-out assets in run-controlled per Section 27.15.
    for task in tasks:
        all_patches = discover_patches(task.task_dir, PatchSplit.DEVELOPMENT) + discover_patches(
            task.task_dir, PatchSplit.FROZEN_EVAL
        )
        if any(patch.manifest.split is PatchSplit.FROZEN_EVAL for patch in all_patches):
            console.print(
                f"[bold red]error:[/bold red] {task.manifest.id} contains frozen-eval patches; "
                "run-controlled selects development only"
            )
            raise typer.Exit(_EXIT_USAGE) from None

    # Every patch must have a confirmed truth annotation whose recorded hashes
    # match (Section 27.15) before any controlled evaluation.
    for task in tasks:
        for patch in discover_patches(task.task_dir, PatchSplit.DEVELOPMENT):
            try:
                require_confirmed_annotation(results_root, experiment_id, patch)
            except (MissingAnnotationError, AnnotationMismatchError) as exc:
                console.print(f"[bold red]error:[/bold red] {exc}")
                raise typer.Exit(_EXIT_USAGE) from None

    runner = _new_runner()
    recorder = ExperimentRecorder(results_root, experiment_id)
    invalid_input = 0
    infra = 0
    grader_list = _parse_graders(graders)
    for task in tasks:
        task_image = _resolve_task_image_or_exit(task)
        console.print(f"[bold]{task.manifest.id}:[/bold] evaluating development patches ...")
        prepare_task(task)
        records = run_controlled(
            task,
            recorder=recorder,
            runner=runner,
            image=task_image,
            project_root=Path.cwd(),
            graders=grader_list,
        )
        for record in records:
            patch_id = record.patch.id if record.patch is not None else "?"
            status = record.status
            if status == "invalid_input":
                invalid_input += 1
            elif status == "infrastructure_error":
                infra += 1
            console.print(
                f"  - {record.grader.name} {patch_id}: {status} "
                f"reward={record.result.reward} reasons={record.result.reason_codes}"
            )
    recorder.write_metadata(
        plan_metadata(
            experiment_id=experiment_id, project_root=Path.cwd(), tasks=tasks, graders=grader_list
        )
    )
    if invalid_input:
        raise typer.Exit(_EXIT_USAGE)
    if infra:
        raise typer.Exit(_EXIT_INFRA)
    console.print(f"[bold green]run-controlled: experiment {experiment_id} complete[/bold green]")
    raise typer.Exit(_EXIT_OK)


@app.command("build-images")
def build_images_cmd(
    tasks_dir: Path = typer.Argument(..., help="Directory of task corpora"),
    split: str = typer.Option(
        "all", "--split", callback=_split_enum_or_all, help="development | frozen_eval | all"
    ),
) -> None:
    """Build per-task immutable images and write image.lock.json (Sections 27.6/27.11)."""
    tasks = _load_task_or_exit(tasks_dir)
    if split != "all":
        split_value = Split(split)
        tasks = [task for task in tasks if task.manifest.split is split_value]
    if not tasks:
        console.print(f"[bold red]error:[/bold red] no tasks with split {split!r} in {tasks_dir}")
        raise typer.Exit(_EXIT_USAGE)
    _corpus_check_or_exit(tasks)
    for task in tasks:
        console.print(f"[bold]{task.manifest.id}:[/bold] building task image ...")
        try:
            digest = build_task_image(task)
        except Exception as exc:
            console.print(f"[bold red]{task.manifest.id}: image build failed:[/bold red] {exc}")
            raise typer.Exit(_EXIT_INFRA) from None
        console.print(f"  digest: {digest}")
    console.print(f"[bold green]build-images: {len(tasks)} image(s) ready[/bold green]")
    raise typer.Exit(_EXIT_OK)


def _write_labeling_evidence(
    results_root: Path,
    labeling_id: str,
    split: str,
    task_id: str,
    patch_id: str,
    evidence: LabelingEvidence,
) -> Path:
    target = (
        results_root
        / "labeling"
        / labeling_id
        / split
        / task_id
        / f"{patch_id}.json"
    )
    if target.exists():
        raise typer.Exit(_EXIT_USAGE)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_bytes(evidence.to_json_bytes())
    os.replace(tmp, target)
    return target


def _write_draft_annotation(
    results_root: Path,
    labeling_id: str,
    task_id: str,
    patch_id: str,
    draft: Mapping[str, object],
) -> Path:
    target = results_root / "labeling" / labeling_id / "annotations" / task_id / f"{patch_id}.yaml"
    if target.exists():
        raise typer.Exit(_EXIT_USAGE)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_text(
        yaml.safe_dump(draft, sort_keys=True).replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, target)
    return target


@app.command("label-patches")
def label_patches_cmd(
    tasks_dir: Path = typer.Argument(..., help="Directory of task corpora"),
    split: str = typer.Option(..., "--split", callback=_split_enum),
    labeling_id: str = typer.Option(..., "--labeling-id"),
    results_root: Path = typer.Option(
        Path("results"), "--results-root", help="Results root directory"
    ),
) -> None:
    """Run oracle+authoritative labeling evidence and write draft annotations (27.9/27.15)."""
    if not re.fullmatch(_LABELING_ID_PATTERN, labeling_id):
        console.print(
            f"[bold red]error:[/bold red] labeling-id must match {_LABELING_ID_PATTERN}"
        )
        raise typer.Exit(_EXIT_USAGE)
    tasks = _load_task_or_exit(tasks_dir)
    split_value = Split(split)
    tasks = [task for task in tasks if task.manifest.split is split_value]
    if not tasks:
        console.print(f"[bold red]error:[/bold red] no tasks with split {split!r} in {tasks_dir}")
        raise typer.Exit(_EXIT_USAGE)
    _corpus_check_or_exit(tasks)

    runner = _new_runner()
    confirmed = 0
    needs_review = 0
    for task in tasks:
        image = _resolve_task_image_or_exit(task)
        console.print(f"[bold]{task.manifest.id}:[/bold] labeling patches ...")
        for patch, evidence in label_task(
            task, runner=runner, image=image, labeling_id=labeling_id
        ):
            _write_labeling_evidence(
                results_root,
                labeling_id,
                split_value.value,
                task.manifest.id,
                patch.manifest.id,
                evidence,
            )
            draft = evidence.draft_annotation
            _write_draft_annotation(
                results_root, labeling_id, task.manifest.id, patch.manifest.id, draft
            )
            if draft["disposition"] == "confirmed":
                confirmed += 1
            else:
                needs_review += 1
            console.print(
                f"  - {patch.manifest.id}: oracle_passed={evidence.oracle.get('passed')} "
                f"draft={draft['disposition']}"
            )
    console.print(
        f"[bold green]label-patches: {len(tasks)} task(s), {confirmed} confirmed drafts, "
        f"{needs_review} for review[/bold green]"
    )
    raise typer.Exit(_EXIT_OK)


@app.command()
def freeze(
    grader: str = typer.Option(..., "--grader", help="Grader version to freeze (hardened_v1)"),
    git_tag: str = typer.Option(..., "--git-tag", help="Annotated tag to create"),
    tasks_dir: Path = typer.Option(
        Path("tasks"), "--tasks", help="Directory of task corpora"
    ),
    results_root: Path = typer.Option(
        Path("results"), "--results-root", help="Results root directory"
    ),
) -> None:
    """Freeze hardened v1 (Section 27.14): lock-only commit plus annotated tag.

    Refuses to mutate the repository when any precondition fails (existing tag,
    dirty worktree, missing Git author, failed quality/preconditions, held-out
    content, or development result/annotation inconsistency). Commits ONLY
    ``freeze/grader_v1.lock.json`` with message ``Freeze hardened grader v1``
    and creates the annotated tag ``grader-v1-frozen`` on that commit.
    """
    try:
        result = run_freeze(
            project_root=Path.cwd(),
            grader=grader,
            git_tag=git_tag,
            tasks_dir=tasks_dir,
            results_root=results_root,
        )
    except FreezeError as exc:
        console.print("[bold red]freeze refused:[/bold red]")
        console.print(str(exc))
        raise typer.Exit(_EXIT_FREEZE) from None
    console.print(f"[bold green]freeze: {grader} frozen at {git_tag}[/bold green]")
    console.print(f"  source HEAD    : {result.source_head_sha}")
    console.print(f"  freeze commit  : {result.freeze_commit_sha}")
    console.print(f"  tag object     : {result.tag_object_sha}")
    console.print(f"  tag commit     : {result.tag_commit_sha}")
    console.print(f"  tag tree       : {result.tag_tree_sha}")
    console.print(f"  controlled exps: {', '.join(result.controlled_experiments)}")
    console.print(f"  validation exps: {', '.join(result.validation_experiments)}")
    console.print(f"  protected tree : {result.protected_tree_sha256}")
    console.print(f"  result set     : {result.development_result_set_sha256}")
    console.print(f"  lock           : {result.lock_path}")
    raise typer.Exit(_EXIT_OK)
