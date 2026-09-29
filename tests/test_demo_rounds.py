from pathlib import Path

import pytest

from swarmauth import verify_log

from agent.sim import run_series

NOW = 1_790_000_000


@pytest.mark.parametrize("scenario", ["health", "finance"])
def test_offline_federation_rounds(tmp_path: Path, scenario: str):
    fed = run_series(5, "federated", tmp_path / "fed.jsonl", start=NOW, scenario=scenario)
    iso = run_series(5, "isolated", tmp_path / "iso.jsonl", start=NOW, scenario=scenario)

    for s in fed + iso:
        assert s["stats"]["harmful_executed"] == 0
        assert s["stats"]["personal_data_leaks"] == 0
        assert s["stats"]["reply_rejected"] == 0

    # round 1 has no attacks, round 2 fools someone and produces vaccine candidates
    assert fed[0]["stats"]["fooled"] == 0
    assert fed[1]["stats"]["fooled"] > 0 and fed[1]["next_state"]["vaccine_candidates"]
    # the impostor "regulator" is rejected at the identity layer
    assert fed[3]["stats"]["identity_rejected"] > 0
    # sharing vaccines helps: fewer delegates fooled across rounds 3-5
    fooled_fed = sum(s["stats"]["fooled"] for s in fed[2:])
    fooled_iso = sum(s["stats"]["fooled"] for s in iso[2:])
    assert fooled_fed < fooled_iso, (fooled_fed, fooled_iso)
    assert sum(s["stats"]["screened"] for s in fed) > sum(s["stats"]["screened"] for s in iso)
    # human-approved commitments are executed in the following round
    assert any(c["result"] == "committed" for s in fed for c in s["commits"])

    for log in (tmp_path / "audit-federated").glob("*.jsonl"):
        assert verify_log(log).ok, log
