"""CLI for API key provisioning.

Usage:
    python -m app.auth.cli create-key "NomAppli"
    python -m app.auth.cli create-key "NomAppli" --quota 50000
    python -m app.auth.cli list-keys
"""

import argparse
import asyncio
import sys

from app.auth.queries import insert_api_key
from app.auth.service import generate_api_key
from app.database import async_session, engine


async def _create_key(name: str, quota: int) -> None:
    """Generate a new API key, store its hash, and display the plaintext."""
    plaintext, key_hash, _prefix = generate_api_key()

    async with async_session() as db:
        key_id = await insert_api_key(db, key_hash, name, quota)

    print(f"Cle API creee avec succes (id={key_id})")
    print(f"Nom: {name}")
    print(f"Quota mensuel: {quota}")
    print(f"Cle: {plaintext}")
    print()
    print("ATTENTION: Cette cle ne sera plus affichee. Conservez-la en lieu sur.")


async def _list_keys() -> None:
    """Display all API keys (without hashes)."""
    from sqlalchemy import text

    async with async_session() as db:
        result = await db.execute(
            text(
                "SELECT id, name, quota_monthly, revoked, created_at"
                " FROM api_keys ORDER BY id"
            )
        )
        rows = result.fetchall()

    if not rows:
        print("Aucune cle API trouvee.")
        return

    # Header
    print(f"{'ID':<6}{'Nom':<30}{'Quota':<12}{'Revoquee':<10}{'Creee le'}")
    print("-" * 80)
    for row in rows:
        revoked_str = "Oui" if row[3] else "Non"
        created = row[4].strftime("%Y-%m-%d %H:%M")
        print(f"{row[0]:<6}{row[1]:<30}{row[2]:<12}{revoked_str:<10}{created}")


async def _run(args: argparse.Namespace) -> None:
    """Dispatch to the appropriate async handler."""
    try:
        if args.command == "create-key":
            await _create_key(args.name, args.quota)
        elif args.command == "list-keys":
            await _list_keys()
    finally:
        # Clean up the connection pool
        await engine.dispose()


def main() -> None:
    """Parse arguments and run the CLI."""
    parser = argparse.ArgumentParser(description="Gestion des cles API DataCommune")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create-key subcommand
    create_parser = subparsers.add_parser(
        "create-key", help="Creer une nouvelle cle API"
    )
    create_parser.add_argument("name", help="Nom de l'application cliente")
    create_parser.add_argument(
        "--quota",
        type=int,
        default=500,
        help="Quota mensuel de requetes (defaut: 500, plan Decouverte)",
    )

    # list-keys subcommand
    subparsers.add_parser("list-keys", help="Lister toutes les cles API")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
