# Nebius Token Factory kit

Proven in real runs by Good Enough and Spine (Oct 2026). Prices in `kit.json` were checked on 27 Oct 2026; live
prices override them.

## What's here
- `starter/nebius_client.py`: chat, chat_json, live prices, a per-job Budget.
- `starter/test_nebius_client.py`: offline tests with a fake client, so `pytest` costs nothing.
- `starter/render.yaml`, `starter/requirements.txt`, `starter/.env.example`.

## Facts that cost time to find
- OpenAI-compatible: the standard `openai` client with `base_url=https://api.tokenfactory.nebius.com/v1`.
- Model ids: `nvidia/Nemotron-3_5-Lightning`, `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`,
  `nvidia/nemotron-3-super-120b-a12b`, `nvidia/Nemotron-3-Ultra-550b-a55b`, and the vision model
  `nvidia/Nemotron-3.5-Super-VL`. The casing differs per model: copy them exactly.
- All tiers are reasoning models. Thinking moves cost and latency more than model size does. Turn it off per call:
  `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`. With thinking on, allow about 4096 extra
  `max_tokens`, and strip everything up to `</think>` from the reply.
- Lightning with thinking on costs more per call than Super with thinking off (it writes 1,000-4,000 reasoning tokens).
- Prices: `GET /v1/models?verbose=true` gives `pricing.prompt` and `pricing.completion` per token. Keep a fallback table.
- Whether `response_format` with a JSON schema works isn't documented per model. Both projects parse the JSON
  themselves and retry. Super once returned a nested object where a string was asked for, so validate types too, not
  just that it parses.

## Spend caps
Two layers, both in Spine: a per-job `Budget`, and a whole-server cap in the app (US$5 on both projects) that returns
503 and leaves recorded runs replaying. Save recorded runs so the demo survives a spent cap.

## During a build
`NEBIUS_API_KEY` and `NEBIUS_BASE_URL` in `.env` point at the local metering proxy, so read both from the environment.
Don't stream during a build: the proxy can't price a stream.

## Deploy
`starter/render.yaml` follows Spine's config (native Python runtime, FastAPI + uvicorn). Good Enough used
`runtime: docker`. `NEBIUS_API_KEY` is `sync: false` and never committed. Keep `/api/health`.
