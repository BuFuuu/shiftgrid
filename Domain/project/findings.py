from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class FindingsMixin:
    @property
    def findings(self) -> list[dict]:
        return self.data.setdefault("findings", [])

    def add_finding(
        self,
        title: str,
        severity: str = "info",
        description: str = "",
        recommendation: str = "",
    ) -> dict:
        now = _now()
        finding = {
            "id": uuid.uuid4().hex[:8],
            "title": title,
            "severity": severity,
            "description": description,
            "recommendation": recommendation,
            "evidence": [],
            "created_at": now,
            "updated_at": now,
        }
        self.findings.append(finding)
        return finding

    def add_finding_evidence(
        self,
        finding_id: str,
        name: str,
        data_b64: str,
        mime_type: str = "application/octet-stream",
        source_type: str = "other",
        description: str = "",
    ) -> dict:
        f = self.get_finding(finding_id)
        if f is None:
            raise ValueError(f"unknown finding {finding_id}")
        rel_dir = Path(FINDINGS_DIR) / finding_id
        entry = self._write_evidence(rel_dir, name, data_b64, mime_type, source_type, description)
        f.setdefault("evidence", []).append(entry)
        f["updated_at"] = _now()
        return entry

    def get_finding_evidence(self, finding_id: str, evidence_id: str) -> tuple[dict, Path]:
        f = self.get_finding(finding_id)
        if f is None:
            raise ValueError(f"unknown finding {finding_id}")
        for e in f.get("evidence", []):
            if e["id"] == evidence_id:
                return e, (self.folder / e["path"]).resolve()
        raise ValueError(f"unknown evidence {evidence_id} for finding {finding_id}")

    def get_finding(self, finding_id: str) -> dict | None:
        return next((f for f in self.findings if f["id"] == finding_id), None)

    def update_finding(self, finding_id: str, **fields) -> dict:
        f = self.get_finding(finding_id)
        if f is None:
            raise ValueError(f"unknown finding {finding_id}")
        for k in ("title", "severity", "description", "recommendation"):
            if k in fields and fields[k] is not None:
                f[k] = fields[k]
        f["updated_at"] = _now()
        return f

    def remove_finding(self, finding_id: str) -> bool:
        before = len(self.findings)
        self.data["findings"] = [f for f in self.findings if f["id"] != finding_id]
        return len(self.findings) < before
