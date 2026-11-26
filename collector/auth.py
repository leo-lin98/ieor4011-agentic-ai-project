"""API credentials and request signing for both platforms (read from .env; never logged).

Kalshi (docs.kalshi.com/getting_started/api_keys): key ID + Ed25519 (default) or RSA-2048 private key.
  Headers KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP (ms), KALSHI-ACCESS-SIGNATURE = base64 signature over
  f"{timestamp}{METHOD}{path}" where path has no query string and includes /trade-api/v2.
  Ed25519 signs the message directly; RSA uses PSS / SHA-256 / MGF1-SHA-256 / salt = digest length.
Polymarket US (docs.polymarket.us/api-reference/authentication): key ID + base64 secret whose first 32 bytes
  are an Ed25519 seed. Headers X-PM-Access-Key, X-PM-Timestamp (ms), X-PM-Signature over the same message.

Market-data REST endpoints are public on both platforms. Signing is needed for the WebSocket handshakes, for
trading, and (Kalshi) to use the account's Read budget instead of the stricter unauthenticated limit.

.env variables:  KALSHI_KEY_ID, KALSHI_API (private key: PEM, or the base64 DER body of one)
                 POLYMARKET_API (key ID), SECRET_POLY (secret)
"""
import base64
import re
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from .config import ROOT

_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env(path=ROOT / ".env"):
    """Minimal .env reader: KEY=value, KEY = 'value', KEY="value"; # comments; no interpolation."""
    out = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if not m:
            continue
        k, v = m.groups()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k] = v
    return out


def _now_ms():
    return str(int(time.time() * 1000))


# ---------------------------------------------------------------- Kalshi

def _kalshi_private_key(raw):
    raw = raw.strip()
    if "PRIVATE KEY" in raw:
        return serialization.load_pem_private_key(raw.encode(), password=None)
    return serialization.load_der_private_key(base64.b64decode(raw), password=None)  # PEM body only


class KalshiSigner:
    def __init__(self, key_id, private_key):
        if not key_id or not private_key:
            raise ValueError("KALSHI_KEY_ID and KALSHI_API (private key) are both required")
        self.key_id = key_id
        self.key = _kalshi_private_key(private_key)
        self.kind = "ed25519" if isinstance(self.key, ed25519.Ed25519PrivateKey) else (
            "rsa" if isinstance(self.key, rsa.RSAPrivateKey) else type(self.key).__name__)

    def headers(self, method, path):
        """path: full URL path without query, e.g. /trade-api/v2/portfolio/balance."""
        ts = _now_ms()
        msg = f"{ts}{method.upper()}{path.split('?', 1)[0]}".encode()
        if self.kind == "ed25519":
            sig = self.key.sign(msg)
        elif self.kind == "rsa":
            sig = self.key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
                                hashes.SHA256())
        else:
            raise ValueError(f"unsupported Kalshi key type {self.kind}")
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}


# ---------------------------------------------------------------- Polymarket US

class PolyusSigner:
    def __init__(self, key_id, secret):
        if not key_id or not secret:
            raise ValueError("POLYMARKET_API (key ID) and SECRET_POLY (secret) are both required")
        self.key_id = key_id
        self.key = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(secret)[:32])

    def headers(self, method, path):
        """path: URL path without query, e.g. /v1/positions."""
        ts = _now_ms()
        sig = self.key.sign(f"{ts}{method.upper()}{path.split('?', 1)[0]}".encode())
        return {"X-PM-Access-Key": self.key_id, "X-PM-Timestamp": ts, "X-PM-Signature": base64.b64encode(sig).decode()}


# ---------------------------------------------------------------- loading + check

def signers(env=None):
    """(KalshiSigner | None, PolyusSigner | None, problems) from .env."""
    env = load_env() if env is None else env
    problems, k, p = [], None, None
    try:
        k = KalshiSigner(env.get("KALSHI_KEY_ID"), env.get("KALSHI_API") or env.get("KALSHI_PRIVATE_KEY"))
    except Exception as e:  # noqa: BLE001
        problems.append(f"Kalshi: {e}")
    try:
        p = PolyusSigner(env.get("POLYMARKET_API"), env.get("SECRET_POLY") or env.get("POLYMARKET_SECRET"))
    except Exception as e:  # noqa: BLE001
        problems.append(f"Polymarket US: {e}")
    return k, p, problems


def check():
    """One signed read-only request per platform. Prints what each key can do; never prints secrets."""
    import httpx

    from .config import KALSHI_BASE, POLYUS_API_BASE, USER_AGENT
    k, p, problems = signers()
    for pr in problems:
        print("  !", pr)
    ok = True
    hdr = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if k:
        path = "/trade-api/v2/portfolio/balance"
        r = httpx.get(KALSHI_BASE.replace("/trade-api/v2", "") + path, headers={**hdr, **k.headers("GET", path)}, timeout=30)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        if r.status_code == 200:
            print(f"  Kalshi        OK   key type {k.kind}; GET {path} -> 200 (balance fields: {sorted(body)[:4]})")
        else:
            ok = False
            print(f"  Kalshi        FAIL GET {path} -> {r.status_code}: {str(body)[:200]}")
    if p:
        # Authenticated endpoints are on api.polymarket.us (docs.polymarket.us/api-reference/account/get-account-balances)
        path = "/v1/account/balances"
        r = httpx.get(POLYUS_API_BASE + path, headers={**hdr, **p.headers("GET", path)}, timeout=30)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        if r.status_code == 200:
            cur = [b.get("currency") for b in (body.get("balances") or []) if isinstance(b, dict)]
            print(f"  Polymarket US OK   GET {path} -> 200 (balances in: {cur})")
        else:
            ok = False
            print(f"  Polymarket US FAIL GET {path} -> {r.status_code}: {str(body)[:200]}")
    return ok
