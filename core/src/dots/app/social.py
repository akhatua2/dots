"""Per-key API access and pool endorsements, kept by the hub.

The hub's shared join token is the owner credential. On top of it the owner
can mint scoped API keys for other apps (`POST /v1/keys`), and members can
endorse a pool. Keys can call the inference endpoints only; managing keys and
endorsements stays owner-only. State is one small JSON file under the hub's
home so it survives restarts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import secrets
import time

# What a minted key may call. Everything else needs the owner (join) token.
KEY_PATHS = frozenset({"/v1/chat/completions", "/v1/models", "/v1/pools"})
KEY_PREFIX = "dots_sk_live_"


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class SocialStore:
    def __init__(self, path: pathlib.Path | None = None) -> None:
        self.path = path
        self.endorsements: dict[str, list[str]] = {}
        self.keys: list[dict] = []
        if path is not None and path.is_file():
            try:
                data = json.loads(path.read_text())
                self.endorsements = data.get("endorsements", {})
                self.keys = data.get("keys", [])
            except Exception:  # noqa: BLE001 - a corrupt file must not stop the hub
                pass

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"endorsements": self.endorsements, "keys": self.keys})
        )

    # --- endorsements ---------------------------------------------------
    def endorse(self, pool_id: str, account_id: str) -> int:
        who = self.endorsements.setdefault(pool_id, [])
        if account_id not in who:
            who.append(account_id)
            self._save()
        return len(who)

    def unendorse(self, pool_id: str, account_id: str) -> int:
        who = self.endorsements.get(pool_id, [])
        if account_id in who:
            who.remove(account_id)
            self._save()
        return len(who)

    # --- keys -----------------------------------------------------------
    def create_key(
        self, name: str, scopes: list[str], monthly_limit: int | None
    ) -> tuple[dict, str]:
        secret = KEY_PREFIX + secrets.token_urlsafe(18)
        record = {
            "id": "key_" + secrets.token_hex(4),
            "name": name or "Untitled",
            "scopes": scopes or ["pools"],
            "monthly_limit": monthly_limit,
            "tail": secret[-4:],
            "hash": _hash(secret),
            "created": time.time(),
            "last_used": None,
            "requests": 0,
        }
        self.keys.append(record)
        self._save()
        return self.public(record), secret

    def revoke_key(self, key_id: str) -> bool:
        n = len(self.keys)
        self.keys = [k for k in self.keys if k["id"] != key_id]
        if len(self.keys) != n:
            self._save()
            return True
        return False

    def verify(self, secret: str) -> dict | None:
        digest = _hash(secret)
        for k in self.keys:
            if hmac.compare_digest(k["hash"], digest):
                return k
        return None

    def touch(self, record: dict) -> None:
        record["last_used"] = time.time()
        record["requests"] = int(record.get("requests", 0)) + 1
        self._save()

    @staticmethod
    def public(record: dict) -> dict:
        return {k: v for k, v in record.items() if k != "hash"}

    def list_keys(self) -> list[dict]:
        return [self.public(k) for k in self.keys]
