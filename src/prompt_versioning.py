import uuid
import datetime
import json
import sqlite3
from typing import Optional

from database import get_connection


def _new_id() -> str:
    return "pv-" + uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S."
    ) + f"{datetime.datetime.now(datetime.timezone.utc).microsecond:06d}Z"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS prompt_versions (
    id              TEXT PRIMARY KEY,
    step_id         TEXT NOT NULL,
    version         INTEGER NOT NULL,
    prompt_template TEXT NOT NULL DEFAULT '',
    rendered_prompt TEXT DEFAULT '',
    run_id          TEXT DEFAULT '',
    context_data    TEXT DEFAULT '{}',
    output_data     TEXT DEFAULT '{}',
    tokens_input    INTEGER DEFAULT 0,
    tokens_output   INTEGER DEFAULT 0,
    model_used      TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_step ON prompt_versions(step_id);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_step_version ON prompt_versions(step_id, version);
"""


class PromptVersionManager:
    def ensure_schema(self) -> None:
        conn = get_connection()
        conn.executescript(CREATE_TABLE_SQL)
        conn.commit()

    def record_version(
        self,
        step_id: str,
        prompt_template: str,
        rendered_prompt: str = "",
        run_id: str = "",
        context_data: str = "{}",
        output_data: str = "{}",
        tokens_input: int = 0,
        tokens_output: int = 0,
        model_used: str = "",
        notes: str = "",
    ) -> dict:
        conn = get_connection()
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS max_ver FROM prompt_versions WHERE step_id = ?",
            (step_id,),
        ).fetchone()
        next_version = (row["max_ver"] if row else 0) + 1

        new_id = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO prompt_versions
               (id, step_id, version, prompt_template, rendered_prompt, run_id,
                context_data, output_data, tokens_input, tokens_output, model_used, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id,
                step_id,
                next_version,
                prompt_template,
                rendered_prompt,
                run_id,
                context_data,
                output_data,
                tokens_input,
                tokens_output,
                model_used,
                notes,
                now,
            ),
        )
        conn.commit()
        return _row_to_dict(
            conn.execute("SELECT * FROM prompt_versions WHERE id = ?", (new_id,)).fetchone()
        )

    def list_versions(self, step_id: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM prompt_versions WHERE step_id = ? ORDER BY version DESC",
            (step_id,),
        ).fetchall()
        return _rows_to_dicts(rows)

    def get_version(self, version_id: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM prompt_versions WHERE id = ?", (version_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_version_by_number(self, step_id: str, version: int) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM prompt_versions WHERE step_id = ? AND version = ?",
            (step_id, version),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def diff_versions(self, version_id_a: str, version_id_b: str) -> dict:
        a = self.get_version(version_id_a)
        b = self.get_version(version_id_b)

        if not a or not b:
            return {"error": "one or both versions not found", "a": a, "b": b}

        a_lines = a["prompt_template"].splitlines()
        b_lines = b["prompt_template"].splitlines()

        max_len = max(len(a_lines), len(b_lines))
        diff_lines = []

        for i in range(max_len):
            a_line = a_lines[i] if i < len(a_lines) else None
            b_line = b_lines[i] if i < len(b_lines) else None

            if a_line == b_line:
                diff_lines.append({"line": i + 1, "type": "context", "content": a_line})
            else:
                if a_line is not None:
                    diff_lines.append({"line": i + 1, "type": "removed", "content": a_line})
                if b_line is not None:
                    diff_lines.append({"line": i + 1, "type": "added", "content": b_line})

        a_lines_set = set(a_lines)
        b_lines_set = set(b_lines)
        added_lines = sorted(b_lines_set - a_lines_set)
        removed_lines = sorted(a_lines_set - b_lines_set)

        return {
            "version_a": {
                "id": a["id"],
                "version": a["version"],
                "step_id": a["step_id"],
            },
            "version_b": {
                "id": b["id"],
                "version": b["version"],
                "step_id": b["step_id"],
            },
            "added_lines": added_lines,
            "removed_lines": removed_lines,
            "diff_lines": diff_lines,
            "identical": a["prompt_template"] == b["prompt_template"],
        }

    def rollback(self, step_id: str, target_version: int) -> dict:
        source = self.get_version_by_number(step_id, target_version)
        if not source:
            raise ValueError(f"Version {target_version} not found for step {step_id}")

        return self.record_version(
            step_id=step_id,
            prompt_template=source["prompt_template"],
            rendered_prompt=source.get("rendered_prompt", ""),
            run_id=source.get("run_id", ""),
            context_data=source.get("context_data", "{}"),
            output_data=source.get("output_data", "{}"),
            tokens_input=source.get("tokens_input", 0),
            tokens_output=source.get("tokens_output", 0),
            model_used=source.get("model_used", ""),
            notes=f"Rollback to v{target_version}",
        )

    def get_step_latest(self, step_id: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM prompt_versions WHERE step_id = ? ORDER BY version DESC LIMIT 1",
            (step_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
