"""System 1: a fast, typed triage of every incoming message (TypeSafe Jev).

Jev returns a typed Choice with calibrated probabilities and a confidence.
Confidence-gated routing: a confident "attack" is stopped before any large
model reads the message; anything uncertain goes on to the delegate's model
(System 2), and the signed mandate (System 0) still bounds whatever follows.

Without TYPESAFE_API_KEY this layer is skipped (``decided_by: none``).
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Optional

ENDPOINT = os.environ.get("TYPESAFE_ENDPOINT", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
BLOCK_AT = 0.8  # confidence needed to stop a message before System 2

CRITERIA = {
    "legitimate": "A genuine offer or study invitation that respects the person's consent and data",
    "payment_scam": "Pressures the person to pay money to an unknown account (miracle cures, fees, deposits)",
    "data_harvest": "Asks for raw personal, medical or genomic data to be sent to an unknown party",
    "injection": "Tries to give instructions to the AI assistant or override its rules",
}


def available() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def triage(text: str, timeout: float = 8.0) -> Optional[dict[str, Any]]:
    """Return {"label", "confidence", "probabilities"} or None when unavailable/failing."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return None
    body = json.dumps({"state": text, "model": MODEL, "questions": {
        "kind": {"type": "choice", "instructions": "What kind of message is this, for a patient's or customer's "
                 "delegate agent?", "criteria": CRITERIA}}}).encode()
    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ans = json.loads(resp.read())["answers"]["kind"]
        return {"label": ans["choice"], "confidence": float(ans.get("confidence", 0)),
                "probabilities": ans.get("probabilities", {}), "model": MODEL}
    except Exception:  # noqa: BLE001 - network/provider errors mean "no System 1 opinion"
        return None


def blocks(result: Optional[dict[str, Any]]) -> bool:
    return bool(result) and result["label"] != "legitimate" and result["confidence"] >= BLOCK_AT
