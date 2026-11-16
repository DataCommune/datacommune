"""Export schema metadata only. Supply TBLS_DSN through the environment.

Run with the backend Python environment (psycopg required) and tbls on PATH,
or pass --tbls /path/to/tbls. No application records are queried.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/database"
OBSERVATION = ROOT / ".planning/codebase/database-observation.json"
METADATA_SQL = """
SELECT json_build_object(
  'observed_at', current_timestamp,
  'server_version', current_setting('server_version'),
  'read_only', current_setting('transaction_read_only'),
  'alembic_versions', (SELECT json_agg(version_num) FROM alembic_version),
  'extensions', (SELECT json_object_agg(extname, extversion) FROM pg_extension),
  'partitions', (SELECT json_agg(json_build_object(
      'child',cn.nspname||'.'||c.relname,'parent',pn.nspname||'.'||p.relname))
    FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid
    JOIN pg_class p ON p.oid=i.inhparent
    JOIN pg_namespace cn ON cn.oid=c.relnamespace
    JOIN pg_namespace pn ON pn.oid=p.relnamespace
    WHERE cn.nspname='public' AND c.relkind IN ('r','p')),
  'application_functions', (SELECT json_agg(DISTINCT n.nspname||'.'||p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND NOT EXISTS (
      SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass
      AND d.objid=p.oid AND d.deptype='e')),
  'commune_synthesis_exists', to_regclass('public.v_commune_synthese') IS NOT NULL
)
"""


def readonly_dsn(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"postgres", "postgresql"} or not parts.hostname:
        raise ValueError("TBLS_DSN must be a PostgreSQL URL")
    query = dict(parse_qsl(parts.query))
    # Override caller options so every connection opened by tbls is read-only.
    query.pop("options", None)
    query.update(
        default_transaction_read_only="on",
        statement_timeout="30000",
        connect_timeout="8",
    )
    return urlunsplit(parts._replace(query=urlencode(query)))


def run_tbls(binary: str, args: list[str], env: dict[str, str]) -> None:
    result = subprocess.run([binary, *args], cwd=ROOT, env=env, capture_output=True)
    if result.returncode:
        # Driver diagnostics can contain connection URLs; never forward them.
        raise RuntimeError(
            f"tbls failed (exit {result.returncode}); no credentials logged"
        )


def publish(staged: Path) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    previous = OUTPUT / "schema.json"
    old_tables = (
        json.loads(previous.read_text(encoding="utf-8"))["tables"]
        if previous.exists()
        else []
    )
    # Remove only stale table documents recorded in our preceding generated schema.
    generated = {p.name for p in staged.iterdir() if p.is_file()}
    for table in old_tables:
        for suffix in (".md", ".mermaid"):
            old = (OUTPUT / (table["name"] + suffix)).resolve()
            if old.parent != OUTPUT.resolve():
                raise ValueError("Invalid generated table path")
            if old.name not in generated and old.is_file():
                old.unlink()
    for path in staged.iterdir():
        if path.is_file():
            shutil.copyfile(path, OUTPUT / path.name)


def normalize_sql_links(staged: Path) -> list[dict[str, str]]:
    unresolved = []
    for document in staged.glob("*.md"):

        def replace(match: re.Match[str]) -> str:
            label, target = match.groups()
            if (
                not target.endswith(".md")
                or "://" in target
                or (staged / target).is_file()
            ):
                return match.group(0)
            # SQL parsers also report CTE aliases and system catalogs as dependencies.
            unresolved.append({"document": document.name, "target": target})
            return label

        text = document.read_text(encoding="utf-8")
        document.write_text(
            re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace, text), encoding="utf-8"
        )
    return sorted(unresolved, key=lambda item: (item["document"], item["target"]))


def export(binary: str) -> None:
    dsn = readonly_dsn(os.environ["TBLS_DSN"])
    env = dict(os.environ, TBLS_DSN=dsn)
    parts = urlsplit(dsn)
    connection_query = dict(parse_qsl(parts.query))
    connection_query.pop("default_transaction_read_only", None)
    connection_query.pop("statement_timeout", None)
    connection_dsn = urlunsplit(parts._replace(query=urlencode(connection_query)))
    with psycopg.connect(
        connection_dsn,
        options="-cdefault_transaction_read_only=on -cstatement_timeout=30000",
    ) as conn:
        observation = conn.execute(METADATA_SQL).fetchone()[0]
        assert observation["read_only"] == "on"
        observation["partitions"] = sorted(
            observation["partitions"] or [],
            key=lambda item: (item["parent"], item["child"]),
        )
        observation["application_functions"] = sorted(
            observation["application_functions"] or []
        )
        observation["extensions"] = dict(sorted(observation["extensions"].items()))
        version = subprocess.run(
            [binary, "version"], capture_output=True, text=True, check=True
        )
        observation["tbls_version"] = version.stdout.strip()
        with tempfile.TemporaryDirectory(prefix="datacommune-schema-") as temporary:
            scratch = Path(temporary).resolve()
            raw = scratch / "physical.json"
            # tbls out can reuse docPath/schema.json. A fresh path forces live introspection.
            live_env = dict(env, TBLS_DOC_PATH=str(scratch / "fresh-live-docs"))
            run_tbls(
                binary,
                [
                    "out",
                    "--config",
                    ".tbls.yml",
                    "--sort",
                    "-t",
                    "json",
                    "-o",
                    str(raw),
                ],
                live_env,
            )
            schema = json.loads(raw.read_text(encoding="utf-8"))
            observation["physical_objects"] = len(schema["tables"])
            children = {p["child"] for p in observation["partitions"] or []}
            if not children <= {table["name"] for table in schema["tables"]}:
                raise RuntimeError(
                    "Live schema is missing catalog partitions; refusing cached output"
                )
            schema["tables"] = [
                t for t in schema["tables"] if t["name"] not in children
            ]
            schema["relations"] = [
                r
                for r in schema["relations"]
                if r["table"] not in children and r["parent_table"] not in children
            ]
            functions = set(observation["application_functions"] or [])
            schema["functions"] = [
                f for f in schema.get("functions", []) if f["name"] in functions
            ]
            observation["logical_objects"] = len(schema["tables"])
            observation["declared_foreign_keys"] = len(schema["relations"])
            filtered = scratch / "logical.json"
            filtered.write_text(
                json.dumps(schema, ensure_ascii=False), encoding="utf-8"
            )
            staged = scratch / "docs"
            # JSON input regenerates diagrams offline, without another DB query.
            offline = dict(env, TBLS_DSN="json://" + filtered.as_posix())
            run_tbls(
                binary,
                [
                    "doc",
                    "--config",
                    ".tbls.yml",
                    "--sort",
                    offline["TBLS_DSN"],
                    str(staged),
                ],
                offline,
            )
            revisions = [
                row[0]
                for row in conn.execute("SELECT version_num FROM alembic_version")
            ]
            if sorted(revisions) != sorted(observation["alembic_versions"]):
                raise RuntimeError(
                    "Migration changed during export; rerun the schema export"
                )
            observation["unresolved_sql_references"] = normalize_sql_links(staged)
            publish(staged)
    OBSERVATION.parent.mkdir(parents=True, exist_ok=True)
    OBSERVATION.write_text(
        json.dumps(observation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Exported {observation['logical_objects']} logical objects; {len(children)} partitions recorded separately."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tbls", default="tbls")
    args = parser.parse_args()
    try:
        export(args.tbls)
    except Exception as error:
        # Even connection errors may disclose the password embedded in the URL.
        raise SystemExit(
            f"Schema export failed ({type(error).__name__}); verify TBLS_DSN, connection and tbls."
        ) from None


if __name__ == "__main__":
    main()
