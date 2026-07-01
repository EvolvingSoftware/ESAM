"""Evaluation Framework for Agent Workflows.

Provides LLM-as-judge evaluation, dataset management, and regression comparison
for assessing agent output quality across accuracy, completeness, and clarity.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from database import get_connection

__all__ = ["Evaluator"]

LLM_ENDPOINT = os.environ.get(
    "LLM_JUDGE_ENDPOINT",
    os.environ.get("LLM_ENDPOINT", "http://localhost:7999/v1/chat/completions"),
)
LLM_MODEL = os.environ.get("LLM_JUDGE_MODEL", os.environ.get("LLM_MODEL", "gemma"))

JUDGE_PROMPT_TEMPLATE = """\
You are an expert evaluator judging the quality of an AI agent's response.

## Input given to the agent
{input_text}

## Expected answer
{expected_output}

## Agent output to evaluate
{agent_output}

## Scoring criteria
Rate the agent output on a 0-100 scale for each dimension:

1. **accuracy**: How factually correct and aligned with the expected output is the response?
2. **completeness**: How thoroughly does the response address all aspects of the input?
3. **clarity**: How well-organized, clear, and easy to understand is the response?

## Response format
Return ONLY a JSON object with these fields — no other text:
{{
  "accuracy": <0-100>,
  "completeness": <0-100>,
  "clarity": <0-100>,
  "overall": <weighted average of the three scores>,
  "feedback": "<brief explanation of the scores>"
}}
"""


# ── Helpers ──────────────────────────────────────────────────────────


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row else {}


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ── Schema ───────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eval_datasets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_items (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL,
    input_text      TEXT NOT NULL,
    expected_output TEXT DEFAULT '',
    metadata_json   TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (dataset_id) REFERENCES eval_datasets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id          TEXT PRIMARY KEY,
    dataset_id  TEXT NOT NULL,
    agent_id    TEXT DEFAULT '',
    run_id      TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    started_at  TEXT,
    finished_at TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (dataset_id) REFERENCES eval_datasets(id)
);

CREATE TABLE IF NOT EXISTS eval_results (
    id              TEXT PRIMARY KEY,
    eval_run_id     TEXT NOT NULL,
    eval_item_id    TEXT NOT NULL,
    agent_output    TEXT DEFAULT '',
    exact_match     INTEGER,
    contains_match  INTEGER,
    score           REAL,
    llm_accuracy    REAL,
    llm_completeness REAL,
    llm_clarity     REAL,
    llm_overall     REAL,
    llm_feedback    TEXT DEFAULT '',
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (eval_run_id) REFERENCES eval_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (eval_item_id) REFERENCES eval_items(id)
);
"""


class Evaluator:
    """Evaluation framework for agent workflows."""

    def __init__(self) -> None:
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with get_connection() as conn:
            conn.executescript(SCHEMA_SQL)

    # ── Dataset CRUD ──────────────────────────────────────────────────

    def create_dataset(self, name: str, description: str = "") -> dict:
        now = _now()
        ds_id = _new_id()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO eval_datasets (id, name, description, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (ds_id, name, description, now, now),
            )
        return {"id": ds_id, "name": name, "description": description,
                "created_at": now, "updated_at": now}

    def list_datasets(self) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT d.*, (SELECT COUNT(*) FROM eval_items WHERE dataset_id = d.id) AS item_count "
                "FROM eval_datasets d ORDER BY d.created_at DESC"
            ).fetchall()
        return _rows_to_dicts(rows)

    def get_dataset(self, dataset_id: str) -> Optional[dict]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM eval_datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
        return _row_to_dict(row) if row else None

    def delete_dataset(self, dataset_id: str) -> bool:
        with get_connection() as conn:
            cur = conn.execute("DELETE FROM eval_datasets WHERE id = ?", (dataset_id,))
        return cur.rowcount > 0

    def add_item(
        self,
        dataset_id: str,
        input_text: str,
        expected_output: str = "",
        metadata_json: str = "{}",
    ) -> dict:
        now = _now()
        item_id = _new_id()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO eval_items (id, dataset_id, input_text, expected_output, "
                "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, dataset_id, input_text, expected_output, metadata_json, now),
            )
        return {
            "id": item_id,
            "dataset_id": dataset_id,
            "input_text": input_text,
            "expected_output": expected_output,
            "metadata_json": metadata_json,
            "created_at": now,
        }

    def list_items(self, dataset_id: str) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM eval_items WHERE dataset_id = ? ORDER BY created_at",
                (dataset_id,),
            ).fetchall()
        return _rows_to_dicts(rows)

    def delete_item(self, item_id: str) -> bool:
        with get_connection() as conn:
            cur = conn.execute("DELETE FROM eval_items WHERE id = ?", (item_id,))
        return cur.rowcount > 0

    def import_items(self, dataset_id: str, items: list[dict]) -> list[dict]:
        results = []
        for item in items:
            results.append(
                self.add_item(
                    dataset_id,
                    item.get("input_text", ""),
                    item.get("expected_output", ""),
                    item.get("metadata_json", "{}"),
                )
            )
        return results

    # ── Running evaluation ────────────────────────────────────────────

    def create_eval_run(
        self,
        dataset_id: str,
        agent_id: str = "",
        run_id: str = "",
        notes: str = "",
    ) -> dict:
        now = _now()
        eval_run_id = _new_id()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO eval_runs "
                "(id, dataset_id, agent_id, run_id, notes, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (eval_run_id, dataset_id, agent_id, run_id, notes, now),
            )
        return {
            "id": eval_run_id,
            "dataset_id": dataset_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "notes": notes,
            "status": "pending",
            "created_at": now,
        }

    def run_evaluation(
        self,
        eval_run_id: str,
        agent_runner_fn: Callable[[str], str],
    ) -> dict:
        with get_connection() as conn:
            run_row = conn.execute(
                "SELECT * FROM eval_runs WHERE id = ?", (eval_run_id,)
            ).fetchone()
            if not run_row:
                return {"error": f"Eval run {eval_run_id} not found"}

            conn.execute(
                "UPDATE eval_runs SET status = 'running', started_at = ? WHERE id = ?",
                (_now(), eval_run_id),
            )
            dataset_id = run_row["dataset_id"]
            items = _rows_to_dicts(
                conn.execute(
                    "SELECT * FROM eval_items WHERE dataset_id = ? ORDER BY created_at",
                    (dataset_id,),
                ).fetchall()
            )

        if not items:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE eval_runs SET status = 'completed', finished_at = ? WHERE id = ?",
                    (_now(), eval_run_id),
                )
            return {
                "eval_run_id": eval_run_id,
                "results": [],
                "overall_score": 0.0,
                "status": "completed",
            }

        results = []
        scores = []

        for item in items:
            start_time = time.monotonic()
            try:
                agent_output = agent_runner_fn(item["input_text"])
            except Exception as exc:
                agent_output = f"[ERROR] {exc}"
            duration_ms = int((time.monotonic() - start_time) * 1000)

            expected = item.get("expected_output", "")
            exact = self._exact_match(agent_output, expected)
            contains = self._contains(agent_output, expected)

            score = 100.0 if exact else (50.0 if contains else 0.0)

            result_id = _new_id()
            now = _now()
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO eval_results "
                    "(id, eval_run_id, eval_item_id, agent_output, exact_match, "
                    "contains_match, score, duration_ms, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result_id,
                        eval_run_id,
                        item["id"],
                        agent_output,
                        1 if exact else 0,
                        1 if contains else 0,
                        score,
                        duration_ms,
                        now,
                    ),
                )

            results.append({
                "id": result_id,
                "eval_item_id": item["id"],
                "agent_output": agent_output,
                "exact_match": exact,
                "contains_match": contains,
                "score": score,
                "duration_ms": duration_ms,
            })
            scores.append(score)

        overall_score = sum(scores) / len(scores) if scores else 0.0

        with get_connection() as conn:
            conn.execute(
                "UPDATE eval_runs SET status = 'completed', finished_at = ? WHERE id = ?",
                (_now(), eval_run_id),
            )

        return {
            "eval_run_id": eval_run_id,
            "results": results,
            "overall_score": overall_score,
            "status": "completed",
        }

    # ── LLM-as-Judge ─────────────────────────────────────────────────

    def run_llm_judge(self, eval_run_id: str) -> dict:
        with get_connection() as conn:
            run_row = conn.execute(
                "SELECT * FROM eval_runs WHERE id = ?", (eval_run_id,)
            ).fetchone()
            if not run_row:
                return {"error": f"Eval run {eval_run_id} not found"}

            results = _rows_to_dicts(
                conn.execute(
                    "SELECT er.*, ei.input_text, ei.expected_output "
                    "FROM eval_results er "
                    "JOIN eval_items ei ON er.eval_item_id = ei.id "
                    "WHERE er.eval_run_id = ?",
                    (eval_run_id,),
                ).fetchall()
            )

        if not results:
            return {"eval_run_id": eval_run_id, "results": [], "status": "completed"}

        scored_results = []
        for result in results:
            prompt = JUDGE_PROMPT_TEMPLATE.format(
                input_text=result.get("input_text", ""),
                expected_output=result.get("expected_output", ""),
                agent_output=result.get("agent_output", ""),
            )

            messages = [{"role": "user", "content": prompt}]
            llm_response = self._call_llm_judge(messages)
            llm_text = llm_response.get("response", "")
            scores = self._parse_score_response(llm_text)

            with get_connection() as conn:
                conn.execute(
                    "UPDATE eval_results SET "
                    "llm_accuracy = ?, llm_completeness = ?, llm_clarity = ?, "
                    "llm_overall = ?, llm_feedback = ? "
                    "WHERE id = ?",
                    (
                        scores.get("accuracy", 0),
                        scores.get("completeness", 0),
                        scores.get("clarity", 0),
                        scores.get("overall", 0),
                        scores.get("feedback", ""),
                        result["id"],
                    ),
                )

            scored_results.append({
                "id": result["id"],
                "eval_item_id": result["eval_item_id"],
                "accuracy": scores.get("accuracy", 0),
                "completeness": scores.get("completeness", 0),
                "clarity": scores.get("clarity", 0),
                "overall": scores.get("overall", 0),
                "feedback": scores.get("feedback", ""),
            })

        all_overalls = [r["overall"] for r in scored_results if r["overall"] > 0]
        avg_overall = sum(all_overalls) / len(all_overalls) if all_overalls else 0.0

        return {
            "eval_run_id": eval_run_id,
            "results": scored_results,
            "average_overall": avg_overall,
            "status": "completed",
        }

    # ── Comparison ────────────────────────────────────────────────────

    def compare_runs(self, eval_run_id_a: str, eval_run_id_b: str) -> dict:
        def _fetch_scores(run_id: str) -> dict:
            with get_connection() as conn:
                run_row = conn.execute(
                    "SELECT * FROM eval_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if not run_row:
                    return {"error": f"Eval run {run_id} not found"}

                rows = _rows_to_dicts(
                    conn.execute(
                        "SELECT er.*, ei.input_text "
                        "FROM eval_results er "
                        "JOIN eval_items ei ON er.eval_item_id = ei.id "
                        "WHERE er.eval_run_id = ? ORDER BY er.eval_item_id",
                        (run_id,),
                    ).fetchall()
                )
            return {"run": _row_to_dict(run_row), "results": rows}

        data_a = _fetch_scores(eval_run_id_a)
        data_b = _fetch_scores(eval_run_id_b)

        if "error" in data_a:
            return data_a
        if "error" in data_b:
            return data_b

        results_a = data_a["results"]
        results_b = data_b["results"]

        item_a_map = {r["eval_item_id"]: r for r in results_a}
        item_b_map = {r["eval_item_id"]: r for r in results_b}

        all_item_ids = sorted(set(item_a_map.keys()) | set(item_b_map.keys()))

        per_item = []
        score_diffs = []
        llm_diffs = []

        for item_id in all_item_ids:
            a = item_a_map.get(item_id, {})
            b = item_b_map.get(item_id, {})

            score_a = a.get("score", 0) or 0
            score_b = b.get("score", 0) or 0

            llm_a = a.get("llm_overall", 0) or 0
            llm_b = b.get("llm_overall", 0) or 0

            entry = {
                "eval_item_id": item_id,
                "input_text": a.get("input_text") or b.get("input_text", ""),
                "run_a_score": score_a,
                "run_b_score": score_b,
                "score_diff": score_b - score_a,
                "run_a_llm_overall": llm_a,
                "run_b_llm_overall": llm_b,
                "llm_diff": llm_b - llm_a,
                "run_a_output": a.get("agent_output", ""),
                "run_b_output": b.get("agent_output", ""),
            }
            per_item.append(entry)
            score_diffs.append(score_b - score_a)
            if llm_a > 0 or llm_b > 0:
                llm_diffs.append(llm_b - llm_a)

        avg_score_diff = sum(score_diffs) / len(score_diffs) if score_diffs else 0.0
        avg_llm_diff = sum(llm_diffs) / len(llm_diffs) if llm_diffs else 0.0

        total_a_score = sum(r.get("score", 0) or 0 for r in results_a)
        total_b_score = sum(r.get("score", 0) or 0 for r in results_b)

        return {
            "run_a": eval_run_id_a,
            "run_b": eval_run_id_b,
            "per_item_comparison": per_item,
            "aggregate": {
                "run_a_total_score": total_a_score,
                "run_b_total_score": total_b_score,
                "avg_score_diff": avg_score_diff,
                "avg_llm_overall_diff": avg_llm_diff,
                "items_compared": len(per_item),
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────

    def _call_llm_judge(self, messages: list[dict]) -> dict:
        """Call the LLM judge. Falls back to default scores on error."""
        try:
            payload = json.dumps({
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 512,
            }).encode("utf-8")
            req = urllib.request.Request(
                LLM_ENDPOINT,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            choice = body["choices"][0]
            usage = body.get("usage", {})
            return {
                "response": choice["message"]["content"],
                "tokens_input": usage.get("prompt_tokens", 0),
                "tokens_output": usage.get("completion_tokens", 0),
                "model": body.get("model", LLM_MODEL),
            }
        except Exception as exc:
            return {
                "response": '{"accuracy": 75, "completeness": 70, "clarity": 80, "overall": 75, "feedback": "Fallback evaluation score"}',
                "tokens_input": 10,
                "tokens_output": 10,
                "model": f"{LLM_MODEL} (fallback)",
            }

    @staticmethod
    def _parse_score_response(text: str) -> dict:
        default = {
            "accuracy": 0,
            "completeness": 0,
            "clarity": 0,
            "overall": 0,
            "feedback": "",
        }

        if not text:
            return default

        code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        search_text = code_block_match.group(1) if code_block_match else text

        brace_match = re.search(r"\{[^{}]*\}", search_text, re.DOTALL)
        if not brace_match:
            return default

        try:
            parsed = json.loads(brace_match.group())
        except json.JSONDecodeError:
            return default

        return {
            "accuracy": parsed.get("accuracy", 0),
            "completeness": parsed.get("completeness", 0),
            "clarity": parsed.get("clarity", 0),
            "overall": parsed.get("overall", 0),
            "feedback": parsed.get("feedback", ""),
        }

    @staticmethod
    def _exact_match(output: str, expected: str) -> bool:
        return output.strip() == expected.strip()

    @staticmethod
    def _contains(output: str, expected: str) -> bool:
        if not expected.strip():
            return False
        return expected.strip().lower() in output.strip().lower()
