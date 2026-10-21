"""Human-readable names and stable joins for the actual execution sessions."""

from __future__ import annotations

import re

DOMAINS = {
    "computer_user_support_specialists": "Computer support",
    "online_merchants": "Online merchants",
    "supply_chain_managers": "Supply chain",
    "purchasing_agents_except_wholesale_retail_and_farm_products": "Purchasing",
    "social_science_research_assistants": "Social science",
    "training_and_development_specialists": "Training",
    "technical_writers": "Technical writing",
    "management_analysts": "Management analysis",
}
CONDITIONS = {
    "baseline": "Original",
    "harness2": "Raw feedback",
    "diagnostics": "Diagnostics",
    "qualification": "Qualification",
}


def identity_fields(identity, manifest):
    value = dict(identity)
    value["campaign"] = manifest["campaign"]
    task = value.get("task", "")
    domain, _, task_id = task.partition("/")
    phase, role = value.get("phase"), value.get("role")
    stage = value.get("stage") or value.get("run_id", "").split("/")[-1]
    match = re.fullmatch(r"(base|inv|dom|c)([12])(?:\.a\d+)?", stage)
    round_number = int(match[2]) if match else 0
    stage_name = {
        "base": "Starting execution",
        "inv": "General proposal" if role == "improver" else "General probe",
        "dom": "Domain proposal" if role == "improver" else "Domain probe",
        "c": "Compose" if role == "improver" else "Composed probe",
    }.get(match[1] if match else "", stage)
    if stage.startswith("select."):
        stage_name = "Adoption"
    elif role == "reflection":
        stage_name = "Reflection"
    if phase == "smoke":
        cohort = "qualification"
    elif role == "judge":
        cohort = "judging"
    elif phase == "evaluate":
        cohort, stage_name = "heldout", "Held-out execution"
    else:
        cohort = "adaptation"
    if role == "judge":
        stage_name = "Native judge"
    value.update(
        domain=domain,
        task_id=task_id,
        condition=value.get("arm", "qualification"),
        cohort=cohort,
        round=round_number,
        stage_name=stage_name or "Tool check",
        attempt=value.get("proposal_attempt", 1),
        source_fingerprint=value.get("harness_fingerprint", ""),
        rendered_fingerprint=value.get("rendered_fingerprint", ""),
    )
    if manifest.get("schema_version") == 4:
        value["study"] = value.get("study", manifest.get("active_study", "jobbench"))
        value["trial"] = value.get("adaptation_replicate", 0)
        if value["study"] == "code":
            value["task_id"] = task
    return value


def conversation_name(identity, auxiliary=False):
    parts = [
        DOMAINS.get(identity.get("domain", ""), identity.get("task", "Infrastructure")),
        CONDITIONS.get(identity.get("condition", ""), identity.get("arm", "Qualification")),
    ]
    if identity.get("study"):
        parts.insert(0, "Code" if identity["study"] == "code" else "JobBench")
        parts.append(f"Trial {identity.get('trial', identity.get('adaptation_replicate', 0))}")
    if identity.get("cohort") == "qualification":
        number = identity.get("qualification_attempt", 1)
        parts.append("Qualification" if number == 1 else f"Qualification {number}")
    elif identity.get("task_id"):
        parts.append(identity["task_id"])
    if identity.get("round"):
        parts.append(f"Round {identity['round']}")
    parts.extend(
        [identity.get("stage_name", identity["role"]), f"Attempt {identity.get('attempt', 1)}"]
    )
    if auxiliary:
        parts.append("Auxiliary titles")
    return " / ".join(parts)
