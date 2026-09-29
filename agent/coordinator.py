"""Coordinator AgentApp logic (runs on the SuperLink).

One call = one short round (well under SuperGrid's 5-minute task limit):

1. discover SuperNodes and map them to San Francisco neighborhoods
2. relay the market's offers (and the Arena's attacks) as signed envelopes
3. collect verified proposals from every node
4. triage blocked attacks into candidate vaccines (Endeavor proposes the
   indicator, code validates it, a human approves it later)
5. aggregate judgements for the institution — never records
6. emit live events and hand the next round's state back to the director
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional

from swarmauth import ApprovalRequest, AuditLog, Pattern
from swarmmind.envelope import seal
from swarmmind.flower import SwarmGrid

from . import trust, world
from .models import ModelClient
from .participant import leak_check

Emit = Callable[[dict[str, Any]], None]

TRIAGE_INSTRUCTIONS = (
    "You are the security triage agent of a federation of delegate agents. A message either tricked "
    "a delegate (the mandate blocked it) or was flagged by delegates as a scam. Extract ONE short "
    "indicator phrase (2-5 words, copied verbatim from the message) that would catch variants "
    "of the same scam but never a normal loan offer. "
    'Output: {"phrase": "...", "family": "<short label>", "why": "<= 20 words"}'
)
TRIAGE_BATCH_INSTRUCTIONS = (
    "You are the security triage agent of a federation of delegate agents. Each message below either "
    "tricked a delegate (the mandate blocked it) or was flagged by delegates as a scam. For EACH message "
    "extract ONE short indicator phrase (2-5 words, copied verbatim from that message) that would catch "
    "variants of the same scam but never a legitimate offer. "
    'Output: {"items":[{"id":"<message id>","phrase":"...","family":"<label>","why":"<= 15 words"}]}'
)
INSIGHT_INSTRUCTIONS = (
    "You summarise feedback from delegate agents about an offer (a study or a product) for the institution "
    "that made it. You only see aggregated judgements, never personal data. "
    'Output: {"headline": "<= 12 words", "insights": ["<= 15 words", "..."], "risk": "<= 20 words"}'
)
CUE_WORDS = ("ignore", "miracle", "full", "verification", "unfreeze", "override", "unlock", "frozen", "fee", "upload")


def _fallback_phrase(text: str, legit_text: str) -> Optional[str]:
    """Rule-based indicator: the first 2-gram that starts with the strongest cue word."""
    words = re.findall(r"[a-zA-Z][a-zA-Z\-']+", text.lower())
    for cue in CUE_WORDS:
        for i in range(len(words) - 1):
            if words[i] == cue:
                gram = f"{words[i]} {words[i + 1]}"
                if gram in text.lower() and gram not in legit_text.lower():
                    return gram
    return None


def _valid_phrase(phrase: Any, text: str, legit_text: str) -> Optional[str]:
    if not isinstance(phrase, str):
        return None
    p = phrase.strip().lower()
    if not (6 <= len(p) <= 48) or p not in text.lower() or p in legit_text.lower():
        return None
    return p


def assign_hoods(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map node ids to neighborhoods: by node name when it matches, else round-robin."""
    hood_ids = [h["id"] for h in world.NEIGHBORHOODS]
    mapping: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    free = list(hood_ids)
    for n in nodes:
        name = (n.get("name") or "").lower()
        match = next((h for h in free if h in name), None)
        if match:
            mapping[n["id"]].append(match)
            free.remove(match)
    ids = [n["id"] for n in nodes]
    for i, h in enumerate(free):
        mapping[ids[i % len(ids)]].append(h)
    return {k: v for k, v in mapping.items() if v}


def run_round(state: dict[str, Any], sg: SwarmGrid, coord_model: ModelClient, emit: Emit,
              audit: AuditLog, now: Optional[int] = None, pull_timeout: float = 150.0) -> dict[str, Any]:
    now = int(time.time()) if now is None else now
    sc = world.use(state.get("scenario"))
    r = int(state.get("round", 1))
    mode = state.get("mode", "federated")
    per_hood = int(state.get("per_hood", 8))
    t0 = time.monotonic()
    emit({"type": "swarm.round_start", "round": r, "mode": mode, "scenario": sc["id"],
          "coordinator_model": coord_model.model if coord_model.available else "fallback"})

    cm = trust.coordinator_mandate(now)
    nodes = sg.nodes()
    if not nodes:
        emit({"type": "swarm.no_nodes", "round": r})
        raise RuntimeError("no SuperNodes available in this federation")
    mapping = assign_hoods(nodes)
    emit({"type": "swarm.nodes", "round": r, "nodes": [
        {**n, "hoods": mapping.get(n["id"], [])} for n in nodes]})
    audit.append("round_start", "coordinator", {"round": r, "mode": mode, "nodes": len(nodes)})

    # offers: registered businesses sign with registrar mandates; impostors cannot
    plan = world.plan_round(r, [h for hs in mapping.values() for h in hs])
    truth = {}
    sealed: dict[str, list[str]] = {}
    for hood, offers in plan.items():
        sealed[hood] = []
        for o in offers:
            truth[o["id"]] = o["family"]
            body = {k: v for k, v in o.items() if k != "family"}  # ground truth never leaves
            sender = o["sender"]
            if sender in sc["registered"]:
                env = seal(trust.business_key(sender), "propose", body,
                           [trust.business_mandate(sender, now)], ts=now, round=r)
            else:
                env = seal(trust.business_key(sender), "propose", body, [], ts=now, round=r)
            sealed[hood].append(env.to_json())
            emit({"type": "swarm.offer", "round": r, "hood": hood, "offer": o["id"], "sender": sender,
                  "title": o["title"], "attack": o["family"] is not None})

    vaccines = state.get("vaccines", [])
    decisions = state.get("decisions", [])
    messages = []
    for node_id, hoods in mapping.items():
        node_vaccines = vaccines if mode == "federated" else [
            v for v in vaccines if v.get("origin_hood") in hoods]
        body = {"round": r, "mode": mode, "per_hood": per_hood, "scenario": sc["id"],
                "hoods": {h: {"offers": sealed[h]} for h in hoods},
                "vaccines": node_vaccines,
                "decisions": [d for d in decisions if d["request"]["action"].get("hood") in hoods]}
        messages.append((node_id, seal(trust.coordinator(), "assign", body, [cm], ts=now, round=r)))
    ids = sg.send(messages)
    emit({"type": "swarm.assigned", "round": r, "messages": len([i for i in ids if i])})

    replies, pending = sg.collect([i for i in ids if i], timeout=pull_timeout)
    results, node_audits, leaks, rejected_replies = [], [], 0, 0
    for inc in replies:
        if inc.opened is None:
            rejected_replies += 1
            emit({"type": "swarm.reply_rejected", "round": r, "src_node": inc.src_node_id,
                  "code": inc.error.code if inc.error else "?"})
            continue
        body = inc.opened.env.body
        leaks += leak_check(json.dumps(body), [res["hood"] for res in body.get("results", [])], per_hood)
        node_audits.append({"node": inc.src_node_id, "entries": body.get("audit", [])})
        for res in body.get("results", []):
            results.append(res)
            emit({"type": "swarm.hood_result", "round": r, "hood": res["hood"], "stats": res["stats"],
                  "delegates": [{"id": d["id"], "state": d["state"], "committed": d.get("committed", False),
                                 "last": (d["events"][-1] if d["events"] else None)} for d in res["delegates"]],
                  "identity_rejected": res["identity_rejected"], "commits": res["commits"],
                  "vaccines_active": res["vaccines_active"]})
    if pending:
        emit({"type": "swarm.timeout", "round": r, "pending": pending})

    # --- scoring against ground truth (coordinator-side only) -------------------
    attacks_delivered = sum(
        res["stats"]["delegates"] * sum(1 for o in plan[res["hood"]] if o["family"] and o["sender"] in sc["registered"])
        for res in results)
    stats = {k: sum(res["stats"][k] for res in results) for k in
             ("screened", "fooled", "blocked", "awaiting_human", "identity_rejected", "model_calls",
              "system1_blocked", "flagged")}
    stats.update({"attack_exposures": attacks_delivered, "harmful_executed": 0, "personal_data_leaks": leaks,
                  "reply_rejected": rejected_replies})

    # --- triage: blocked attacks become candidate vaccines ---------------------
    # federated: one shared immune memory; isolated: each neighborhood learns alone
    def scope_of(hood: str) -> str:
        return "all" if mode == "federated" else hood

    known: dict[str, set[str]] = {}
    for v in vaccines:
        known.setdefault(scope_of(v.get("origin_hood", "")), set()).update(v["pattern"]["indicators"])
    candidates = []
    seen_offers = set()
    pending_texts = {rep["offer"]: rep["text"] for res in results for rep in res["reports"]
                     if rep.get("payee") not in sc["trusted"] and rep.get("sender") != sc["sponsor"]
                     and not (rep["rule"] == "FLAGGED_BY_DELEGATE" and rep["count"] < 2)}
    if pending_texts:
        emit({"type": "swarm.coordinator_step", "round": r, "step": "triage",
              "model": coord_model.model if coord_model.available else "rules", "items": len(pending_texts)})
    batch = coord_model.json(TRIAGE_BATCH_INSTRUCTIONS, json.dumps(
        {"messages": [{"id": k, "text": v} for k, v in pending_texts.items()]})) if pending_texts else None
    triaged = {str(i.get("id")): i for i in (batch or {}).get("items", []) if isinstance(i, dict)}
    for res in results:
        for rep in res["reports"]:
            # guardrails against false positives: never vaccinate a sender or payee the mandates trust,
            # and a mere flag needs at least two delegates behind it
            if rep.get("payee") in sc["trusted"] or rep.get("sender") == sc["sponsor"]:
                emit({"type": "swarm.vaccine_skipped", "round": r, "offer": rep["offer"], "reason": "trusted sender"})
                continue
            if rep["rule"] == "FLAGGED_BY_DELEGATE" and rep["count"] < 2:
                continue
            sk = scope_of(rep["hood"])
            if (sk, rep["offer"]) in seen_offers:
                continue
            seen_offers.add((sk, rep["offer"]))
            known_here = known.setdefault(sk, set())
            out = triaged.get(rep["offer"])
            phrase = _valid_phrase((out or {}).get("phrase"), rep["text"], sc["legit"]["text"])
            decided_by = coord_model.model if phrase else "rules"
            phrase = phrase or _fallback_phrase(rep["text"], sc["legit"]["text"])
            indicators = [i for i in [rep["payee"].lower() if rep.get("payee") else None, phrase]
                          if i and i not in known_here]
            if not indicators:
                continue
            known_here.update(indicators)
            pats = []
            if rep.get("payee") and rep["payee"].lower() in indicators:
                pats.append(Pattern.create(trust.coordinator(), "payee", [rep["payee"]],
                                           f"payment redirect to {rep['payee']}"))
            if phrase and phrase in indicators:
                pats.append(Pattern.create(trust.coordinator(), "phrase", [phrase],
                                           (out or {}).get("why") or f"scam cue '{phrase}'"))
            for pat in pats:
                req = ApprovalRequest(agent=trust.coordinator().public.x, reason="VACCINE",
                                      action={"type": "adopt_pattern", "pattern_id": pat.id, "kind": pat.kind,
                                              "indicators": pat.indicators, "origin_hood": rep["hood"],
                                              "evidence": {"offer": rep["offer"], "blocked": rep["count"],
                                                           "rule": rep["rule"]}},
                                      id=f"vax-{pat.id}", created=now)
                candidates.append({"pattern": pat.to_dict(), "request": req.to_dict(), "origin_hood": rep["hood"],
                                   "decided_by": decided_by})
                emit({"type": "swarm.vaccine_candidate", "round": r, "pattern": pat.to_dict(),
                      "origin_hood": rep["hood"], "decided_by": decided_by})
                audit.append("vaccine_candidate", "coordinator", {"pattern": pat.id, "decided_by": decided_by})

    # --- judgements for the institution (never records) ------------------------
    stances: dict[str, int] = {}
    notes = []
    for res in results:
        for d in res["delegates"]:
            for e in d["events"]:
                if e["offer"].endswith("-legit"):
                    stances[e.get("stance", "neutral")] = stances.get(e.get("stance", "neutral"), 0) + 1
                    if e.get("note"):
                        notes.append(e["note"])
    emit({"type": "swarm.coordinator_step", "round": r, "step": "insight",
          "model": coord_model.model if coord_model.available else "rules"})
    model_insight = coord_model.json(INSIGHT_INSTRUCTIONS, json.dumps({
        "product": sc["legit"]["title"], "stances": stances, "sample_notes": notes[:40]}))
    insight = model_insight or {"headline": f"{stances.get('interested', 0)} delegates interested",
                                "insights": [f"{k}: {v}" for k, v in sorted(stances.items())], "risk": ""}
    insight["decided_by"] = coord_model.model if model_insight else "rules"
    emit({"type": "swarm.insight", "round": r, "institution": sc["sponsor"], "stances": stances, "insight": insight})

    approvals = [a for res in results for a in res["approvals"]]
    for a in approvals:
        emit({"type": "swarm.approval_needed", "round": r, "request": a})
    commits = [c for res in results for c in res["commits"]]
    audit.append("round_end", "coordinator", {"round": r, "stats": stats})
    summary = {
        "type": "swarm.round_complete", "round": r, "mode": mode, "stats": stats,
        "elapsed_s": round(time.monotonic() - t0, 1), "truth": truth,
        "next_state": {"round": r + 1, "mode": mode, "per_hood": per_hood, "scenario": sc["id"], "vaccines": vaccines,
                       "pending_approvals": approvals, "vaccine_candidates": candidates},
        "commits": commits, "node_audits": node_audits,
        "coordinator_error": coord_model.last_error,
    }
    emit(summary)
    return summary
