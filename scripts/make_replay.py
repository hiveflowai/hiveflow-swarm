"""Regenerate the offline replay files the dashboard plays (deterministic, no models)."""
import json
from pathlib import Path

from agent.sim import run_series

for scenario, mode in [(sc, m) for sc in ("health", "finance") for m in ("federated", "isolated")]:
    raw = Path(f"runs/replay-{scenario}-{mode}.jsonl")
    series = run_series(5, mode, raw, start=1790000000, scenario=scenario)
    print(scenario, mode, "fooled per round:", [s["stats"]["fooled"] for s in series],
          "screened:", [s["stats"]["screened"] for s in series])
    out = []
    for line in raw.read_text().splitlines():
        e = json.loads(line)
        if e["type"] == "swarm.round_complete":  # keep the last entries of each node's signed log
            e["node_audits"] = [{"node": a["node"], "entries": [
                {k: x[k] for k in ("seq", "type", "actor", "hash", "prev", "ts", "data")} for x in a["entries"][-12:]]}
                for a in e.get("node_audits", [])]
        out.append(e)
    Path(f"dashboard/replay/{scenario}-{mode}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n")
