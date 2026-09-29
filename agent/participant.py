"""Neighborhood node: hosts delegate agents next to their principals' data.

Per round it: verifies every offer's sender identity, screens offers with
adopted vaccines (before any model sees them), lets the model *propose* what
each delegate would do, and runs every proposal through SwarmAuth policy.
Only judgements leave the node — never the person's data.
"""

from __future__ import annotations

import json
import random
from typing import Any, Optional

from swarmauth import (
    ASK, DENY, Action, ApprovalDecision, ApprovalRequest, AuditLog, Pattern, Policy,
    verify_chain, verify_decision,
)
from swarmauth.mandate import MandateError
from swarmmind.envelope import Envelope, EnvelopeError, open_envelope

from . import system1, trust, world
from .models import ModelClient

HARMFUL_RULES = {"PAYEE_NOT_ALLOWED", "MAX_AMOUNT", "OUT_OF_MANDATE", "PURPOSE"}
PRIVATE_KEYS = world.PRIVATE_KEYS

DELEGATE_INSTRUCTIONS = (
    "You simulate delegate agents. Each delegate represents one person (a patient or a customer) and acts "
    "only in that person's interest under a signed mandate. Never share raw medical or financial data. For every (delegate, offer) pair decide: "
    "'ignore', 'opinion' (just give feedback) or 'act' (do what the offer asks). "
    "stance is one of interested|neutral|not_interested|suspicious; note is <= 15 words. "
    'Output: {"decisions":[{"delegate":"<id>","offer":"<id>","response":"...","stance":"...","note":"..."}]}'
)


def _sim_decision(d: world.Delegate, offer: dict[str, Any], r: int, harmful: bool) -> dict[str, Any]:
    rng = random.Random(f"{r}/{d.id}/{offer['id']}")
    if harmful:
        if rng.random() < d.gullibility:
            return {"response": "act", "stance": "interested", "note": "sounds urgent, doing it"}
        return {"response": "opinion", "stance": "suspicious", "note": "looks like a scam"}
    wants = d.public_profile["goal"] in world.S()["wants_goals"]
    if rng.random() < (0.22 if wants else 0.04):
        return {"response": "act", "stance": "interested",
                "note": "fits my goal, pre-screen" if world.S()["id"] == "health" else "fits my goal, pre-apply"}
    stance = rng.choice(["neutral", "interested", "not_interested"])
    notes = {"health": {"neutral": "want to ask my doctor first", "interested": "would join if eligible",
                        "not_interested": "not the right time for me"},
             "finance": {"neutral": "maybe later", "interested": "rate is ok if no fees",
                         "not_interested": "can't take more debt now"}}[world.S()["id"]]
    return {"response": "opinion", "stance": stance, "note": notes[stance]}


def _model_decisions(model: ModelClient, hood: str, items: list[tuple[world.Delegate, dict[str, Any]]]
                     ) -> dict[tuple[str, str], dict[str, Any]]:
    if not items or not model.available:
        return {}
    ds = {d.id: d for d, _ in items}
    offers = {o["id"]: o for _, o in items}
    prompt = json.dumps({
        "neighborhood": hood,
        "delegates": [{"id": d.id, "profile": d.public_profile["condition"], "goal": d.public_profile["goal"],
                       "trait": d.public_profile["trait"], "hint": d.hint()}
                      for d in ds.values()],
        "offers": [{"id": o["id"], "from": o["sender"], "title": o["title"], "text": o["text"]}
                   for o in offers.values()],
        "pairs": [[d.id, o["id"]] for d, o in items],
    })
    out = model.json(DELEGATE_INSTRUCTIONS, prompt) or {}
    res: dict[tuple[str, str], dict[str, Any]] = {}
    for item in out.get("decisions", []) if isinstance(out.get("decisions"), list) else []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("delegate")), str(item.get("offer")))
        if key[0] in ds and key[1] in offers and item.get("response") in ("ignore", "opinion", "act"):
            res[key] = {"response": item["response"], "stance": str(item.get("stance", "neutral"))[:20],
                        "note": str(item.get("note", ""))[:120]}
    return res


def _adopt_vaccines(policy: Policy, vaccines: list[dict[str, Any]], audit: AuditLog, actor: str) -> list[str]:
    adopted = []
    for v in vaccines:
        try:
            pat = Pattern.from_dict(v["pattern"])
            req = ApprovalRequest(**v["request"])
            dec = ApprovalDecision.from_dict(v["decision"])
        except (KeyError, TypeError):
            continue
        if policy.adopt(pat, dec, req, trust.vaccine_approvers()):
            adopted.append(pat.id)
    if adopted:
        audit.append("vaccines_adopted", actor, {"patterns": adopted})
    return adopted


def process_hood(hood: str, spec: dict[str, Any], ctx: dict[str, Any], model: ModelClient,
                 audit: AuditLog, now: int) -> dict[str, Any]:
    r = ctx["round"]
    actor = f"node/{hood}"
    dels = world.delegates_for(hood, ctx.get("per_hood", 8))
    grants = {}
    for d in dels:
        try:
            grants[d.id] = verify_chain([d.mandate(now)], {d.principal.kid}, now=now)
        except MandateError as err:  # pragma: no cover - synthetic mandates are valid
            audit.append("mandate_invalid", actor, {"delegate": d.id, "code": err.code})

    policy = Policy()
    adopted = _adopt_vaccines(policy, ctx.get("vaccines", []), audit, actor)

    # 1) identity layer: who is really sending these offers?
    offers, identity_rejected = [], []
    for raw in spec.get("offers", []):
        try:
            op = open_envelope(raw, trust.market_roots(), now=now)
            offers.append({**op.env.body, "_sender_kid": op.env.sender_kid})
        except EnvelopeError as err:
            try:
                claimed = Envelope.from_json(raw).body
            except EnvelopeError:
                claimed = {}
            identity_rejected.append({"offer": claimed.get("id"), "claimed_sender": claimed.get("sender"),
                                      "code": err.code})
            audit.append("offer_rejected", actor, {"offer": claimed.get("id"), "code": err.code})

    # 2) vaccine screening happens before any model sees the offer
    outcomes: dict[str, dict[str, Any]] = {d.id: {"id": d.id, "state": "green", "events": []} for d in dels}
    to_model: list[tuple[world.Delegate, dict[str, Any]]] = []
    screened = 0
    for d in dels:
        for o in offers:
            hit = policy.screen(text=o["text"], payee=o["ask"].get("payee"))
            if hit is not None:
                screened += 1
                outcomes[d.id]["events"].append({"offer": o["id"], "result": "screened", "rule": f"PATTERN:{hit.id}",
                                                 "decided_by": "code"})
                audit.append("screened", actor, {"delegate": d.id, "offer": o["id"], "pattern": hit.id})
            else:
                to_model.append((d, o))

    # 2b) System 1 (Jev): one typed triage per offer; a confident attack stops here
    approvals, reports, fooled, blocked, legit_acts = [], {}, 0, 0, 0
    s1 = {o["id"]: system1.triage(o["text"]) for o in offers if any(x is o for _, x in to_model)}
    s1_blocked = 0
    flagged = 0
    remaining = []
    for d, o in to_model:
        verdict = s1.get(o["id"])
        if system1.blocks(verdict):
            s1_blocked += 1
            conf_pct = int(round(verdict["confidence"] * 100))  # signed logs take integers only
            outcomes[d.id]["events"].append({"offer": o["id"], "result": "screened", "rule": f"SYSTEM1:{verdict['label']}",
                                             "confidence_pct": conf_pct, "decided_by": verdict["model"]})
            audit.append("system1_block", actor, {"delegate": d.id, "offer": o["id"], "label": verdict["label"],
                                                  "confidence_pct": conf_pct})
            reports.setdefault(o["id"], {"offer": o["id"], "sender": o["sender"], "title": o["title"], "text": o["text"],
                                         "payee": o["ask"].get("payee"), "rule": f"SYSTEM1:{verdict['label']}",
                                         "hood": hood, "count": 0})["count"] += 1
        else:
            remaining.append((d, o))
    to_model = remaining

    # 3) model proposes, policy decides
    proposals = _model_decisions(model, hood, to_model)
    for d, o in to_model:
        key = (d.id, o["id"])
        harmful_truth = o["ask"].get("payee") not in world.S()["trusted"]  # only used by the sim fallback
        p = proposals.get(key)
        decided_by = model.model if p else "sim"
        if p is None:
            p = _sim_decision(d, o, r, harmful_truth)
        ev = {"offer": o["id"], "result": p["response"], "stance": p["stance"], "note": p["note"],
              "decided_by": decided_by}
        if p["response"] == "act":
            ask = o["ask"]
            action = Action(scope=ask["scope"], amount=int(ask.get("amount", 0)), payee=ask.get("payee"),
                            purpose=world.S()["purpose"], text=o["text"])
            dec = policy.evaluate(grants.get(d.id), action)
            ev.update({"verdict": dec.verdict, "rule": dec.rule})
            if dec.verdict == DENY:
                blocked += 1
                if dec.rule in HARMFUL_RULES:
                    fooled += 1
                    ev["result"] = "blocked"
                    reports.setdefault(o["id"], {"offer": o["id"], "sender": o["sender"], "title": o["title"],
                                                 "text": o["text"], "payee": ask.get("payee"), "rule": dec.rule,
                                                 "hood": hood, "count": 0})["count"] += 1
            elif dec.verdict == ASK:
                req = ApprovalRequest(agent=d.agent_key.public.x, reason=dec.rule,
                                      mandate_id=grants[d.id].chain_ids[-1],
                                      action={**action.to_dict(), "offer_id": o["id"], "delegate": d.id,
                                              "hood": hood, "title": o["title"]},
                                      id=f"r{r}-{d.id}-{o['id']}", created=now)
                approvals.append(req.to_dict())
                ev["result"] = "awaiting_human"
                legit_acts += 1
            audit.append("decision", actor, {"delegate": d.id, "offer": o["id"], "verdict": dec.verdict,
                                             "rule": dec.rule, "decided_by": decided_by})
        if p["response"] != "act" and p.get("stance") == "suspicious":
            # a delegate that spots a scam also triggers the immune response (detection, not only victims)
            flagged += 1
            reports.setdefault(o["id"], {"offer": o["id"], "sender": o["sender"], "title": o["title"],
                                         "text": o["text"], "payee": o["ask"].get("payee"),
                                         "rule": "FLAGGED_BY_DELEGATE", "hood": hood, "count": 0})["count"] += 1
            audit.append("flagged", actor, {"delegate": d.id, "offer": o["id"], "decided_by": decided_by})
        outcomes[d.id]["events"].append(ev)

    # 4) signed human decisions from earlier rounds
    commits = []
    for item in ctx.get("decisions", []):
        req_d = item.get("request", {})
        act = req_d.get("action", {})
        if act.get("hood") != hood:
            continue
        d = next((x for x in dels if x.id == act.get("delegate")), None)
        if d is None:
            continue
        req = ApprovalRequest(**req_d)
        dec = ApprovalDecision.from_dict(item["decision"])
        ok = verify_decision(dec, req, {d.principal.kid})
        if ok and dec.approved:
            recheck = policy.evaluate(grants.get(d.id), Action(
                scope=act["scope"], amount=act["amount"], payee=act["payee"], purpose=act.get("purpose")))
            ok = recheck.verdict != DENY
        result = "committed" if ok and dec.approved else "declined" if ok else "invalid_approval"
        commits.append({"request": req.id, "delegate": d.id, "result": result, "title": act.get("title")})
        audit.append("commit", actor, {"request": req.id, "delegate": d.id, "result": result,
                                       "approver": dec.approver_kid})

    # 5) map state per delegate (worst event wins)
    rank = {"blocked": 4, "awaiting_human": 3, "screened": 2}
    for d in dels:
        evs = outcomes[d.id]["events"]
        worst = max((rank.get(e["result"], 0) for e in evs), default=0)
        outcomes[d.id]["state"] = {4: "red", 3: "amber", 2: "shield"}.get(worst, "green")
        for c in commits:
            if c["delegate"] == d.id and c["result"] == "committed":
                outcomes[d.id]["committed"] = True

    return {
        "hood": hood,
        "delegates": list(outcomes.values()),
        "approvals": approvals,
        "commits": commits,
        "reports": list(reports.values()),
        "identity_rejected": identity_rejected,
        "vaccines_active": adopted,
        "stats": {"delegates": len(dels), "offers": len(offers), "screened": screened, "fooled": fooled,
                  "blocked": blocked, "awaiting_human": len(approvals), "identity_rejected": len(identity_rejected),
                  "system1_blocked": s1_blocked, "flagged": flagged, "system1": "jev" if system1.available() else "none",
                  "model_calls": 1 if to_model and model.available else 0,
                  "model_error": (model.last_error or "")[:120]},
    }


def leak_check(payload: str, hoods: list[str], per_hood: int = 8) -> int:
    """Count private fields of local people that appear in an outgoing payload."""
    leaks = sum(payload.count(f'"{k}"') for k in PRIVATE_KEYS)
    for h in hoods:
        for d in world.delegates_for(h, per_hood):
            sig = json.dumps(d.private, sort_keys=True)
            leaks += payload.count(sig)
    return leaks


def run_participant(body: dict[str, Any], model: ModelClient, audit: AuditLog, now: int) -> dict[str, Any]:
    world.use(body.get("scenario"))
    ctx = {k: body.get(k) for k in ("round", "per_hood", "vaccines", "decisions", "mode")}
    ctx["vaccines"] = ctx["vaccines"] or []
    ctx["decisions"] = ctx["decisions"] or []
    hoods = body.get("hoods", {})
    results = [process_hood(h, spec, ctx, model, audit, now) for h, spec in hoods.items()]
    return {"round": ctx["round"], "results": results}


def audit_tail(audit: AuditLog, since_seq: int = 0) -> list[dict[str, Any]]:
    from swarmauth import read_log
    return [e for e in read_log(audit.path) if e["seq"] >= since_seq]


__all__ = ["run_participant", "leak_check", "audit_tail"]
_ = Optional  # keep typing import used in older Pythons
