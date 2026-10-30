"""Agent runner. It holds only an agent token and reaches everything through the gateway's HTTP API.

The model chooses each tool call. If the model is unreachable the run falls back to a fixed plan, and the run record
says so: brain is "model" or "scripted-fallback", and the UI and proof files show that label.
"""
import json
import os

from . import nebius_client as nc

MAX_STEPS = 6
SYSTEM = "You are the {name} for a small cafe. Job: {job}. Use only the tools you are given. When finished, reply in one short sentence."
budget = nc.Budget(float(os.environ.get("AGENT_SPEND_CAP_USD", "3.00")))


class ModelBrain:
    label = "model"

    def __init__(self, tier: str = "super"):
        self.tier = tier

    def step(self, messages: list, tools: list) -> dict:
        budget.check()
        model = nc.MODELS[self.tier]
        r = nc.client().chat.completions.create(model=model, messages=messages, tools=tools, temperature=0.2, max_tokens=400,
                                                extra_body={"chat_template_kwargs": {"enable_thinking": False}})
        pin, pout = nc.prices().get(model, nc.FALLBACK_PRICES[model])
        budget.spent += r.usage.prompt_tokens * pin + r.usage.completion_tokens * pout
        m = r.choices[0].message
        if not m.tool_calls:
            return {"done": m.content or ""}
        t = m.tool_calls[0]
        return {"tool": t.function.name, "args": json.loads(t.function.arguments or "{}"), "id": t.id}


class ScriptedBrain:
    label = "scripted-fallback"

    def __init__(self, plan: list):
        self.plan = list(plan)

    def step(self, messages: list, tools: list) -> dict:
        if not self.plan:
            return {"done": "plan finished"}
        tool, args = self.plan.pop(0)
        return {"tool": tool, "args": args, "id": f"s{len(messages)}"}


def run_agent(http, agent: dict, task: str, plan: list, brain=None) -> dict:
    """http is any httpx-style client pointed at the gateway. Returns the transcript and which brain made the choices."""
    head = {"Authorization": f"Bearer {agent['token']}"}
    found = http.get("/gateway/tools", headers=head).json()
    messages = [{"role": "system", "content": SYSTEM.format(name=agent["name"], job=agent["job"])},
                {"role": "user", "content": task}]
    brain = brain or ModelBrain()
    fallback, steps, error = ScriptedBrain(plan), [], ""
    for _ in range(MAX_STEPS):
        try:
            d = brain.step(messages, found["tools"])
        except Exception as e:        # any model failure ends model use for this run and is recorded
            if brain is fallback:
                raise
            error, brain = f"{type(e).__name__}: {str(e)[:160]}", fallback
            d = brain.step(messages, found["tools"])
        if "done" in d:
            return {"brain": brain.label, "model_error": error, "tools_seen": names(found), "steps": steps, "final": d["done"]}
        r = http.post("/gateway/call", headers=head, json={"tool": d["tool"], "args": d["args"]})
        result = r.json()
        steps.append({"tool": d["tool"], "args": d["args"], "http": r.status_code, "result": result})
        messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": d["id"], "type": "function", "function": {
            "name": d["tool"], "arguments": json.dumps(d["args"])}}]})
        messages.append({"role": "tool", "tool_call_id": d["id"], "content": json.dumps(result)})
    return {"brain": brain.label, "model_error": error, "tools_seen": names(found), "steps": steps, "final": "step limit reached"}


def names(found: dict) -> list:
    return [t["function"]["name"] for t in found["tools"]]
