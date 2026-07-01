"""Capture the current database schema as an initial migration."""
import sys
from pathlib import Path

# Add src to path
SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from database import get_connection

SCHEMA_MIGRATION_FILE = SRC / "migrations" / "001_initial_schema.py"


def capture_current_schema():
    """Dump all CREATE TABLE statements from the current database."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL AND name != 'schema_versions' ORDER BY name"
    ).fetchall()

    statements = []
    for row in rows:
        stmt = row[0]
        if stmt:
            statements.append(stmt)

    stmts_joined = "\n\n".join(statements)
    table_names = []
    for s in statements:
        if "CREATE TABLE" in s:
            try:
                name = s.split("CREATE TABLE IF NOT EXISTS ")[-1].split("(")[0].strip()
                table_names.append(name)
            except Exception:
                pass

    down_stmts = ";\nDROP TABLE IF EXISTS ".join(table_names)

    migration_content = f'''"""
Migration 001: Initial schema capture.
Generated from current database state.
"""
UP_SQL = """\\
{stmts_joined}
"""
DOWN_SQL = """\\
DROP TABLE IF EXISTS {down_stmts};
"""
'''
    # Write migration file
    (SRC / "migrations").mkdir(parents=True, exist_ok=True)
    (SCHEMA_MIGRATION_FILE).write_text(migration_content)
    print(f"Schema captured to {SCHEMA_MIGRATION_FILE}")
    print(f"Found {len(statements)} tables")


if __name__ == "__main__":
    capture_current_schema()
