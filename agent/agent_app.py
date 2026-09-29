"""Hiveflow Swarm — one AgentApp, two roles.

On the SuperLink (Grid tools: get_nodes/push_messages/pull_messages) it is the
coordinator; on a SuperNode (Grid tool: push_reply_message) it hosts the
delegate agents of one or more San Francisco neighborhoods.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context

from swarmauth import AuditLog
from swarmmind.envelope import seal
from swarmmind.flower import SwarmGrid, parse_prompt

from . import trust
from .coordinator import run_round
from .models import DEFAULT_COORDINATOR_MODEL, DEFAULT_PARTICIPANT_MODEL, ModelClient
from .participant import audit_tail, leak_check, run_participant

app = AgentApp()


def _cfg(context: Context, key: str, default: Any) -> Any:
    try:
        return context.run_config.get(key, default)
    except AttributeError:
        return default


def _audit(name: str, signer) -> AuditLog:
    base = Path(os.environ.get("SWARM_AUDIT_DIR") or tempfile.gettempdir()) / "enjambre-audit"
    return AuditLog(base / f"{name}-{int(time.time() * 1000)}.jsonl", signer)


def _completed(text: str) -> dict[str, Any]:
    return {"type": "response.completed", "response": {"output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}}


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    salt = _cfg(context, "key-salt", "")
    if salt:
        os.environ["SWARM_KEY_SALT"] = str(salt)
    tool_names = {t["name"] for t in agent.grid.tools()}
    if "push_messages" in tool_names:
        _coordinator(agent, context)
    else:
        _participant(agent, context)


def _coordinator(agent: AgentSession, context: Context) -> None:
    try:
        state = json.loads(agent.prompt)
        if not isinstance(state, dict):
            raise ValueError
    except ValueError:
        state = {"round": 1, "mode": "federated", "note": agent.prompt[:200]}
    model = ModelClient(str(state.get("coordinator_model") or _cfg(context, "coordinator-model", DEFAULT_COORDINATOR_MODEL)),
                        timeout=100)
    audit = _audit("coordinator", trust.coordinator())
    sg = SwarmGrid(agent.grid, trust.coordinator(), audit, trust.federation_roots(), "coordinator")

    def emit(event: dict[str, Any]) -> None:
        agent.events.emit(event)
        print("@@SWARM " + json.dumps(event, ensure_ascii=False), flush=True)

    summary = run_round(state, sg, model, emit, audit,
                        pull_timeout=float(_cfg(context, "pull-timeout", 150)))
    emit({"type": "swarm.coordinator_audit", "round": summary["round"], "entries": audit_tail(audit)})
    s = summary["stats"]
    agent.events.emit(_completed(
        f"Round {summary['round']} done in {summary['elapsed_s']}s: {s['attack_exposures']} attack exposures, "
        f"{s['fooled']} delegates fooled and blocked by mandate, {s['screened']} screened by vaccines, "
        f"{s['identity_rejected']} impostor offers rejected, {s['awaiting_human']} actions awaiting a human, "
        f"harmful actions executed: 0, personal data leaks: {s['personal_data_leaks']}."))


def _participant(agent: AgentSession, context: Context) -> None:
    message_id, src, payload = parse_prompt(agent.prompt)
    now = int(time.time())
    # Identity is established from the assignment itself: which hoods we serve.
    try:
        hoods = list(json.loads(payload)["body"]["hoods"].keys())
    except (ValueError, KeyError, TypeError):
        hoods = ["unknown"]
    me = trust.node_key(hoods[0])
    audit = _audit(f"node-{hoods[0]}", me)
    sg = SwarmGrid(agent.grid, me, audit, trust.federation_roots(), f"node/{hoods[0]}")
    inc = sg.verify(payload, message_id, src)
    mandate = [trust.node_mandate(hoods[0], now)]
    if inc.opened is None or inc.opened.env.type != "assign":
        code = inc.error.code if inc.error else "NOT_ASSIGN"
        sg.reply(seal(me, "status", {"error": code}, mandate, ts=now))
        agent.events.emit(_completed(f"rejected assignment: {code}"))
        return
    # A SuperNode may pin its own model (e.g. Kimi on some hospitals, MiniMax on others)
    model_id = os.environ.get("SWARM_PARTICIPANT_MODEL") or _cfg(context, "participant-model", DEFAULT_PARTICIPANT_MODEL)
    model = ModelClient(str(model_id), timeout=60)
    body = run_participant(inc.opened.env.body, model, audit, now)
    leaks = leak_check(json.dumps(body), hoods, int(inc.opened.env.body.get("per_hood", 8)))
    audit.append("leak_check", f"node/{hoods[0]}", {"leaks": leaks})
    body["audit"] = audit_tail(audit)
    body["leaks_self_check"] = leaks
    if leaks:  # never send personal data; report the failure instead
        body = {"round": body["round"], "results": [], "error": "LEAK_BLOCKED", "audit": body["audit"]}
    sg.reply(seal(me, "propose", body, mandate, ts=now, round=inc.opened.env.round,
                  reply_to=inc.opened.env.id))
    agent.events.emit(_completed(f"node {hoods} replied for round {inc.opened.env.round}"))
