"""One-shot VAPID keypair generator (push mechanics slice §2).

Run once per environment; paste the output into that environment's config:
  - local:  the repo `.env`
  - prod:   the Azure Web App's Environment variables

    docker compose exec backend python -m app.scripts.generate_vapid_keys

Web Push (VAPID) uses an EC P-256 keypair. The PUBLIC key is the browser's
`applicationServerKey` (safe to expose — served by GET /api/push/vapid-public-key,
same category as GOOGLE_CLIENT_ID). The PRIVATE key signs the push requests and
MUST stay secret. Both are emitted as unpadded base64url of the raw key bytes
(65-byte uncompressed point / 32-byte scalar) — the format the browser expects
and that Vapid02.from_raw() reconstructs on the send side.

Dev and prod may hold DIFFERENT pairs for this mechanics test (no shared data);
a subscription is always encrypted with, and validated against, one pair.
"""

import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate() -> tuple[str, str]:
    """Return (public_key_b64url, private_key_b64url) for a fresh EC P-256 pair."""
    vapid = Vapid02()
    vapid.generate_keys()
    private_key = vapid.private_key
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    private_raw = private_key.private_numbers().private_value.to_bytes(32, "big")
    return _b64url(public_raw), _b64url(private_raw)


def main() -> None:
    public_key, private_key = generate()
    print("# VAPID keys — paste into .env (local) or the Web App settings (prod).")
    print("# Keep VAPID_PRIVATE_KEY secret; VAPID_PUBLIC_KEY is safe to expose.")
    print(f"VAPID_PUBLIC_KEY={public_key}")
    print(f"VAPID_PRIVATE_KEY={private_key}")
    print("VAPID_SUBJECT=mailto:owner@example.com")


if __name__ == "__main__":
    main()
