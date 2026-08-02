"""Generates a self-signed CA and per-node certificates for mTLS between
nodes. Run once before `docker compose up`:

    python scripts/gen_certs.py

Uses the cryptography library directly rather than the openssl CLI, since
cryptography is already a project dependency (for AES-256 at rest) and this
avoids requiring openssl on the host at all. A self-signed CA is fine for a
student project -- no rotation story is attempted here.

Output layout (all under certs/, which is gitignored -- private keys must
never be committed):

    certs/ca.crt              mounted read-only into every container
    certs/ca.key               host-only, NEVER mounted, NEVER committed
    certs/node-1/node-1.crt
    certs/node-1/node-1.key    mounted only into node-1's own container
    certs/node-2/...
    certs/node-3/...

Safe to re-run: does nothing if certs/ca.crt already exists.
"""
import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

NODE_IDS = ["node-1", "node-2", "node-3"]  # matches docker-compose.yml's services
CERTS_DIR = Path(__file__).resolve().parent.parent / "certs"
VALIDITY = datetime.timedelta(days=3650)


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _make_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _generate_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dynamokv-cluster-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _make_node_cert(node_id: str, ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _generate_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + VALIDITY)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(node_id), x509.DNSName("localhost")]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def main() -> None:
    if (CERTS_DIR / "ca.crt").exists():
        print(f"{CERTS_DIR}/ca.crt already exists -- skipping (delete certs/ to regenerate).")
        return

    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = _make_ca()
    _write_key(CERTS_DIR / "ca.key", ca_key)
    _write_cert(CERTS_DIR / "ca.crt", ca_cert)
    print(f"Generated CA: {CERTS_DIR}/ca.crt")

    for node_id in NODE_IDS:
        node_dir = CERTS_DIR / node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        node_key, node_cert = _make_node_cert(node_id, ca_key, ca_cert)
        _write_key(node_dir / f"{node_id}.key", node_key)
        _write_cert(node_dir / f"{node_id}.crt", node_cert)
        print(f"Generated node cert: {node_dir}/{node_id}.crt")


if __name__ == "__main__":
    main()
