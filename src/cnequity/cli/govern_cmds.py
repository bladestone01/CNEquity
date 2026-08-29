"""Contracts, profiles and snapshots — the reproducibility surface.

These are what let a published result name exactly the data it used: a
fingerprinted dataset contract, a versioned research universe, and an immutable
checksummed copy of the bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from cnequity.cli._root import cli
from cnequity.cli._shared import (
    _cfg,
    config_option,
)


def _profile_list_payload(include_compatibility: bool) -> list[dict]:
    from cnequity.domain.universe_profiles import list_universe_profiles

    return list_universe_profiles(include_compatibility=include_compatibility)


def _profile_show_payload(name: str, symbols: tuple[str, ...]) -> dict:
    from cnequity.domain.universe_profiles import (
        resolve_universe_profile,
        show_universe_profile,
    )

    try:
        payload = show_universe_profile(name)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if symbols:
        payload["concrete_scope_hash"] = resolve_universe_profile(name).symbol_scope_hash(symbols)
        payload["symbols"] = sorted(
            {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        )
    return payload


@cli.group("profile")
def profile_grp():
    """Inspect versioned research universe profiles."""


@profile_grp.command("list")
@click.option(
    "--include-compatibility/--official-only",
    default=True,
    show_default=True,
    help="Include legacy universe aliases in the registry listing.",
)
def profile_list(include_compatibility: bool):
    """List machine-readable profile registry records."""

    click.echo(
        json.dumps(_profile_list_payload(include_compatibility), ensure_ascii=False, indent=2)
    )


@profile_grp.command("show")
@click.argument("name")
@click.option(
    "--symbol",
    "symbols",
    multiple=True,
    help="Bind the profile to concrete symbols and include concrete_scope_hash.",
)
def profile_show(name: str, symbols: tuple[str, ...]):
    """Show one versioned profile and its stable scope hash."""

    click.echo(json.dumps(_profile_show_payload(name, symbols), ensure_ascii=False, indent=2))


@cli.group("contract")
def contract_grp():
    """Inspect and validate the registered dataset data contract."""


@contract_grp.command("show")
@click.argument("dataset", required=False)
@click.option(
    "--dataset",
    "dataset_option",
    default=None,
    help="Dataset name (an argument is also accepted). Omit for the full contract.",
)
@click.option(
    "--out",
    "--output",
    "--path",
    "output_path",
    default="-",
    show_default=True,
    help="Write the JSON to this path instead of stdout; '-' prints.",
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON (the default).")
def contract_show(dataset: str | None, dataset_option: str | None, output_path: str, as_json: bool):
    """Show one dataset contract, or the complete registry contract.

    `--out PATH` writes it instead of printing: that file is a contract vintage
    to commit beside a release, and it is what `cne contract diff` reads back to
    classify a later registry as compatible or breaking.
    """
    from cnequity.domain.contracts import (
        build_contract,
        contract_json,
        dataset_contract,
        export_contract,
    )

    name = dataset_option or dataset
    try:
        payload = dataset_contract(name) if name else build_contract()
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    # ``--json`` is intentionally a no-op today: JSON is the stable output
    # shape for this command. Keeping the option makes scripts explicit and
    # leaves room for a future human table without changing their invocation.
    del as_json

    if output_path == "-":
        click.echo(contract_json(payload))
        return
    if name:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(contract_json(payload) + "\n", encoding="utf-8")
    else:
        export_contract(output_path)
    click.echo(f"Wrote {output_path}")


@contract_grp.command("diff")
@click.argument("old_contract", required=False)
@click.argument("new_contract", required=False)
@click.option("--old", "old_option", default=None, help="Baseline contract path.")
@click.option("--new", "new_option", default=None, help="Candidate contract path.")
@click.option("--from", "from_option", default=None, help="Alias for --old.")
@click.option("--to", "to_option", default=None, help="Alias for --new.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
@click.option(
    "--allow-breaking",
    is_flag=True,
    help="Return exit code 0 even when breaking changes are found.",
)
def contract_diff(
    old_contract: str | None,
    new_contract: str | None,
    old_option: str | None,
    new_option: str | None,
    from_option: str | None,
    to_option: str | None,
    as_json: bool,
    allow_breaking: bool,
):
    """Compare OLD_CONTRACT with NEW_CONTRACT (default: current registry)."""
    from cnequity.domain.contracts import contract_json, diff_contracts, format_contract_diff

    old_path = old_option or from_option or old_contract
    new_path = new_option or to_option or new_contract
    if old_path is None:
        raise click.UsageError("provide OLD_CONTRACT or --old/--from")
    try:
        diff = diff_contracts(old_path, new_path)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(contract_json(diff))
    else:
        click.echo(format_contract_diff(diff))
    if diff["is_breaking"] and not allow_breaking:
        raise SystemExit(1)


@contract_grp.command("validate")
@click.argument("contract_path", required=False)
@click.option(
    "--path", "path_option", default=None, help="Contract JSON path (an argument is also accepted)."
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
@click.option(
    "--against-registry",
    is_flag=True,
    help="Require a file contract to match the current DATASETS/SCHEMAS/PRIMARY_KEYS exactly.",
)
def contract_validate(
    contract_path: str | None,
    path_option: str | None,
    as_json: bool,
    against_registry: bool,
):
    """Validate a contract file, or the current registry when omitted."""
    from cnequity.domain.contracts import contract_json, validate_contract

    contract_path = path_option or contract_path
    errors = validate_contract(
        contract_path,
        against_registry=True if (contract_path is None or against_registry) else False,
    )
    if as_json:
        click.echo(contract_json({"valid": not errors, "errors": errors}))
    elif errors:
        for error in errors:
            click.echo(f"ERROR: {error}", err=True)
    else:
        click.echo("Contract OK")
    if errors:
        raise SystemExit(1)


@cli.group("snapshot")
def snapshot_grp():
    """Create, verify and safely restore portable lake snapshots."""


@snapshot_grp.command("create")
@click.argument("name")
@click.option(
    "--dataset",
    "datasets",
    multiple=True,
    required=True,
    help="Dataset to include (repeatable). A snapshot is explicit, never the whole lake.",
)
@config_option
@click.option(
    "--snapshot-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Where snapshots live; default is meta/snapshots under the data root.",
)
def snapshot_create(
    name: str, datasets: tuple[str, ...], config_path: str, snapshot_root: Path | None
):
    """Freeze the named datasets into a new immutable snapshot.

    The manifest records every Parquet file's size and SHA-256 alongside the
    dataset state, the contract fingerprint and the run lineage — enough for a
    reader to prove later that a published result used exactly these bytes.
    Prints the manifest path.
    """
    from cnequity.storage.snapshots import SnapshotStore

    manifest = SnapshotStore(_cfg(config_path), snapshot_root).create(name, list(datasets))
    click.echo(str(manifest))


@snapshot_grp.command("verify")
@click.argument("name")
@config_option
@click.option(
    "--snapshot-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Where snapshots live; default is meta/snapshots under the data root.",
)
def snapshot_verify(name: str, config_path: str, snapshot_root: Path | None):
    """Re-hash every file in the snapshot against its manifest.

    Exits 1 on the first size or digest mismatch, so it works as a gate in a
    scheduled job. Run it before trusting a snapshot you did not just create —
    bit rot and a truncated copy look identical until the hashes disagree.
    """
    from dataclasses import asdict

    from cnequity.storage.snapshots import SnapshotStore

    result = SnapshotStore(_cfg(config_path), snapshot_root).verify(name)
    click.echo(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    if not result.passed:
        raise SystemExit(1)


@snapshot_grp.command("restore")
@click.argument("name")
@click.argument("target", type=click.Path(path_type=Path))
@config_option
@click.option(
    "--snapshot-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Where snapshots live; default is meta/snapshots under the data root.",
)
def snapshot_restore(name: str, target: Path, config_path: str, snapshot_root: Path | None):
    """Restore a snapshot into TARGET, which must be new or empty.

    An active lake root is refused and an existing file is never overwritten:
    restoring is how you inspect an old vintage beside the current one, not how
    you roll the live lake back. Check the result with
    `cne status --datasets` against TARGET before pointing anything at it.
    """
    from cnequity.storage.snapshots import SnapshotStore

    restored = SnapshotStore(_cfg(config_path), snapshot_root).restore(name, target)
    click.echo(str(restored))
