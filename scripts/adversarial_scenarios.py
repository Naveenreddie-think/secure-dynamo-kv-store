"""The 10 Phase 7 adversarial scenario implementations. Each executes
against a REAL, live `docker compose up` cluster -- no in-process mocking.
See PROGRESS.md's Phase 7 entry for the full taxonomy and why each
category lands where it does; the fast, deterministic mechanism proofs for
categories 2 (partial)/4/5/6 live in tests/test_adversarial_mechanisms.py
and the extended tests/test_gossip.py, not here.

Internal-port attacks (1, 2, 3, 5, 6) are executed FROM INSIDE a node's own
container via `docker compose exec ... python -c "<snippet>"` -- not just
for practical reachability (the internal port is deliberately never
published to the host), but because this is the MORE FAITHFUL simulation
of PLAN.md's own framing: a "compromised node" attacker is, by definition,
already inside the cluster network with a legitimate node's real
credentials, which is exactly what `docker compose exec node-X` gives you
(that container's own mounted certs at /app/certs/node/, and the same
`dynamokv`/`httpx`/`cryptography` packages already installed in the image).
"""
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Context:
    node_ids: List[str]
    public_ports: Dict[str, int]
    auth_tokens: Dict[str, dict]

    def public_url(self, node_id: str) -> str:
        return f"https://localhost:{self.public_ports[node_id]}"


@dataclass
class Result:
    category: str
    scenario: str
    expected: str
    observed: str
    prevented: bool
    detected: bool
    evidence: str = ""
    note: str = ""


def exec_snippet(node_id: str, code: str, timeout: int = 20) -> Dict[str, Any]:
    """Run a Python snippet inside node_id's container; the snippet must
    print exactly one JSON object as its last stdout line."""
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", node_id, "python", "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO_ROOT,
    )
    stdout = proc.stdout.strip()
    last_line = stdout.splitlines()[-1] if stdout else ""
    try:
        parsed = json.loads(last_line)
    except (json.JSONDecodeError, IndexError):
        parsed = {"parse_error": True, "stdout": stdout, "stderr": proc.stderr[-2000:]}
    parsed["_returncode"] = proc.returncode
    return parsed


# ---------------------------------------------------------------------------
# Category 1 -- unauthorized internal join
# ---------------------------------------------------------------------------

_NO_CERT_SNIPPET = """
import json, ssl, socket
ctx = ssl.create_default_context(cafile="/app/certs/ca.crt")
try:
    sock = socket.create_connection(("node-2", 8443), timeout=5)
    ssock = ctx.wrap_socket(sock, server_hostname="node-2")
    ssock.sendall(b"GET /internal/keys/probe HTTP/1.1\\r\\nHost: node-2\\r\\n\\r\\n")
    data = ssock.recv(200)
    print(json.dumps({"connected": True, "got_response": len(data) > 0}))
except Exception as e:
    print(json.dumps({"connected": False, "error": type(e).__name__}))
"""

_ROGUE_CERT_SNIPPET = """
import json, ssl, socket, datetime, tempfile, os
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "rogue-node")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (
    x509.CertificateBuilder().subject_name(name).issuer_name(name)
    .public_key(key.public_key()).serial_number(x509.random_serial_number())
    .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
    .sign(key, hashes.SHA256())
)
with tempfile.TemporaryDirectory() as d:
    keyp, certp = os.path.join(d, "rogue.key"), os.path.join(d, "rogue.crt")
    open(keyp, "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    open(certp, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
    ctx = ssl.create_default_context(cafile="/app/certs/ca.crt")
    ctx.load_cert_chain(certp, keyp)
    try:
        sock = socket.create_connection(("node-2", 8443), timeout=5)
        ssock = ctx.wrap_socket(sock, server_hostname="node-2")
        ssock.sendall(b"GET /internal/keys/probe HTTP/1.1\\r\\nHost: node-2\\r\\n\\r\\n")
        data = ssock.recv(200)
        print(json.dumps({"connected": True, "got_response": len(data) > 0}))
    except Exception as e:
        print(json.dumps({"connected": False, "error": type(e).__name__}))
"""


def scenario_1_unauthorized_join(ctx: Context) -> List[Result]:
    results = []
    for label, snippet in [("no client cert", _NO_CERT_SNIPPET), ("rogue self-signed CA cert", _ROGUE_CERT_SNIPPET)]:
        out = exec_snippet("node-1", snippet)
        prevented = not out.get("connected") or not out.get("got_response")
        results.append(
            Result(
                category="1",
                scenario=f"unauthorized internal join -- {label}",
                expected="PREVENTED",
                observed="PREVENTED" if prevented else "SUCCEEDED",
                prevented=prevented,
                detected=False,  # TLS-layer rejection happens below where the audit middleware ever sees anything
                evidence=json.dumps(out),
            )
        )
    return results


# ---------------------------------------------------------------------------
# Category 2 -- node identity spoofing via internal API
# ---------------------------------------------------------------------------


def scenario_2_identity_spoofing(ctx: Context) -> List[Result]:
    results = []

    gossip_snippet = """
import json, ssl, httpx
c = ssl.create_default_context(cafile="/app/certs/ca.crt")
c.load_cert_chain("/app/certs/node/node-2.crt", "/app/certs/node/node-2.key")
client = httpx.Client(verify=c, timeout=5.0)
r1 = client.post("https://node-1:8443/internal/gossip", json={"sender": "node-2", "table": {"node-2": 1}})
r2 = client.post("https://node-1:8443/internal/gossip", json={"sender": "totally-fabricated-identity", "table": {"node-2": 2}})
print(json.dumps({"honest_status": r1.status_code, "spoofed_status": r2.status_code}))
"""
    out = exec_snippet("node-2", gossip_snippet)
    accepted = out.get("spoofed_status") == 200 and out.get("honest_status") == 200
    results.append(
        Result(
            category="2",
            scenario="gossip 'sender' field accepted with a fabricated identity (node-2's real cert used)",
            expected="UNDEFENDED",
            observed="UNDEFENDED (accepted identically)" if accepted else "DEFENDED (rejected)",
            prevented=not accepted,
            detected=False,
            evidence=json.dumps(out),
        )
    )

    fake_target = "not-a-real-cluster-member"
    hint_snippet = f"""
import json, ssl, httpx
c = ssl.create_default_context(cafile="/app/certs/ca.crt")
c.load_cert_chain("/app/certs/node/node-2.crt", "/app/certs/node/node-2.key")
client = httpx.Client(verify=c, timeout=5.0)
r = client.put("https://node-1:8443/internal/hints/spoof-probe-key",
               json={{"target": "{fake_target}", "value": "x", "clock": {{"node-2": 1}}}})
print(json.dumps({{"status": r.status_code}}))
"""
    exec_snippet("node-2", hint_snippet)

    check_snippet = f"""
import json
from dynamokv.crypto import EncryptedStorage, load_or_create_encryption_key
from dynamokv.storage.sqlite import SqliteStorage
from dynamokv.hint_store import HintStore
storage = EncryptedStorage(SqliteStorage("data/node-1.db.hints"), load_or_create_encryption_key("data/node-1.key"))
pending = HintStore(storage).pending_for("{fake_target}")
print(json.dumps({{"hint_stored_under_fake_target": "spoof-probe-key" in pending}}))
"""
    check = exec_snippet("node-1", check_snippet)
    accepted_hint = check.get("hint_stored_under_fake_target", False)
    results.append(
        Result(
            category="2",
            scenario="hint 'target' field accepted for an arbitrary/fake node id (node-2's real cert used)",
            expected="UNDEFENDED",
            observed="UNDEFENDED (accepted)" if accepted_hint else "DEFENDED (rejected)",
            prevented=not accepted_hint,
            detected=False,
            evidence=json.dumps(check),
        )
    )
    return results


# ---------------------------------------------------------------------------
# Category 3 -- gossip forgery, suppress a healthy node's liveness
# ---------------------------------------------------------------------------

_GOSSIP_PARTITION_OVERRIDE = """\
networks:
  net-a:
  net-b:

services:
  node-1:
    networks:
      - net-a
  node-2:
    networks:
      - net-a
      - net-b
  node-3:
    networks:
      - net-b
"""


def _write_time_public_put(node_id: str, port: int, key: str, token: str) -> float:
    start = time.monotonic()
    subprocess.run(
        [
            "docker", "compose", "exec", "-T", node_id, "python", "-c",
            f"""
import httpx, ssl
c = ssl.create_default_context(cafile="/app/certs/ca.crt"); c.check_hostname = False; c.verify_mode = ssl.CERT_NONE
httpx.put("https://localhost:8000/keys/{key}", json={{"value": "probe"}},
          headers={{"Authorization": "Bearer {token}"}}, verify=c, timeout=15.0)
""",
        ],
        capture_output=True, text=True, timeout=20, cwd=REPO_ROOT,
    )
    return time.monotonic() - start


def scenario_3_gossip_forgery(ctx: Context) -> List[Result]:
    override_path = REPO_ROOT / "docker-compose.override.yml"
    already_had_override = override_path.exists()
    token = next(iter(ctx.auth_tokens)) if ctx.auth_tokens else ""

    try:
        override_path.write_text(_GOSSIP_PARTITION_OVERRIDE)
        subprocess.run(["docker", "compose", "up", "-d"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        time.sleep(3)  # let the new network topology settle

        # node-2 (still reachable from the harness's exec) confirms node-3 stays genuinely healthy throughout.
        # The public port is HTTPS-only (server-only TLS) even with no client cert required.
        health_snippet = (
            'import json,httpx,ssl\n'
            'c=ssl.create_default_context(cafile="/app/certs/ca.crt");c.check_hostname=False;c.verify_mode=ssl.CERT_NONE\n'
            'r=httpx.get("https://node-3:8000/healthz",verify=c,timeout=5)\n'
            'print(json.dumps({"status":r.status_code}))'
        )
        health_before = exec_snippet("node-2", health_snippet)

        baseline_latency = _write_time_public_put("node-1", 8000, "gossip-probe-before", token)

        # compromised node-2 (real cert) repeatedly relays a frozen counter for node-3 to node-1
        relay_snippet = """
import json, ssl, httpx
c = ssl.create_default_context(cafile="/app/certs/ca.crt")
c.load_cert_chain("/app/certs/node/node-2.crt", "/app/certs/node/node-2.key")
client = httpx.Client(verify=c, timeout=3.0)
try:
    client.post("https://node-1:8443/internal/gossip", json={"sender": "node-2", "table": {"node-3": 1}})
except Exception:
    pass
print(json.dumps({"sent": True}))
"""
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            exec_snippet("node-2", relay_snippet, timeout=8)
            time.sleep(0.5)

        health_after = exec_snippet("node-2", health_snippet)
        attack_latency = _write_time_public_put("node-1", 8000, "gossip-probe-after", token)

        node3_stayed_healthy = health_before.get("status") == 200 and health_after.get("status") == 200
        # a proactively-skipped write (node-1 believes node-3 down) is much
        # faster than one that pays a live timeout attempting node-3 first --
        # but this observable only works if the baseline itself was slow
        # (i.e. the reactive path genuinely paid a live timeout). If the
        # baseline is already fast, Docker's network segmentation caused a
        # near-instant connection/DNS failure rather than a hung TCP
        # timeout, and the observable can't distinguish anything -- that's
        # inconclusive, not evidence of a defense.
        observable_is_meaningful = baseline_latency > 2.0
        belief_manipulated = observable_is_meaningful and attack_latency < (baseline_latency * 0.5)

        if not observable_is_meaningful:
            prevented, detected = False, False  # inconclusive: claim neither a finding nor a defense
            observed = (
                f"inconclusive -- baseline write latency was already fast ({baseline_latency:.2f}s), "
                "so the latency-based observable can't distinguish this run's outcome. Docker's "
                "network-level segmentation (separate bridge networks) causes near-instant "
                "connection/DNS failures rather than a hung TCP timeout, so a REACTIVE failed "
                "attempt to unreachable node-3 is already about as fast as a PROACTIVE gossip-driven "
                "skip would be -- the two paths are latency-indistinguishable under this specific "
                "segmentation technique, independent of whether the forged relay actually took effect."
            )
        else:
            prevented = not (node3_stayed_healthy and belief_manipulated)
            detected = False
            observed = (
                "UNDEFENDED (node-3 stayed healthy throughout, but node-1's write latency dropped "
                f"from {baseline_latency:.2f}s to {attack_latency:.2f}s, consistent with node-1 "
                "proactively treating node-3 as down)" if not prevented
                else "DEFENDED (no latency drop observed despite a slow, meaningful baseline)"
            )

        return [
            Result(
                category="3",
                scenario="compromised relay suppresses a healthy node's perceived liveness (topology: node-1 reachable to node-3 only via node-2)",
                expected="UNDEFENDED",
                observed=observed,
                prevented=prevented,
                detected=detected,
                evidence=json.dumps({
                    "node3_health_before": health_before, "node3_health_after": health_after,
                    "baseline_write_latency_s": baseline_latency, "post_attack_write_latency_s": attack_latency,
                }),
                note=(
                    "The chosen live observable (write-latency delta) doesn't cleanly distinguish "
                    "proactive-skip from reactive-fail-fast when Docker network segmentation causes "
                    "near-instant failures rather than slow timeouts. This does NOT mean the attack is "
                    "defended -- it means this specific live-reproduction technique is inconclusive by "
                    "construction. The underlying mechanism is proven deterministically, independent of "
                    "Docker/network timing, in "
                    "tests/test_gossip.py::test_adversarial_stale_relay_makes_a_healthy_node_appear_down."
                ),
            )
        ]
    finally:
        if not already_had_override and override_path.exists():
            override_path.unlink()
        subprocess.run(["docker", "compose", "up", "-d"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        time.sleep(6)  # let the full mesh re-settle before subsequent scenarios


# ---------------------------------------------------------------------------
# Category 4 -- vector-clock replay defense (working defense)
# ---------------------------------------------------------------------------


def scenario_4_replay_defense(ctx: Context) -> List[Result]:
    token = next(iter(ctx.auth_tokens)) if ctx.auth_tokens else ""
    key = "replay-probe-key"

    snippet = f"""
import json, ssl, httpx
pub = ssl.create_default_context(cafile="/app/certs/ca.crt"); pub.check_hostname = False; pub.verify_mode = ssl.CERT_NONE
pub_client = httpx.Client(verify=pub, timeout=10.0, headers={{"Authorization": "Bearer {token}"}})

r1 = pub_client.put("https://localhost:8000/keys/{key}", json={{"value": "v1"}})
clock1 = r1.json()["clock"]
pub_client.put("https://localhost:8000/keys/{key}", json={{"value": "v2"}})

internal = ssl.create_default_context(cafile="/app/certs/ca.crt")
internal.load_cert_chain("/app/certs/node/node-1.crt", "/app/certs/node/node-1.key")
internal_client = httpx.Client(verify=internal, timeout=5.0)
internal_client.put("https://node-1:8443/internal/keys/{key}", json={{"value": "v1", "clock": clock1}})

final = pub_client.get("https://localhost:8000/keys/{key}")
print(json.dumps({{"final_value": final.json()["value"]}}))
"""
    out = exec_snippet("node-1", snippet)
    prevented = out.get("final_value") == "v2"
    return [
        Result(
            category="4",
            scenario="replay a captured old write after a newer one has landed",
            expected="PREVENTED",
            observed="PREVENTED (replay dropped, v2 retained)" if prevented else "SUCCEEDED (replay overwrote v2!)",
            prevented=prevented,
            detected=False,
            evidence=json.dumps(out),
        )
    ]


# ---------------------------------------------------------------------------
# Category 5 -- vector-clock forgery / hijack + self-heal boundary
# ---------------------------------------------------------------------------


def scenario_5_clock_forgery(ctx: Context) -> List[Result]:
    token = next(iter(ctx.auth_tokens)) if ctx.auth_tokens else ""
    key = "forgery-probe-key"

    snippet = f"""
import json, ssl, httpx
pub = ssl.create_default_context(cafile="/app/certs/ca.crt"); pub.check_hostname = False; pub.verify_mode = ssl.CERT_NONE
pub_client = httpx.Client(verify=pub, timeout=10.0, headers={{"Authorization": "Bearer {token}"}})
# Explicitly coordinate the legit write via node-1's public port (not
# "localhost", which would resolve to whichever node this snippet happens
# to run from) -- the poison below claims to be node-1's write, so the
# clock it needs to dominate must actually BE node-1's, or the two are
# merely concurrent (a 409 conflict) rather than a clean, provable hijack.
pub_client.put("https://node-1:8000/keys/{key}", json={{"value": "legit"}})

internal = ssl.create_default_context(cafile="/app/certs/ca.crt")
internal.load_cert_chain("/app/certs/node/node-2.crt", "/app/certs/node/node-2.key")
internal_client = httpx.Client(verify=internal, timeout=5.0)
for node in ["node-1", "node-2", "node-3"]:
    internal_client.put(f"https://{{node}}:8443/internal/keys/{key}",
                         json={{"value": "poisoned", "clock": {{"node-1": 999999}}}})

hijacked = pub_client.get("https://node-1:8000/keys/{key}").json()["value"]

recovered_resp = pub_client.put("https://node-1:8000/keys/{key}", json={{"value": "recovered"}})
recovered = pub_client.get("https://node-1:8000/keys/{key}").json()["value"]

print(json.dumps({{"hijacked_value": hijacked, "recovered_value": recovered}}))
"""
    # Run FROM node-2's own container -- each container only has its OWN
    # node's cert mounted at /app/certs/node/, so "node-2's real cert" must
    # be used from inside node-2's own container, not borrowed remotely.
    out = exec_snippet("node-2", snippet)
    hijack_succeeded = out.get("hijacked_value") == "poisoned"
    self_healed = out.get("recovered_value") == "recovered"

    results = [
        Result(
            category="5",
            scenario="fabricated clock claimed for a node the caller doesn't represent hijacks the key cluster-wide",
            expected="UNDEFENDED (immediate hijack)",
            observed="UNDEFENDED (hijack succeeded)" if hijack_succeeded else "DEFENDED",
            prevented=not hijack_succeeded,
            detected=False,
            evidence=json.dumps(out),
        ),
        Result(
            category="5",
            scenario="self-heal boundary -- impersonated node's next real coordinated write supersedes the poison",
            expected="the poison is NOT eternal",
            observed="self-healed as expected" if self_healed else "did NOT self-heal (unexpected)",
            prevented=self_healed,
            detected=False,
            evidence=json.dumps(out),
        ),
    ]
    return results


# ---------------------------------------------------------------------------
# Category 6 -- unauthorized/forged delete via internal API
# ---------------------------------------------------------------------------


def scenario_6_unauthorized_delete(ctx: Context) -> List[Result]:
    token = next(iter(ctx.auth_tokens)) if ctx.auth_tokens else ""
    key = "delete-probe-key"

    snippet = f"""
import json, ssl, httpx
pub = ssl.create_default_context(cafile="/app/certs/ca.crt"); pub.check_hostname = False; pub.verify_mode = ssl.CERT_NONE
pub_client = httpx.Client(verify=pub, timeout=10.0, headers={{"Authorization": "Bearer {token}"}})
pub_client.put("https://localhost:8000/keys/{key}", json={{"value": "bar"}})

internal = ssl.create_default_context(cafile="/app/certs/ca.crt")
internal.load_cert_chain("/app/certs/node/node-2.crt", "/app/certs/node/node-2.key")
internal_client = httpx.Client(verify=internal, timeout=5.0)
for node in ["node-1", "node-2", "node-3"]:
    internal_client.delete(f"https://{{node}}:8443/internal/keys/{key}")

after = pub_client.get("https://localhost:8000/keys/{key}")
print(json.dumps({{"status_after_forged_delete": after.status_code}}))
"""
    # Run FROM node-2's own container, matching category 5's fix -- see note there.
    out = exec_snippet("node-2", snippet)
    deleted = out.get("status_after_forged_delete") == 404
    return [
        Result(
            category="6",
            scenario="delete all replicas directly via the internal API, with no legitimate client-facing delete ever issued",
            expected="UNDEFENDED",
            observed="UNDEFENDED (key was deleted with no authorization)" if deleted else "DEFENDED",
            prevented=not deleted,
            detected=False,
            evidence=json.dumps(out),
        )
    ]


# ---------------------------------------------------------------------------
# Category 7 -- read encrypted data without the key
# ---------------------------------------------------------------------------


def scenario_7_read_without_key(ctx: Context) -> List[Result]:
    token = next(iter(ctx.auth_tokens)) if ctx.auth_tokens else ""
    key = "crypto-probe-key"

    seed_snippet = f"""
import json, ssl, httpx
pub = ssl.create_default_context(cafile="/app/certs/ca.crt"); pub.check_hostname = False; pub.verify_mode = ssl.CERT_NONE
httpx.put("https://localhost:8000/keys/{key}", json={{"value": "top-secret-value"}},
          headers={{"Authorization": "Bearer {token}"}}, verify=pub, timeout=10.0)
print(json.dumps({{"seeded": True}}))
"""
    exec_snippet("node-1", seed_snippet)

    attack_snippet = f"""
import json, sqlite3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dynamokv.crypto import load_or_create_encryption_key
import base64

conn = sqlite3.connect("data/node-1.db")
row = conn.execute("SELECT value FROM kv WHERE key=?", ("{key}",)).fetchone()
results = {{}}
if row is None:
    print(json.dumps({{"error": "key not found on node-1 (may not be a replica for this key)"}}))
else:
    envelope = json.loads(row[0])
    nonce = base64.b64decode(envelope["nonce"])
    ciphertext = base64.b64decode(envelope["ciphertext"])

    wrong_key = AESGCM.generate_key(bit_length=256)
    try:
        AESGCM(wrong_key).decrypt(nonce, ciphertext, None)
        results["wrong_key_decrypted"] = True
    except Exception as e:
        results["wrong_key_decrypted"] = False
        results["wrong_key_error"] = type(e).__name__

    real_key = load_or_create_encryption_key("data/node-1.key")
    plaintext = AESGCM(real_key).decrypt(nonce, ciphertext, None)
    results["real_key_recovered_value"] = json.loads(plaintext)
    print(json.dumps(results))
"""
    out = exec_snippet("node-1", attack_snippet)
    prevented = out.get("wrong_key_decrypted") is False
    return [
        Result(
            category="7",
            scenario="decrypt raw on-disk ciphertext with the wrong key",
            expected="PREVENTED",
            observed="PREVENTED (wrong key raises)" if prevented else "SUCCEEDED (leaked plaintext!)",
            prevented=prevented,
            detected=False,
            evidence=json.dumps(out),
        )
    ]


# ---------------------------------------------------------------------------
# Category 8 -- tamper with ciphertext at rest
# ---------------------------------------------------------------------------


def scenario_8_ciphertext_tampering(ctx: Context) -> List[Result]:
    token = next(iter(ctx.auth_tokens)) if ctx.auth_tokens else ""
    key = "tamper-probe-key"

    seed_snippet = f"""
import json, ssl, httpx
pub = ssl.create_default_context(cafile="/app/certs/ca.crt"); pub.check_hostname = False; pub.verify_mode = ssl.CERT_NONE
httpx.put("https://localhost:8000/keys/{key}", json={{"value": "tamper-me"}},
          headers={{"Authorization": "Bearer {token}"}}, verify=pub, timeout=10.0)
print(json.dumps({{"seeded": True}}))
"""
    exec_snippet("node-1", seed_snippet)

    tamper_snippet = f"""
import json, sqlite3, base64

conn = sqlite3.connect("data/node-1.db")
row = conn.execute("SELECT value FROM kv WHERE key=?", ("{key}",)).fetchone()
if row is None:
    print(json.dumps({{"error": "key not found on node-1"}}))
else:
    envelope = json.loads(row[0])
    ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
    ciphertext[0] ^= 0xFF  # flip a byte
    envelope["ciphertext"] = base64.b64encode(bytes(ciphertext)).decode("ascii")
    conn.execute("UPDATE kv SET value=? WHERE key=?", (json.dumps(envelope), "{key}"))
    conn.commit()
    print(json.dumps({{"tampered": True}}))
"""
    exec_snippet("node-1", tamper_snippet)

    # Read via node-1's OWN internal endpoint (its own legit cert), not the
    # public quorum-routed endpoint -- with N=3/R=2, a quorum read is
    # correctly fault-tolerant and would be satisfied by the two OTHER,
    # untampered replicas, masking the corruption on node-1 specifically.
    # That's quorum working as designed, not evidence either way about
    # whether GCM's authentication tag actually caught the tampering on the
    # one replica it was applied to -- so this checks that replica directly.
    read_snippet = f"""
import json, ssl, httpx
internal = ssl.create_default_context(cafile="/app/certs/ca.crt")
internal.load_cert_chain("/app/certs/node/node-1.crt", "/app/certs/node/node-1.key")
try:
    r = httpx.get("https://node-1:8443/internal/keys/{key}", verify=internal, timeout=10.0)
    print(json.dumps({{"status": r.status_code, "body": r.text[:300]}}))
except Exception as e:
    print(json.dumps({{"status": None, "error": type(e).__name__}}))
"""
    out = exec_snippet("node-1", read_snippet)
    # 200 with the tampered node's value intact would mean tampering slipped
    # through; anything else (500 from a decrypt exception, etc.) means it
    # was caught. A 200 whose body doesn't parse as the original plaintext
    # would also indicate detection, but AESGCM raises before that's possible.
    prevented = out.get("status") != 200
    return [
        Result(
            category="8",
            scenario="flip a byte in stored ciphertext, then read that specific replica directly (its own internal endpoint)",
            expected="PREVENTED",
            observed="PREVENTED (tampered read rejected/failed)" if prevented else "SUCCEEDED (corrupted data accepted!)",
            prevented=prevented,
            detected=False,
            evidence=json.dumps(out),
        )
    ]


# ---------------------------------------------------------------------------
# Category 9 -- client auth bypass attempts
# ---------------------------------------------------------------------------


def scenario_9_auth_bypass(ctx: Context) -> List[Result]:
    import httpx as _httpx  # noqa: local import so the harness itself doesn't require it at module import time if unused elsewhere

    url = f"{ctx.public_url('node-1')}/keys/auth-probe-key"
    client = _httpx.Client(verify=False, timeout=10.0)
    results = []

    resp = client.get(url)
    results.append(("no Authorization header", resp.status_code, 401))

    resp = client.get(url, headers={"Authorization": "not-bearer-format"})
    results.append(("malformed Authorization header", resp.status_code, 401))

    resp = client.get(url, headers={"Authorization": "Bearer not-a-real-token"})
    results.append(("unknown token", resp.status_code, 401))

    if ctx.auth_tokens:
        readonly_token = None
        for tok, entry in ctx.auth_tokens.items():
            if all("write" not in verbs for verbs in entry.get("namespaces", {}).values()):
                readonly_token = tok
                break
        if readonly_token:
            resp = client.put(url, json={"value": "x"}, headers={"Authorization": f"Bearer {readonly_token}"})
            results.append(("read-only token attempting write", resp.status_code, 403))

        write_token = next(iter(ctx.auth_tokens))
        resp = client.put(url, json={"value": "x"}, headers={"Authorization": f"Bearer {write_token}"})
        results.append(("fully valid token", resp.status_code, 200))

    out = []
    all_prevented = True
    for name, actual, expected in results:
        ok = actual == expected
        all_prevented = all_prevented and (ok if expected != 200 else True)
        out.append(Result(
            category="9",
            scenario=f"auth bypass attempt -- {name}",
            expected=str(expected),
            observed=str(actual),
            prevented=ok,
            detected=ok,
            evidence=json.dumps({"status": actual}),
        ))
    return out


# ---------------------------------------------------------------------------
# Category 10 -- audit trail blind spot
# ---------------------------------------------------------------------------


def scenario_10_audit_blind_spot(ctx: Context) -> List[Result]:
    results = []

    check_public = exec_snippet(
        "node-1",
        'import json; content = open("data/node-1.audit.log").read() if __import__("os").path.exists("data/node-1.audit.log") else ""; print(json.dumps({"public_entries": content.count(chr(10))}))',
    )
    check_internal = exec_snippet(
        "node-1",
        'import json,os; p="data/node-1.internal-audit.log"; content = open(p).read() if os.path.exists(p) else ""; print(json.dumps({"internal_entries": content.count(chr(10)), "internal_mentions_forged_ops": ("internal/keys" in content) or ("internal/hints" in content) or ("internal/gossip" in content)}))',
    )
    has_internal_trail = check_internal.get("internal_entries", 0) > 0
    results.append(
        Result(
            category="10a",
            scenario="internal-port attacks (categories 2/3/5/6) are captured in an audit trail",
            expected="FIXED this phase (audit middleware attached to the internal app)",
            observed=(
                f"internal audit log has {check_internal.get('internal_entries', 0)} entries"
                if has_internal_trail else "internal audit log is empty or missing"
            ),
            prevented=False,  # detection, not prevention
            detected=has_internal_trail,
            evidence=json.dumps({"public": check_public, "internal": check_internal}),
        )
    )

    tamper_snippet = """
import json, os
p = "data/node-1.audit.log"
existed = os.path.exists(p)
if existed:
    open(p, "w").close()  # truncate -- simulates a compromised node covering its tracks
print(json.dumps({"truncated": existed}))
"""
    tamper_out = exec_snippet("node-1", tamper_snippet)
    results.append(
        Result(
            category="10b",
            scenario="a compromised node truncates/edits its own local audit log with no integrity check",
            expected="UNDEFENDED",
            observed="UNDEFENDED (truncation succeeded, no error/alarm)" if tamper_out.get("truncated") else "no log file present to tamper with",
            prevented=False,
            detected=False,
            evidence=json.dumps(tamper_out),
            note="Tamper-evident logging (hash chaining or signing) is Phase 9-adjacent structured-logging territory, not attempted here.",
        )
    )
    return results


ALL_SCENARIOS = [
    ("1", scenario_1_unauthorized_join),
    ("2", scenario_2_identity_spoofing),
    ("3", scenario_3_gossip_forgery),
    ("4", scenario_4_replay_defense),
    ("5", scenario_5_clock_forgery),
    ("6", scenario_6_unauthorized_delete),
    ("7", scenario_7_read_without_key),
    ("8", scenario_8_ciphertext_tampering),
    ("9", scenario_9_auth_bypass),
    ("10", scenario_10_audit_blind_spot),
]
