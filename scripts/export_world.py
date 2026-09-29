"""Export the public part of each synthetic world for the dashboard (no private fields)."""
import json
from pathlib import Path

from agent import world

for sid, sc in world.SCENARIOS.items():
    out = {
        "scenario": sid,
        "title": sc["title"],
        "person": sc["person"],
        "sponsor": sc["sponsor"],
        "neighborhoods": [{**h, "label": sc["site_labels"][h["id"]], "area": h["label"]} for h in world.NEIGHBORHOODS],
        "institutions": sc["institutions"],
        "arena": world.ARENA,
        "delegates": [{"id": d.id, "name": d.name, "hood": d.hood, "x": round(d.x, 3), "y": round(d.y, 3),
                       "summary": d.summary(), "trait": d.public_profile["trait"]}
                      for d in world.delegates(scenario=sid)],
    }
    Path(f"dashboard/world-{sid}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(sid, len(out["delegates"]), "delegates exported")
