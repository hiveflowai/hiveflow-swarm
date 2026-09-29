"""In-process federation for development, tests and the offline replay.

Runs the exact coordinator and participant code with an in-memory Grid that
honours the Flower Grid tool contract. Nothing here is used on SuperGrid.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from swarmauth import AuditLog, decide
from swarmauth.approval import ApprovalRequest
from swarmmind.envelope import seal
from swarmmind.flower import SwarmGrid, parse_prompt

from . import trust, world
from .coordinator import run_round
from .models import ModelClient
from .participant import audit_tail, run_participant


class _NodeGrid:
    def __init__(self, fed: "InProcFederation", node_id: str, instruction: dict[str, Any]):
        self.fed, self.node_id, self.instruction = fed, node_id, instruction

    def call(self, item: dict[str, Any]) -> dict[str, Any]:
        assert item["name"] == "push_reply_message"
        mid = uuid.uuid4().hex
        self.fed.replies[self.instruction["message_id"]] = {
            "message_id": mid, "reply_to_message_id": self.instruction["message_id"],
            "src_node_id": self.node_id, "payload": item["arguments"]["payload"], "error": None}
        return {"output": json.dumps({"message_id": mid, "error": None})}


class InProcFederation:
    """SuperLink-side Grid: push runs the participant synchronously."""

    def __init__(self, node_names: list[str], audit_dir: Path, now_fn: Callable[[], int],
                 participant_model: Optional[ModelClient] = None) -> None:
        self.nodes = [{"id": str(1000 + i), "name": n, "location": None} for i, n in enumerate(node_names)]
        self.replies: dict[str, dict[str, Any]] = {}
        self.audit_dir = audit_dir
        self.now_fn = now_fn
        self.model = participant_model or ModelClient("none")

    def call(self, item: dict[str, Any]) -> dict[str, Any]:
        args = item["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        name = item["name"]
        if name == "get_nodes":
            out = {"nodes": self.nodes, "num_available": len(self.nodes)}
        elif name == "push_messages":
            results = []
            for m in args["messages"]:
                mid = uuid.uuid4().hex
                self._deliver(m["dst_node_id"], mid, m["payload"])
                results.append({"message_id": mid, "error": None})
            out = {"results": results}
        elif name == "pull_messages":
            got = [self.replies.pop(i) for i in args["message_ids"] if i in self.replies]
            out = {"messages": got, "pending_message_ids": [i for i in args["message_ids"]
                                                            if i not in {g["reply_to_message_id"] for g in got}]}
        else:
            raise ValueError(name)
        return {"type": "function_call_output", "call_id": item["call_id"], "output": json.dumps(out)}

    def _deliver(self, node_id: str, message_id: str, payload: str) -> None:
        prompt = json.dumps({"message_id": message_id, "src_node_id": "0", "payload": payload})
        mid, src, pl = parse_prompt(prompt)
        hoods = list(json.loads(pl)["body"]["hoods"].keys())
        me = trust.node_key(hoods[0])
        audit = AuditLog(self.audit_dir / f"node-{hoods[0]}.jsonl", me)
        grid = _NodeGrid(self, node_id, {"message_id": message_id})
        sg = SwarmGrid(grid, me, audit, trust.federation_roots(), f"node/{hoods[0]}", clock=self.now_fn)
        inc = sg.verify(pl, mid, src)
        now = self.now_fn()
        body = run_participant(inc.opened.env.body, self.model, audit, now)
        body["audit"] = audit_tail(audit)
        sg.reply(seal(me, "propose", body, [trust.node_mandate(hoods[0], now)], ts=now,
                      round=inc.opened.env.round, reply_to=inc.opened.env.id))


def auto_decide(summary: dict[str, Any], approve_commitments: bool = True,
                approve_vaccines: bool = True) -> dict[str, Any]:
    """Stand-in for the humans in offline runs: sign decisions with the right keys."""
    ns = summary["next_state"]
    decisions = []
    for req_d in ns.get("pending_approvals", []):
        d = next(x for x in world.delegates_for(req_d["action"]["hood"]) if x.id == req_d["action"]["delegate"])
        req = ApprovalRequest(**req_d)
        decisions.append({"request": req_d, "decision": decide(d.principal, req, approve_commitments).to_dict()})
    vaccines = list(ns.get("vaccines", []))
    for c in ns.get("vaccine_candidates", []):
        req = ApprovalRequest(**c["request"])
        dec = decide(trust.security_officer(), req, approve_vaccines)
        if approve_vaccines:
            vaccines.append({"pattern": c["pattern"], "request": c["request"], "decision": dec.to_dict(),
                             "origin_hood": c["origin_hood"]})
    return {"round": ns["round"], "mode": ns["mode"], "per_hood": ns["per_hood"], "scenario": ns.get("scenario"),
            "vaccines": vaccines, "decisions": decisions}


def run_series(rounds: int, mode: str, out: Path, node_names: Optional[list[str]] = None,
               coordinator_model: Optional[ModelClient] = None,
               participant_model: Optional[ModelClient] = None,
               start: Optional[int] = None, scenario: str = "health") -> list[dict[str, Any]]:
    """Run ``rounds`` rounds offline, writing dashboard events to ``out`` (JSONL)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    audit_dir = out.parent / f"audit-{mode}"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for f in audit_dir.glob("*.jsonl"):
        f.unlink()
    clock = {"t": start or int(time.time())}
    names = node_names or [f"sf-{h['id']}" for h in world.NEIGHBORHOODS]
    fed = InProcFederation(names, audit_dir, lambda: clock["t"], participant_model)
    coord = trust.coordinator()
    caudit = AuditLog(audit_dir / "coordinator.jsonl", coord)
    sg = SwarmGrid(fed, coord, caudit, trust.federation_roots(), "coordinator", clock=lambda: clock["t"])
    model = coordinator_model or ModelClient("none")
    summaries = []
    world.use(scenario)
    state: dict[str, Any] = {"round": 1, "mode": mode, "per_hood": 8, "scenario": scenario}
    with out.open("w") as f:
        def emit(e: dict[str, Any]) -> None:
            f.write(json.dumps({**e, "ts": clock["t"]}, ensure_ascii=False) + "\n")
        for _ in range(rounds):
            s = run_round(state, sg, model, emit, caudit, now=clock["t"])
            summaries.append(s)
            state = auto_decide(s)
            for d in state["decisions"]:
                emit({"type": "swarm.human_decision", "round": s["round"], "request": d["request"]["id"],
                      "approved": d["decision"]["approved"], "approved_by": "principal"})
            for v in state["vaccines"][len(s["next_state"]["vaccines"]):]:
                emit({"type": "swarm.vaccine_approved", "round": s["round"], "pattern": v["pattern"]["id"],
                      "indicators": v["pattern"]["indicators"]})
            clock["t"] += 90
    return summaries
