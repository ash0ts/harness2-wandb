# Pilot implementation notes

The first campaign exercises the runner as well as the research question. These
implementation changes are part of its evidence and should accompany its results.
The state receipts identify the revisions and affected attempts.

- Concurrent input staging initially failed for two technical-writing jobs before
  they made any provider requests. Their failure records were retained. A lock fixed
  staging; only those unstarted jobs were restarted.
- The first runner stopped a domain after a failed adoption. Upstream `run_domain`
  continues to the next task without adding a library entry. The runner was corrected
  to follow upstream, preserving completed no-output attempts. This happened before
  any benchmark grading or held-out execution.
- A condition with no adopted task2 harness receives no substitute harness. Its
  transfer executions remain unavailable and count against planned coverage. Other
  conditions can finish. Failed final adaptation attempts receive the native no-output
  score; their empty output directories and traces are retained.
- Provider requests and OpenCode tool events were captured during adaptation. The
  original gateway did not save request bodies for calls it rejected before forwarding
  to the provider. Those rejections can appear in OpenCode's trajectory, but they do
  not have independent model-request spans. Exact preflight retry counts therefore
  cannot be claimed for the adaptation traces. The gateway now records those requests
  with an explicit `forwarded_to_provider: false` and zero provider charge.
- The input-size guard originally measured messages plus a fixed overhead. It now
  measures the entire request, including tool definitions, before reserving cost.
  The gateway update is applied only after adaptation has no in-flight requests.
  Recorded provider usage is checked against every reservation; no known usage is
  replaced with zero. Code revisions and the audit are included in exported provenance.
- Agent Lens's returned cost fields assigned zero cost to cached input in the inspected
  qualification spans. Report costs use provider usage and the pinned cached-input
  price instead. The public cost ledger, rather than the Agent Lens display total,
  governs the spending cap.

All conditions share the pinned offline worker image. Its tool environment includes
limitations that adaptation can observe, including tool attempts that require unavailable
internet access. The image is not repaired between conditions. The public report should
not attribute an observed score difference to diagnostics without inspecting the
deliverable and judge evidence.

The manifest's seed controls the upstream workflow. It is not an inference sampling
seed. Sampling parameters were omitted from model requests, retaining provider defaults;
the independent request records preserve this choice. Exact replay of generated text is
not promised.
