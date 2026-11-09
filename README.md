# Probation

Agents earn their spending power from what PayPal shows.

A small business runs separate AI agents for ads, supplies and refunds. When PayPal shows that an agent's decision
caused a refund, Probation fires it and removes its PayPal tools before its next call. When PayPal shows three clean
payments in a row, the agent is promoted and its limit rises, with no owner edit. The owner can still change limits,
hand over the work, and reinstate an agent, and each of those decisions is logged.

## How it works

- **Gateway.** The only part of the product holding PayPal credentials. Agents hold a token and nothing else.
- **Tool filtering.** Discovery and every call use the same list, so a removed tool is gone from both at once.
- **Scores from PayPal.** A reconciler reads each capture back from PayPal. The agent is identified from the order's
  `custom_id`, and every score change carries a transaction ID or gateway log ID.
- **Ladder.** Three clean settlements promote and raise the limit by half. Partial refunds and open disputes demote,
  and full refunds and lost disputes fire. Over-limit attempts cost score.
- **Owner console.** An editable AG Grid roster, a guided flow, an evidence trail, and risk and spend views built with
  AG Grid Community and AG Charts Community. The owner can ask for a new view in plain English: the model picks from
  known fields, and the server rejects anything else before it reaches a chart.

## Run it

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn probation.main:app --port 8000
```

Set `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `NEBIUS_API_KEY` and `NEBIUS_BASE_URL` in `.env`. Set `OWNER_TOKEN` to
require a token on owner actions. Open http://127.0.0.1:8000 and follow the Owner flow.

## Current limits

- Agents run on Nemotron through Nebius Token Factory. If the model is unreachable, a run falls back to a fixed plan,
  and the UI and `proof_results/` label it as such. The recorded runs did not use the fallback.
- The gateway calls PayPal's REST APIs directly. It does not use the PayPal Agent Toolkit or MCP server.
- In the sandbox, the app's merchant account plays the supplier and PayPal's test card plays the business's payment
  method.
- Promotion runs live on the sandbox, but only on what a sandbox can show. A clean settlement here is a capture that
  PayPal reads back as COMPLETED with no return ticket, a few seconds after payment. The run does not wait out a real
  refund or chargeback window, and three payments is a demo threshold. The rule counts payments, not dollars or time,
  so an agent could farm cheap purchases. A real deployment would need a holding period and amount weighting.
- The dispute rules have never run on a live dispute: the sandbox had none. They and owner review routing are tested
  offline only.
- A free Render instance has temporary storage, so agent state resets when the service restarts. PayPal keeps its
  records.

## Deploy

`render.yaml` defines a free Render web service. Set the secrets in the dashboard, including `OWNER_TOKEN`. The console
asks for that token on the first owner action.

Results are in [RESULTS.md](RESULTS.md). Released under the [MIT License](LICENSE).
