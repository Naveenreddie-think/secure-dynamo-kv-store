"""Shared TLS context builders for mTLS client connections.

Extracted from run.py's original _build_mtls_context() rather than
reimplemented, because that function already paid the cost of discovering a
real httpx bug: httpx.Client(cert=(...), verify="<string path>") silently
DROPS the client certificate. httpx.create_ssl_context()'s string-verify
branch returns immediately with ssl.create_default_context(cafile=...),
before ever reaching the code that would call ctx.load_cert_chain() for
`cert`. Both `cert=` and string `verify=` are marked deprecated in httpx in
favor of exactly what's done here: build the SSLContext yourself and pass
it as `verify=<SSLContext>`.
"""
import ssl


def build_mtls_client_context(cert_path: str, key_path: str, ca_path: str) -> ssl.SSLContext:
    """A context that presents a client certificate AND verifies the
    server's certificate against ca_path -- what every legitimate node uses
    to talk to another node's internal (mTLS-required) port."""
    ctx = ssl.create_default_context(cafile=ca_path)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


def build_no_client_cert_context(ca_path: str) -> ssl.SSLContext:
    """Verifies the server's certificate but presents no client
    certificate at all -- what an attacker with no cluster credential
    would use to probe the internal port."""
    return ssl.create_default_context(cafile=ca_path)
