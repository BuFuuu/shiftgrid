from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class CredentialsMixin:
    @property
    def credentials(self) -> list[dict]:
        return self.data.setdefault("credentials", [])

    def add_credential(self, username: str = "", password: str = "", observations: str = "", source: str = "manual") -> dict:
        cred = {
            "id": uuid.uuid4().hex[:8],
            "username": username,
            "password": password,
            "observations": observations,
            "source": source,
            "added_at": _now(),
        }
        self.credentials.append(cred)
        return cred

    def remove_credential(self, cred_id: str) -> bool:
        before = len(self.credentials)
        self.data["credentials"] = [c for c in self.credentials if c["id"] != cred_id]
        return len(self.credentials) < before
