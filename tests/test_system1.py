import json

from agent import system1


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_system1_skipped_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert system1.triage("anything") is None and not system1.blocks(None)


def test_system1_confidence_gated(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    answer = {"answers": {"kind": {"type": "choice", "choice": "payment_scam", "confidence": 0.93,
                                   "probabilities": {"payment_scam": 0.95, "legitimate": 0.05}}}}
    monkeypatch.setattr(system1.urllib.request, "urlopen", lambda req, timeout: _Resp(answer))
    r = system1.triage("A miracle cure, pay $500 to acct-3319")
    assert r["label"] == "payment_scam" and system1.blocks(r)
    answer["answers"]["kind"]["confidence"] = 0.4
    assert not system1.blocks(system1.triage("unclear"))


def test_round_with_system1_active(tmp_path, monkeypatch):
    """Jev's float confidence must never break the signed audit log."""
    from pathlib import Path
    from agent.sim import run_series
    from swarmauth import verify_log

    def fake(text, timeout=8.0):
        scam = any(w in text.lower() for w in ("miracle", "genome", "fee"))
        return {"label": "payment_scam" if scam else "legitimate", "confidence": 0.93 if scam else 0.97,
                "probabilities": {}, "model": "jev-latest"}

    monkeypatch.setattr(system1, "triage", fake)
    s = run_series(3, "federated", Path(tmp_path) / "ev.jsonl", start=1_790_000_000, scenario="health")
    assert sum(x["stats"]["system1_blocked"] for x in s) > 0
    assert all(x["stats"]["reply_rejected"] == 0 for x in s)
    for log in (Path(tmp_path) / "audit-federated").glob("*.jsonl"):
        assert verify_log(log).ok
