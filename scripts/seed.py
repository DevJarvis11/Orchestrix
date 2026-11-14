"""Seed demo users, the showcase lesson memory and demo_data tables (SPEC 5.6, `make seed`).

Idempotent: safe to run repeatedly. Dev-mode users mirror the Keycloak realm export
(infra/keycloak/orchestrix-realm.json); with Keycloak, users are provisioned on first login.
"""

import asyncio
import csv
from pathlib import Path

import httpx
import psycopg
from psycopg import sql

from orchestrix_core.config import get_settings, libpq_dsn
from orchestrix_core.db.repos.dev_logins import DevLoginRepo
from orchestrix_core.db.repos.tasks import UserRepo
from orchestrix_core.db.session import create_engine, create_sessionmaker
from orchestrix_memory.clients import EmbeddingUnavailable, GatewayEmbedder
from orchestrix_memory.repo import MemoryRepo, memory_id, team_ns

ROOT = Path(__file__).parents[1]
CSV = ROOT / "tests" / "fixtures" / "showcase" / "query_latency.csv"
USERS = (  # email, team(s)
    ("alice@orchestrix.local", ["platform"]),
    ("bob@orchestrix.local", ["platform"]),
    ("carol@orchestrix.local", ["research"]),
    ("dave@orchestrix.local", ["research"]),
    ("oscar@orchestrix.local", ["platform", "research"]),
    ("ada@orchestrix.local", ["platform"]),
    ("demo@orchestrix.local", ["demo"]),
    ("admin@orchestrix.local", ["platform", "research"]),
)
LOGINS = (  # username, email, password, roles: dev-mode password logins (demo credentials)
    ("admin", "admin@orchestrix.local", "admin", ["requester", "reviewer", "operator", "admin"]),
    ("alice", "alice@orchestrix.local", "alice@123", ["requester"]),
    ("bob", "bob@orchestrix.local", "bob@123", ["requester", "reviewer"]),
)
LESSON = "Benchmarks need the vendor's own source plus one independent source."


async def seed_users_and_memory() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_sessionmaker(engine)
    users = UserRepo(sessions)
    for email, teams in USERS:
        user = await users.upsert(f"dev|{email}", email, email.split("@")[0])
        for team in teams:
            await users.ensure_team_member(user.id, team)
        print(f"user {email} -> {', '.join(teams)}")
    logins = DevLoginRepo(sessions)
    for username, email, password, roles in LOGINS:
        user = await users.upsert(f"dev|{email}", email, username)
        await logins.set_password(username, user.id, password, roles)
        print(f"login {username} ({', '.join(roles)})")
    platform = await users.ensure_team_member(
        (await users.upsert("dev|bob@orchestrix.local", "bob@orchestrix.local", "bob")).id,
        "platform",
    )
    async with httpx.AsyncClient() as http:
        embedder = GatewayEmbedder.from_settings(http, settings)
        try:
            vector: list[float] | None = (await embedder.embed([LESSON]))[0]
        except EmbeddingUnavailable:
            vector = None
            print("embedding endpoint unreachable (VPN off?): the lesson is stored unembedded")
    created = await MemoryRepo(sessions).insert(
        id_=memory_id("seed", "lesson", LESSON),
        namespace=team_ns(platform.id),
        kind="lesson",
        text_=LESSON,
        importance=0.9,
        embedding=vector,
        source_run_id=None,
        source_ref="seed",
    )
    print("seeded lesson" if created else "lesson already seeded")
    await engine.dispose()


def seed_demo_data() -> None:
    """demo_data is read by data.sql_query; DDL needs the migrator role."""
    dsn = libpq_dsn(get_settings().migrations_url)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS demo_data.query_latency ("
            "date date NOT NULL, database text NOT NULL, query_type text NOT NULL, "
            "p50_ms real NOT NULL, p95_ms real NOT NULL, qps integer NOT NULL, "
            "PRIMARY KEY (date, database, query_type))"
        )
        with CSV.open() as f:
            rows = [
                (r["date"], r["database"], r["query_type"], r["p50_ms"], r["p95_ms"], r["qps"])
                for r in csv.DictReader(f)
            ]
        cur.executemany(
            sql.SQL(
                "INSERT INTO demo_data.query_latency VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING"
            ),
            rows,
        )
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA demo_data TO orchestrix_readonly")
    print(f"demo_data.query_latency: {len(rows)} rows")


def main() -> None:
    asyncio.run(seed_users_and_memory())
    seed_demo_data()


if __name__ == "__main__":
    main()
