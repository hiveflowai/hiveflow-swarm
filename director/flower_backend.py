"""Start one round as a Flower AgentApp run and stream its events back.

Uses the same Control API calls as ``flwr chat`` (StartRun with a user prompt,
then StreamRunEvents). Requires ``flwr login <connection>`` beforehand for
SuperGrid.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

from flwr.cli.build import build_fab_from_disk
from flwr.cli.chat.chat_app import parse_task_event, start_chat_run
from flwr.cli.flower_config import read_superlink_connection
from flwr.cli.local_superlink import ensure_local_superlink
from flwr.cli.utils import init_http_client_from_connection
from flwr.proto.control_pb2 import StreamRunEventsRequest  # pylint: disable=E0611

from .stage import stage

ROOT = Path(__file__).resolve().parents[1]


def run_round(state: dict[str, Any], connection: str, on_event: Callable[[dict[str, Any]], None],
              federation: Optional[str] = None) -> Optional[dict[str, Any]]:
    conn = ensure_local_superlink(read_superlink_connection(connection))
    stub = init_http_client_from_connection(conn)
    summary = None
    try:
        fab = build_fab_from_disk(stage())
        run_id, _ = start_chat_run(stub, json.dumps(state), federation or conn.federation, None,
                                   fab_content=fab)
        on_event({"type": "director.run_started", "run_id": run_id, "connection": connection,
                  "round": state.get("round")})
        for res in stub.StreamRunEvents(StreamRunEventsRequest(run_id=run_id)):
            etype, payload = parse_task_event(res.task_event)
            if etype.startswith("swarm."):
                on_event(payload)
                if etype == "swarm.round_complete":
                    summary = payload
            elif etype in ("error", "response.failed"):
                on_event({"type": "director.run_failed", "run_id": run_id, "detail": payload})
            elif etype in ("response.completed", "response.incomplete") and summary is not None:
                break
    finally:
        stub.close()
    return summary
