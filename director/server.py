"""Director: serves the map, streams events, turns human clicks into signed decisions, runs rounds.

    uv run python -m director.server --backend sim            # offline, in-process federation
    uv run python -m director.server --backend flower --connection sf-local
    uv run python -m director.server --backend flower --connection supergrid

Open http://localhost:8080/?live=1 (map) and http://<lan-ip>:8080/phone (approvals).
The human keys live here, on the operator's machine; the phone is only a channel.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import socket
import threading
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from swarmauth import AuditLog, decide
from swarmauth.approval import ApprovalRequest

from agent import trust, world

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"


class Director:
    def __init__(self, backend: str, connection: str, mode: str, coordinator_model: str | None,
                 scenario: str = "health") -> None:
        self.backend, self.connection = backend, connection
        self.federation = None
        self.lock = threading.Lock()
        self.subscribers: list[queue.Queue[str]] = []
        self.history: list[str] = []
        self.state: dict[str, Any] = {"round": 1, "mode": mode, "per_hood": 8, "vaccines": [], "decisions": [],
                                      "scenario": scenario}
        if coordinator_model:
            self.state["coordinator_model"] = coordinator_model
        self.pending_commitments: dict[str, dict[str, Any]] = {}
        self.pending_vaccines: dict[str, dict[str, Any]] = {}
        self.running = False
        self.log = (ROOT / "runs" / f"live-{int(time.time())}.jsonl")
        self.log.parent.mkdir(exist_ok=True)
        self._sim = None

    # -- events ------------------------------------------------------------------
    def emit(self, event: dict[str, Any]) -> None:
        if event.get("type") == "swarm.round_complete":
            slim = dict(event)
            slim["node_audits"] = [{"node": a["node"], "entries": a["entries"][-12:]} for a in event.get("node_audits", [])]
            event = slim
            ns = event["next_state"]
            with self.lock:
                self.state["vaccines"] = ns["vaccines"]
                self.state["round"] = ns["round"]
                self.pending_commitments = {a["id"]: a for a in ns["pending_approvals"]}
                self.pending_vaccines = {c["request"]["id"]: c for c in ns["vaccine_candidates"]}
        data = json.dumps(event, ensure_ascii=False)
        with self.lock:
            self.history.append(data)
            with self.log.open("a") as f:
                f.write(data + "\n")
            for q in list(self.subscribers):
                q.put(data)

    # -- humans --------------------------------------------------------------------
    def decide(self, kind: str, rid: str, approved: bool) -> dict[str, Any]:
        with self.lock:
            if kind == "commitment" and rid in self.pending_commitments:
                req_d = self.pending_commitments.pop(rid)
                act = req_d["action"]
                person = next(d for d in world.delegates_for(act["hood"], scenario=self.state["scenario"])
                              if d.id == act["delegate"])
                dec = decide(person.principal, ApprovalRequest(**req_d), approved)
                self.state["decisions"].append({"request": req_d, "decision": dec.to_dict()})
                out = {"type": "swarm.human_decision", "request": rid, "approved": approved,
                       "approver": dec.approver_kid[:12]}
            elif kind == "vaccine" and rid in self.pending_vaccines:
                cand = self.pending_vaccines.pop(rid)
                dec = decide(trust.security_officer(), ApprovalRequest(**cand["request"]), approved)
                if approved:
                    self.state["vaccines"].append({"pattern": cand["pattern"], "request": cand["request"],
                                                   "decision": dec.to_dict(), "origin_hood": cand["origin_hood"]})
                out = {"type": "swarm.vaccine_approved" if approved else "swarm.human_decision",
                       "pattern": cand["pattern"]["id"], "indicators": cand["pattern"]["indicators"],
                       "request": rid, "approved": approved}
            else:
                return {"ok": False, "error": "unknown or already decided"}
        self.emit(out)
        return {"ok": True}

    def pending(self) -> dict[str, Any]:
        with self.lock:
            return {"round": self.state["round"], "running": self.running,
                    "commitments": list(self.pending_commitments.values()),
                    "vaccines": list(self.pending_vaccines.values())}

    # -- rounds ----------------------------------------------------------------------
    def next_round(self) -> dict[str, Any]:
        with self.lock:
            if self.running:
                return {"ok": False, "error": "a round is already running"}
            self.running = True
            state = {k: v for k, v in self.state.items()}
            self.state["decisions"] = []
            self.pending_commitments, self.pending_vaccines = {}, {}  # undecided = denied (timeout)
        threading.Thread(target=self._run, args=(state,), daemon=True).start()
        return {"ok": True, "round": state["round"]}

    def _run(self, state: dict[str, Any]) -> None:
        try:
            if self.backend == "sim":
                from agent.coordinator import run_round
                from agent.models import ModelClient
                from agent.sim import InProcFederation
                from swarmmind.flower import SwarmGrid

                if self._sim is None:
                    audit_dir = ROOT / "runs" / "live-audit"
                    audit_dir.mkdir(parents=True, exist_ok=True)
                    fed = InProcFederation([f"sf-{h['id']}" for h in world.NEIGHBORHOODS], audit_dir,
                                           lambda: int(time.time()))
                    caudit = AuditLog(audit_dir / "coordinator.jsonl", trust.coordinator())
                    self._sim = (SwarmGrid(fed, trust.coordinator(), caudit, trust.federation_roots(), "coordinator"),
                                 caudit)
                sg, caudit = self._sim
                run_round(state, sg, ModelClient("none"), self.emit, caudit)
            else:
                from .flower_backend import run_round as flower_round
                flower_round(state, self.connection, self.emit, federation=self.federation)
        except Exception as err:  # noqa: BLE001
            traceback.print_exc()
            self.emit({"type": "director.run_failed", "detail": str(err)[:300]})
        finally:
            with self.lock:
                self.running = False


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def start_tunnel(port: int) -> str:
    """Quick tunnel de cloudflared (trycloudflare.com) hacia el director; devuelve la URL pública."""
    if not shutil.which("cloudflared"):
        raise SystemExit("--tunnel requiere cloudflared (brew install cloudflared)")
    proc = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 30
    for line in proc.stderr:  # cloudflared imprime la URL en stderr
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            threading.Thread(target=lambda: [None for _ in proc.stderr], daemon=True).start()  # drena el pipe
            return m.group(0)
        if time.time() > deadline:
            break
    proc.kill()
    raise SystemExit("cloudflared no devolvió una URL de túnel")


def make_handler(director: Director, port: int, phone_url: str):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(DASH), **kw)

        def log_message(self, *a):  # quiet
            pass

        def _json(self, obj: Any, code: int = 200) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/events"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.end_headers()
                q: queue.Queue[str] = queue.Queue()
                with director.lock:
                    backlog = list(director.history)
                    director.subscribers.append(q)
                try:
                    for data in backlog:
                        self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    while True:
                        try:
                            data = q.get(timeout=15)
                            self.wfile.write(f"data: {data}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with director.lock:
                        director.subscribers.remove(q)
                return
            if self.path.startswith("/api/pending"):
                return self._json(director.pending())
            if self.path.startswith("/api/info"):
                return self._json({"scenario": director.state["scenario"], "phone_url": phone_url, "backend": director.backend,
                                   "connection": director.connection})
            if self.path.startswith("/phone"):
                self.path = "/phone.html"
            return super().do_GET()

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path.startswith("/api/decide_all"):
                p = director.pending()
                for c in p["commitments"]:
                    director.decide("commitment", c["id"], True)
                for v in p["vaccines"]:
                    director.decide("vaccine", v["request"]["id"], True)
                return self._json({"ok": True})
            if self.path.startswith("/api/decide"):
                return self._json(director.decide(body.get("kind", ""), body.get("id", ""), bool(body.get("approved"))))
            if self.path.startswith("/api/round"):
                return self._json(director.next_round())
            return self._json({"ok": False, "error": "not found"}, 404)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["sim", "flower"], default="sim")
    ap.add_argument("--connection", default="sf-local")
    ap.add_argument("--mode", choices=["federated", "isolated"], default="federated")
    ap.add_argument("--coordinator-model", default=None)
    ap.add_argument("--scenario", choices=["health", "finance"], default="health")
    ap.add_argument("--federation", default=None, help="e.g. @johnolven/sf-hospitals on SuperGrid")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--public-url", default=os.environ.get("SWARM_PUBLIC_URL"),
                    help="URL pública (túnel) para el QR del teléfono, p. ej. https://swarm.hiveflow.ai")
    ap.add_argument("--tunnel", action="store_true", help="levanta un quick tunnel de cloudflared y lo usa en el QR")
    args = ap.parse_args()
    base = args.public_url or (start_tunnel(args.port) if args.tunnel else f"http://{lan_ip()}:{args.port}")
    phone_url = base.rstrip("/") + "/phone"
    director = Director(args.backend, args.connection, args.mode, args.coordinator_model, args.scenario)
    director.federation = args.federation or ("@johnolven/sf-hospitals" if args.connection == "supergrid" else None)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(director, args.port, phone_url))
    print(f"map:   http://localhost:{args.port}/?live=1")
    print(f"phone: {phone_url}")
    print(f"backend={args.backend} connection={args.connection} mode={args.mode}")
    server.serve_forever()


if __name__ == "__main__":
    main()
