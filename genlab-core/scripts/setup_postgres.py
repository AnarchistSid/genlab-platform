#!/usr/bin/env python3
"""One-time PostgreSQL setup for GenLab storage backend.

Creates the database and role needed for the PostgresBackend.
Run: uv run --package genlab-core python genlab-core/scripts/setup_postgres.py
"""
import subprocess
import sys


def run(cmd: list[str], check: bool = False) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def main():
    print("=== GenLab PostgreSQL Setup ===\n")

    # 1. Create database
    code, out = run(["createdb", "genlab"])
    if code == 0:
        print("[OK] Database 'genlab' created")
    else:
        print(f"[SKIP] Database 'genlab' already exists or error: {out.strip()}")

    # 2. Create role
    code, out = run(
        ["psql", "genlab", "-c",
         "CREATE ROLE genlab WITH LOGIN PASSWORD 'genlab_dev';"]
    )
    if code == 0:
        print("[OK] Role 'genlab' created")
    else:
        print(f"[SKIP] Role 'genlab' already exists or error: {out.strip()}")

    # 3. Grant privileges
    code, out = run(
        ["psql", "genlab", "-c",
         "GRANT ALL PRIVILEGES ON DATABASE genlab TO genlab;"]
    )
    if code == 0:
        print("[OK] Granted privileges to 'genlab'")
    else:
        print(f"[WARN] Grant failed: {out.strip()}")

    # 4. Set owner
    code, out = run(
        ["psql", "genlab", "-c",
         "ALTER DATABASE genlab OWNER TO genlab;"]
    )
    if code == 0:
        print("[OK] Set database owner to 'genlab'")
    else:
        print(f"[WARN] Owner change failed: {out.strip()}")

    # 5. Verify
    code, out = run(["psql", "genlab", "-c", "SELECT version();"])
    if code == 0:
        for line in out.strip().split("\n"):
            if "PostgreSQL" in line:
                print(f"\n[OK] PostgreSQL version: {line.strip()}")
                break
    else:
        print(f"[ERROR] Cannot connect to database: {out.strip()}")
        sys.exit(1)

    print("\nSetup complete.")


if __name__ == "__main__":
    main()
