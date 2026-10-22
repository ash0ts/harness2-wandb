# Study v4 provider-stream repair

On October 22, 2026, the running campaign stopped after the provider returned an HTTP-200 event stream containing an internal-server-error object and a completion marker. It contained no model completion or returned model ID. The legacy stream merger discarded the error, and the wrapper reported a model mismatch. This was not evidence of a model switch.

The v4 transport now preserves that error, retains the request's conservative cost reservation when usage is unavailable, and retries eligible failures only before any response is forwarded. There are still at most two transport retries per request. Every attempt is recorded and charged; the existing circuit breaker remains active. A partially forwarded response is never transparently replayed. Actual model mismatches and successful completions without model identity still stop the campaign.

For the affected saved records, readback recovers the error from the raw stream in memory. It uses the Forge SDK's empty-string representation for a missing response model rather than inventing a returned ID. Historical JSON, raw streams and reservations remain unchanged. This handling applies only to schema version 4.

## Resuming saved work

The original qualification record is preserved. Both adaptation and held-out execution require either its exact engine snapshot or an explicit `runtime-repairs/provider-stream-errors.json` receipt. The receipt must match the original and current source hashes, identical images and analysis, and a hashed passing validation log. Only `transport_v4.py`, `runtime.py` and the phase-gate implementation in `v4.py` may differ. A receipt cannot authorize model, manifest, fixture or analysis changes.

The campaign resumed from durable operation records. The two interrupted operations recovered their existing workspaces and execution identities. All 87 pre-recovery freeze records were checked for unchanged content. Failed and incomplete adaptations remain in the campaign; no trial was restarted and no treatment was added.

The original request's $0.032578 reservation remains bounded unknown cost. At the stop, total accounted spending was $102.831243: $102.527712 observed and $0.303531 bounded unknown. These are historical checkpoint values, not final campaign totals.

## Validation

The upstream workflow and showcase suite passed: 141 tests and two subtests. Subsequent stored-span normalization passed the 31 affected transport, recovery and telemetry tests. The final transfer-phase gate change passed all 21 transport and recovery tests. Lint and formatting checks passed.

Checks cover byte-preserving successful streams, error-only responses, bounded retries and costs, circuit behavior, no replay after partial output, missing model identities, actual mismatches, exact repair receipts, rejection of unrelated source drift, and recovery without another paid execution. Qualification, model settings, source/data pins, analysis, the $500 ceiling and all prior pilots remain unchanged.
