# Results

From one recorded run, `proof_results/demo_run.json` (2026-10-30T15:37:08Z), driven through the real UI in headless
Chromium against the PayPal sandbox. Step detail is in `proof_results/demo_steps.json`, with screenshots alongside.

## What happened

| Step | Result | Reference |
| --- | --- | --- |
| Supplies agent buys wrong stock | 5 x BEAN-DECAF-1KG for $90.00, PayPal now shows REFUNDED | capture 5J371796YA449162N, refund 1VW64834XS8143224 |
| Firing | Score change of -40, PayPal tools removed | capture 5J371796YA449162N |
| Direct retry by the fired agent | HTTP 403 at the gateway | `denied_gateway_calls` |
| Ads agent over its limit | HTTP 403, $60.00 against $40.00, score change of -5 | gateway-25 |
| Owner edits the ads limit in the grid | $40.00 to $75.00, logged | `decisions` |
| Ads agent retries | $60.00, COMPLETED | capture 2G547930HW638531K |
| Refunds agent takes over the reorder | 5 x BEAN-HOUSE-1KG for $120.00, COMPLETED | capture 9JA453545L2559149 |
| Owner reinstates the supplies agent | Status demoted, limit $50.00, evidence logged | `decisions` |

Wrong spend refunded in this run: $90.00. PayPal disputes read back: 0.

## What failed or was not shown

- **Model runs: 0 of 5.** All 5 agent runs used the scripted fallback, with `NotFoundError: Error code: 404` from the
  model endpoint. So the wrong purchase in this run was a fixed plan, not a model's mistake.
- **Transaction Search** returned 403 for these sandbox credentials, so it is not used.
- **Disputes:** the API was read, but no dispute existed, so the dispute rules ran only in logic, never on a live one.
- **Promotion and owner review routing** are covered by offline tests only. They did not occur in this run.
- **AI-created views** were not built. The AG Studio views are fixed presets.
- **Browser console:** AG Studio's licence notice appears, because no key is set.
