"""Durable reservations shared by every model call, including concurrent retries."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path


class BudgetError(RuntimeError):
    pass


def micros(value):
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding="ROUND_CEILING"))


def usage_within_limits(usage, input_limit, output_limit):
    """Every supplied counter must be consistent, even when others are absent."""
    if usage is None:
        return True
    if not isinstance(usage, dict):
        return False
    limits = {
        "prompt_tokens": input_limit,
        "completion_tokens": output_limit,
        "total_tokens": input_limit + output_limit,
    }
    for key, limit in limits.items():
        value = usage.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= limit):
            return False
    for parent, key, count, maximum in (
        ("prompt_tokens_details", "cached_tokens", "prompt_tokens", input_limit),
        ("completion_tokens_details", "reasoning_tokens", "completion_tokens", output_limit),
    ):
        details = usage.get(parent)
        if details is None:
            continue
        if not isinstance(details, dict):
            return False
        value = details.get(key)
        ceiling = usage[count] if usage.get(count) is not None else maximum
        if value is not None and (type(value) is not int or not 0 <= value <= ceiling):
            return False
    return True


class Ledger:
    def __init__(self, path: Path, manifest: dict):
        self.path = path
        self.manifest = manifest
        self.bounded_missing = (
            manifest.get("schema_version") == 4
            and manifest.get("accounting", {}).get("missing_usage") == "charge_proven_reservation"
        )
        if micros(manifest.get("prior_spend_usd", 0)) + micros(manifest["budget_usd"]) > micros(
            manifest.get("lifetime_budget_usd", manifest["budget_usd"])
        ):
            raise BudgetError("Campaign and prior spend exceed the lifetime ceiling")
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY, session TEXT NOT NULL, phase TEXT NOT NULL,
                model TEXT NOT NULL, reserved INTEGER NOT NULL, spent INTEGER,
                status TEXT NOT NULL, usage TEXT, reason TEXT)""")
            columns = {r[1] for r in db.execute("PRAGMA table_info(calls)")}
            for name in ("input_limit", "output_limit"):
                if name not in columns:
                    db.execute(f"ALTER TABLE calls ADD COLUMN {name} INTEGER")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def reserve(
        self,
        session: str,
        phase: str,
        model: str,
        output_limit: int,
        input_limit: int | None = None,
    ) -> str:
        if model not in self.manifest["prices_per_million"]:
            raise BudgetError("Model is outside the frozen manifest")
        if not 0 < output_limit <= self.manifest["max_output_tokens"]:
            raise BudgetError("Invalid output limit")
        rate = self.manifest["prices_per_million"][model]
        input_limit = self.manifest["max_input_tokens"] if input_limit is None else input_limit
        if not 0 < input_limit <= self.manifest["max_input_tokens"]:
            raise BudgetError("Invalid reserved input limit")
        # Reserve the entire permitted context, without assuming cache savings.
        reservation = micros(
            (
                Decimal(str(rate["input"])) * input_limit
                + Decimal(str(rate["output"])) * output_limit
            )
            / 1_000_000
        )
        with self.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM calls WHERE session=?", (session,)).fetchone()[
                0
            ]
            if count >= self.manifest["max_steps"] * 2:
                raise BudgetError("Per-session request ceiling reached")
            if db.execute("SELECT 1 FROM calls WHERE status='unknown'").fetchone():
                raise BudgetError("Unreconciled usage; further paid requests are disabled")
            total = db.execute(
                "SELECT COALESCE(SUM(COALESCE(spent,reserved)),0) FROM calls"
            ).fetchone()[0]
            if total + reservation > micros(self.manifest["budget_usd"]):
                raise BudgetError("Campaign budget exhausted, including in-flight reservations")
            if phase == "smoke":
                subtotal = db.execute(
                    "SELECT COALESCE(SUM(COALESCE(spent,reserved)),0) FROM calls WHERE phase='smoke'"
                ).fetchone()[0]
                if subtotal + reservation > micros(self.manifest["qualification_budget_usd"]):
                    raise BudgetError("Qualification budget exhausted")
            for bucket, spec in self.manifest.get("budget_buckets", {}).items():
                if phase in spec["phases"]:
                    marks = ",".join("?" for _ in spec["phases"])
                    subtotal = db.execute(
                        f"SELECT COALESCE(SUM(COALESCE(spent,reserved)),0) FROM calls WHERE phase IN ({marks})",
                        spec["phases"],
                    ).fetchone()[0]
                    if subtotal + reservation > micros(spec["usd"]):
                        raise BudgetError(f"{bucket} budget exhausted, including reservations")
            call_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO calls VALUES (?,?,?,?,?,?,?,NULL,NULL,?,?)",
                (
                    call_id,
                    session,
                    phase,
                    model,
                    reservation,
                    None,
                    "pending",
                    input_limit,
                    output_limit,
                ),
            )
        return call_id

    def finish(self, call_id: str, usage: dict | None, *, rejection: str | None = None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row or row["status"] != "pending":
                raise BudgetError("Call is absent or already finalized")
            if rejection:
                db.execute(
                    "UPDATE calls SET status='rejected',spent=0,reason=? WHERE id=?",
                    (rejection, call_id),
                )
                return
            try:
                if not isinstance(usage, dict):
                    raise ValueError("provider did not return usage")
                if self.bounded_missing and not usage_within_limits(
                    usage, row["input_limit"], row["output_limit"]
                ):
                    raise ValueError("contradictory usage or usage beyond reserved limits")
                inp, out = usage["prompt_tokens"], usage["completion_tokens"]
                cache = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                if any(type(v) is not int or v < 0 for v in (inp, out, cache)):
                    raise ValueError("invalid usage counters")
                if cache > inp or inp > (row["input_limit"] or self.manifest["max_input_tokens"]):
                    raise ValueError("usage exceeds reserved input or double-counts cache")
                if out > (row["output_limit"] or self.manifest["max_output_tokens"]):
                    raise ValueError("output exceeds the configured ceiling")
                rate = self.manifest["prices_per_million"][row["model"]]
                cost = (
                    (inp - cache) * Decimal(rate["input"])
                    + cache * Decimal(rate["cached"])
                    + out * Decimal(rate["output"])
                ) / 1_000_000
                spent = micros(cost)
                if spent > row["reserved"]:
                    raise ValueError("cost exceeds reservation")
            except (KeyError, TypeError, ValueError) as exc:
                status = "unknown"
                # The complete request limits were reserved before forwarding. Missing
                # counters may retain that proven maximum; contradictory counters may not.
                missing = (
                    usage is None
                    or isinstance(usage, dict)
                    and any(
                        usage.get(key) is None for key in ("prompt_tokens", "completion_tokens")
                    )
                )
                safe_partial = (
                    usage_within_limits(usage, row["input_limit"], row["output_limit"])
                    if self.bounded_missing
                    else False
                )
                if self.bounded_missing and missing and safe_partial:
                    status = "bounded_unknown"
                db.execute(
                    "UPDATE calls SET status=?,usage=?,reason=? WHERE id=?",
                    (status, json.dumps(usage), str(exc), call_id),
                )
                return
            db.execute(
                "UPDATE calls SET status='complete',spent=?,usage=? WHERE id=?",
                (spent, json.dumps(usage), call_id),
            )

    def snapshot(self):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM calls ORDER BY rowid")]
        snapshot = {
            "spent_usd": sum(r["spent"] or 0 for r in rows) / 1_000_000,
            "reserved_usd": sum(r["reserved"] for r in rows if r["spent"] is None) / 1_000_000,
            "unresolved": sum(r["status"] in ("unknown", "pending") for r in rows),
            "calls": rows,
        }
        if self.bounded_missing:
            snapshot.update(
                observed_cost_usd=snapshot["spent_usd"],
                bounded_unknown_cost_usd=sum(
                    r["reserved"] for r in rows if r["status"] == "bounded_unknown"
                )
                / 1_000_000,
                in_flight_reservations_usd=sum(
                    r["reserved"] for r in rows if r["status"] == "pending"
                )
                / 1_000_000,
                charged_upper_bound_usd=sum(
                    r["spent"] if r["spent"] is not None else r["reserved"] for r in rows
                )
                / 1_000_000,
                bounded_unknown_requests=sum(r["status"] == "bounded_unknown" for r in rows),
            )
        return snapshot

    def interrupt_pending(self):
        """Call once at gateway startup, before accepting new requests."""
        with self.connect() as db:
            db.execute(
                "UPDATE calls SET status=?,reason='gateway interrupted' WHERE status='pending'",
                ("bounded_unknown" if self.bounded_missing else "unknown",),
            )
