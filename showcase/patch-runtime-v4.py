"""V4-only transport ownership patch, outside the editable harness surface."""

from pathlib import Path

root = Path("/opt/opencode/packages/opencode/src/session")
changes = {
    "retry.ts": (
        "export function retryable(error: Err) {",
        'export function retryable(error: Err) {\n  if (process.env.HARNESS2_GATEWAY_RETRY_OWNER === "1") return undefined',
    ),
    "llm.ts": (
        "maxRetries: input.retries ?? 0,",
        'maxRetries: process.env.HARNESS2_GATEWAY_RETRY_OWNER === "1" ? 0 : (input.retries ?? 0),',
    ),
}
for name, (old, new) in changes.items():
    path = root / name
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"Unexpected pinned OpenCode source: {name}")
    path.write_text(text.replace(old, new))
