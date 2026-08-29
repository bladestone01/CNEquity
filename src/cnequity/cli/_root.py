"""The `cne` command group itself.

It lives apart from `main` so command modules can hang themselves off it
(`@cli.command()`) while `main` imports those modules to register them. Putting
the group in `main` instead makes that a cycle.
"""

from __future__ import annotations

import click


@click.group()
@click.version_option(package_name="cnequity")
def cli():
    """cnequity — A-share data ingestion CLI."""
