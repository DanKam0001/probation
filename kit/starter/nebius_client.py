"""Nemotron on Nebius Token Factory: priced calls, a spend cap per job, and JSON replies that are checked."""
import json
import os
import re

import httpx
from openai import OpenAI

BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
MODELS = {"lightning": "nvidia/Nemotron-3_5-Lightning", "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
          "super": "nvidia/nemotron-3-super-120b-a12b", "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b"}
FALLBACK_PRICES = {MODELS["lightning"]: (6e-8, 2.4e-7), MODELS["nano"]: (6e-8, 2.4e-7),
                   MODELS["super"]: (3e-7, 9e-7), MODELS["ultra"]: (1e-6, 3e-6)}   # US$ per token
THINKING_EXTRA_TOKENS = 4096
_prices = None


def client() -> OpenAI:
    return OpenAI(base_url=BASE_URL, api_key=os.environ["NEBIUS_API_KEY"], max_retries=2, timeout=120)


def prices() -> dict:
    """Live per-token prices, falling back to the table if the call fails."""
    global _prices
    if _prices is None:
        _prices = dict(FALLBACK_PRICES)
        try:
            r = httpx.get(f"{BASE_URL}/models", params={"verbose": "true"}, timeout=15,
                          headers={"Authorization": f"Bearer {os.environ.get('NEBIUS_API_KEY', '')}"})
            for m in r.json().get("data", []):
                p = m.get("pricing") or {}
                if p.get("prompt") is not None and p.get("completion") is not None:
                    _prices[m["id"]] = (float(p["prompt"]), float(p["completion"]))
        except (httpx.HTTPError, ValueError):
            pass
    return _prices


class Budget:
    """Running spend for one job. Refuses the next call once the cap is reached."""

    def __init__(self, cap_usd: float):
        self.cap, self.spent = cap_usd, 0.0

    def check(self) -> None:
        if self.spent >= self.cap:
            raise RuntimeError(f"spend cap of US${self.cap:.2f} reached")


def chat(tier: str, messages: list, budget: Budget, thinking: bool = False, max_tokens: int = 900,
         temperature: float = 0.0, api=None) -> dict:
    budget.check()
    model = MODELS[tier]
    r = (api or client()).chat.completions.create(
        model=model, messages=messages, temperature=temperature,
        max_tokens=max_tokens + (THINKING_EXTRA_TOKENS if thinking else 0),
        extra_body={"chat_template_kwargs": {"enable_thinking": thinking}})
    pin, pout = prices().get(model, FALLBACK_PRICES[model])
    cost = r.usage.prompt_tokens * pin + r.usage.completion_tokens * pout
    budget.spent += cost
    text = r.choices[0].message.content or ""
    return {"text": re.sub(r"(?s)^.*?</think>\s*", "", text).strip(), "cost_usd": cost,
            "tokens_in": r.usage.prompt_tokens, "tokens_out": r.usage.completion_tokens}


def parse_json(text: str) -> dict:
    """The first JSON object in a reply; tolerates fences and chatter."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    i = text.find("{")
    if i < 0:
        raise ValueError("no JSON object in reply")
    return json.JSONDecoder().raw_decode(text[i:])[0]


def chat_json(tier: str, system: str, user: str, budget: Budget, fields: dict, retries: int = 1, api=None, **kw) -> dict:
    """fields maps required key -> type. A wrong shape counts as a failure and is retried, not passed on."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(retries + 1):
        reply = chat(tier, messages, budget, api=api, **kw)
        try:
            obj = parse_json(reply["text"])
            wrong = [k for k, t in fields.items() if not isinstance(obj.get(k), t)]
            if wrong:
                raise ValueError(f"wrong or missing fields: {wrong}")
            return obj
        except ValueError as e:
            if attempt == retries:
                raise
            messages += [{"role": "assistant", "content": reply["text"]},
                         {"role": "user", "content": f"{e}. Reply with only the JSON object."}]
