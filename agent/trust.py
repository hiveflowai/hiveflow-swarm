"""Who trusts whom in the demo federation (all keys synthetic)."""

from __future__ import annotations

from functools import lru_cache

from swarmauth import KeyPair, Mandate, issue

from . import world

MANDATE_TTL = 6 * 3600


@lru_cache(maxsize=None)
def officer() -> KeyPair:
    """Federation officer: human root that enrols the coordinator and nodes."""
    return world.key("human/federation-officer")


@lru_cache(maxsize=None)
def security_officer() -> KeyPair:
    """Human who approves vaccines (pattern adoption)."""
    return world.key("human/security-officer")


@lru_cache(maxsize=None)
def registrar() -> KeyPair:
    """Market registrar: vouches for the identity of businesses (not their honesty)."""
    return world.key("human/market-registrar")


@lru_cache(maxsize=None)
def coordinator() -> KeyPair:
    return world.key("agent/coordinator")


def node_key(hood: str) -> KeyPair:
    return world.key(f"agent/node/{hood}")


def business_key(sender: str) -> KeyPair:
    return world.key(f"business/{sender}")


def coordinator_mandate(now: int) -> Mandate:
    return issue(officer(), coordinator().public.x,
                 ["swarm:discover", "swarm:assign", "swarm:commit", "swarm:share", "swarm:status"],
                 ttl=MANDATE_TTL, purpose="federation-coordination", now=now)


def node_mandate(hood: str, now: int) -> Mandate:
    return issue(officer(), node_key(hood).public.x,
                 ["swarm:propose", "swarm:status", "swarm:share"],
                 ttl=MANDATE_TTL, purpose="federation-coordination", now=now)


def business_mandate(sender: str, now: int) -> Mandate:
    return issue(registrar(), business_key(sender).public.x, ["swarm:propose"],
                 ttl=MANDATE_TTL, purpose="market-offers", now=now)


def federation_roots() -> set[str]:
    return {officer().kid}


def market_roots() -> set[str]:
    return {registrar().kid}


def vaccine_approvers() -> set[str]:
    return {security_officer().kid}
