"""Real loopback HTTPS exercise, not a Substrate/Envoy implementation.

Uses Python stdlib + OpenSSL CLI. Generates temporary test CAs/keys, runs a
client, fixed-route gateway and two canaries on 127.0.0.1, then closes them.
No public DNS, external destination, Actor, mTLS CONNECT, MCP, model or actual
credential provider is used. Fixture evidence is complete only for this lab.
Never substitute production keys/certificates or expose these servers.
"""
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
from tempfile import TemporaryDirectory
from threading import Lock, Thread
import uuid


MODELS = "models.example.test"
DENIED = "denied.example.test"
ORIGIN = "origin.example.test"
CANARY = "canary.example.test"
TEST_TOKEN = "fixture-only-not-a-production-secret"


def openssl(*args):
    subprocess.run(["openssl", *map(str, args)], check=True, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=15)


def certificates(folder):
    for name in ("gateway-ca", "origin-ca"):
        openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                "-nodes", "-days", "1", "-subj", f"/CN=Chapter40-{name}",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout", folder/f"{name}.key", "-out", folder/f"{name}.pem")
    for name, ca, hosts in [("gateway", "gateway-ca", [MODELS, DENIED]),
                             ("origin", "origin-ca", [ORIGIN, CANARY])]:
        openssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                "-nodes", "-subj", f"/CN={hosts[0]}", "-addext",
                "subjectAltName="+",".join("DNS:"+h for h in hosts),
                "-keyout", folder/f"{name}.key", "-out", folder/f"{name}.csr")
        openssl("x509", "-req", "-in", folder/f"{name}.csr", "-CA", folder/f"{ca}.pem",
                "-CAkey", folder/f"{ca}.key", "-CAcreateserial", "-days", "1",
                "-copy_extensions", "copy", "-out", folder/f"{name}.pem")


def client_context(cafile):
    context = ssl.create_default_context(cafile=str(cafile))
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    return context


class LoopbackHTTPS(HTTPSConnection):
    """Pinned loopback dial with independent TLS server-name verification.

    This deliberately bypasses DNS; it is not a test of any resolver/relay.
    """
    def connect(self):
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # Never log request headers, tokens or response bodies.

    def reply(self, status, marker, emitter, rid):
        body = json.dumps({"marker": marker}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("X-Lab-Emitter", emitter)
        self.send_header("X-Lab-Request-ID", rid)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        lab = self.server.lab
        rid = self.headers.get("X-Lab-Request-ID", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rid):
            self.reply(400, "bad_request_id", self.server.role, "invalid")
            return
        if self.server.role == "gateway":
            self.gateway(lab, rid)
        else:
            lab.record(rid, self.server.role, "http_received")
            if self.path == "/health":
                self.reply(200, "listener_ok", self.server.role, rid)
            elif self.path == "/v1/probe":
                authorized = self.headers.get("Authorization") == "Bearer "+TEST_TOKEN
                self.reply(200 if authorized else 401,
                           "probe_ok" if authorized else "credential_required", self.server.role, rid)
            else:
                self.reply(404, "path_missing", self.server.role, rid)

    def gateway(self, lab, rid):
        # Exact toy rules, NOT the Substrate policy parser or wildcard matcher.
        sni = getattr(self.connection, "fixture_sni", None)
        host = self.headers.get("Host")
        with lab.lock:
            allowed = lab.allow_hosts
        if host not in allowed or sni not in allowed:
            lab.record(rid, "gateway", "policy_deny")
            self.reply(403, "policy_denied", "gateway", rid)
            return
        lab.record(rid, "gateway", "policy_allow")
        with lab.lock:
            trust = lab.upstream_trust
            token = lab.inject_token
        # Routes are fixed by the lab, never derived from a caller's IP/URL.
        target_name, target_port, target_role = lab.routes[host]
        connection = LoopbackHTTPS(target_name, target_port, timeout=2,
                                  context=client_context(trust))
        headers = {"X-Lab-Request-ID": rid, "Host": target_name}
        if token is not None:
            headers["Authorization"] = "Bearer "+token
        try:
            connection.request("GET", self.path, headers=headers)
            response = connection.getresponse()
            body = json.loads(response.read(4096))
            lab.record(rid, "gateway", "origin_http")
            self.reply(response.status, body["marker"], target_role, rid)
        except ssl.SSLCertVerificationError:
            lab.record(rid, "gateway", "upstream_tls_failed")
            self.reply(502, "upstream_tls_failed", "gateway", rid)
        finally:
            connection.close()


class Lab:
    """Small serial test harness; no production auth or dynamic routing."""
    def __enter__(self):
        self.temp = TemporaryDirectory(prefix="ch40-loopback-")
        self.folder = Path(self.temp.name)
        self.servers, self.threads, self.events = [], [], []
        self.lock = Lock()
        try:
            certificates(self.folder)
            self.gateway_ca = self.folder/"gateway-ca.pem"
            self.origin_ca = self.folder/"origin-ca.pem"
            self.upstream_trust, self.inject_token = self.origin_ca, None
            self.allow_hosts = (MODELS,)
            self.origin_port = self.start("origin", "origin")
            self.canary_port = self.start("canary", "origin")
            self.routes = {MODELS: (ORIGIN, self.origin_port, "origin"),
                           DENIED: (CANARY, self.canary_port, "canary")}
            self.gateway_port = self.start("gateway", "gateway")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def start(self, role, certificate):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.lab, server.role = self, role
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.folder/f"{certificate}.pem"),
                                str(self.folder/f"{certificate}.key"))
        if role == "gateway":
            context.set_servername_callback(lambda sock, name, ctx: setattr(sock, "fixture_sni", name))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        self.servers.append(server)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        self.threads.append(thread)
        return server.server_port

    def __exit__(self, *args):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=3)
        self.temp.cleanup()

    def record(self, rid, emitter, stage):
        with self.lock:
            self.events.append({"request_id": rid, "emitter": emitter, "stage": stage})

    def configure(self, *, inject_token=None, upstream_trust=None, allow_hosts=(MODELS,)):
        with self.lock:
            if any(host not in self.routes for host in allow_hosts):
                raise ValueError("only the two fixed lab routes are available")
            self.allow_hosts = tuple(allow_hosts)
            self.inject_token = inject_token
            self.upstream_trust = upstream_trust or self.origin_ca

    def probe(self, path="/v1/probe", *, authority=MODELS, server_name=None,
              cafile=None, direct_canary=False):
        rid = uuid.uuid4().hex
        name = server_name or (CANARY if direct_canary else authority)
        port = self.canary_port if direct_canary else self.gateway_port
        trust = cafile or (self.origin_ca if direct_canary else self.gateway_ca)
        connection = LoopbackHTTPS(name, port, timeout=2, context=client_context(trust))
        try:
            connection.request("GET", path, headers={"Host": authority, "X-Lab-Request-ID": rid})
            response = connection.getresponse()
            result = {"status": response.status, "emitter": response.getheader("X-Lab-Emitter"),
                      "marker": json.loads(response.read(4096))["marker"], "client_tls": "VERIFIED"}
            assert response.getheader("X-Lab-Request-ID") == rid
        except ssl.SSLCertVerificationError as exc:
            result = {"status": None, "emitter": "client", "marker": "client_tls_failed",
                      "client_tls": "REJECTED", "verify_code": exc.verify_code}
        finally:
            connection.close()
        with self.lock:
            events = [dict(e) for e in self.events if e["request_id"] == rid]
        # Headers from an arbitrary peer would not be authoritative evidence.
        # These emitters are checked against our controlled server-side journal.
        result["events"] = [{k: e[k] for k in ("emitter", "stage")} for e in events]
        result["origin_received"] = any(e["emitter"] == "origin" for e in events)
        result["canary_received"] = any(e["emitter"] == "canary" for e in events)
        result["automatic_policy_changes"] = 0
        return result


def demo():
    with Lab() as lab:
        health = lab.probe("/health")
        missing_credential = lab.probe()
        lab.configure(inject_token=TEST_TOKEN)
        allowed = lab.probe()
        live_canary = lab.probe("/health", direct_canary=True)
        denied = lab.probe("/health", authority=DENIED)
        wrong_client_ca = lab.probe(cafile=lab.origin_ca)
        lab.configure(inject_token=TEST_TOKEN, upstream_trust=lab.gateway_ca)
        wrong_upstream_ca = lab.probe()
        cases = dict(health=health, missing_credential=missing_credential, allowed=allowed,
                     live_canary=live_canary, denied=denied, wrong_client_ca=wrong_client_ca,
                     wrong_upstream_ca=wrong_upstream_ca)
        return {"network_scope": "LOOPBACK_ONLY", "cases": cases,
                "substrate_executed": False, "external_requests": 0}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
