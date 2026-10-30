# Probation

Agents earn their spending power from what PayPal shows.

A small business runs separate AI agents for ads, supplies and refunds. When PayPal shows that an agent's decision
caused a refund, Probation fires it and removes its PayPal tools before its next call. The owner can raise limits,
hand over the work, and reinstate the agent, and each of those decisions is logged.

## How it works

- **Gateway.** The only part of the product holding PayPal credentials. Agents hold a token and nothing else.
- **Tool filtering.** Discovery and every call use the same list, so a removed tool is gone from both at once.
- **Scores from PayPal.** A reconciler reads each capture back from PayPal. The agent is identified from the order's
  `custom_id`, and every score change carries a transaction ID or gateway log ID.
- **Ladder.** Clean settlements promote, partial refunds and open disputes demote, and full refunds and lost disputes
  fire. Over-limit attempts cost score.
- **Owner console.** An editable AG Grid roster, a guided flow, an evidence trail, and AG Studio risk and spend views.

## Run it

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn probation.main:app --port 8000
```

Set `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `NEBIUS_API_KEY` and `NEBIUS_BASE_URL` in `.env`. Set `OWNER_TOKEN` to
require a token on owner actions. Open http://127.0.0.1:8000 and follow the Owner flow.

## Current limits

- Agent decisions in the recorded run came from a fixed fallback plan, not a model, because the model endpoint was
  unavailable. Every run is labelled, in the UI and in `proof_results/`, with which one made its choices.
- The gateway calls PayPal's REST APIs directly. It does not use the PayPal Agent Toolkit or MCP server.
- In the sandbox, the app's merchant account plays the supplier and PayPal's test card plays the business's payment
  method.
- AG Studio is commercial software. It runs here without a key, so it shows a watermark and console notices. Its
  widgets have no cell editing, so the editable roster is AG Grid Community. AG Studio's AI panel needs a licensed
  key and is not used.

Results are in [RESULTS.md](RESULTS.md).
