from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class ChecksMixin:
    @property
    def checklist(self) -> list[dict]:
        return self.data.setdefault("checklist", [])

    def _get_check(self, check_id: str) -> dict:
        item = next((c for c in self.checklist if c["id"] == check_id), None)
        if item is None:
            raise ValueError(f"unknown check {check_id}")
        return item

    def get_check(self, check_id: str) -> dict:
        return dict(self._get_check(check_id))

    def _get_or_init_result(self, item: dict, endpoint_id: str | None) -> dict:
        if item["scope"] == "per_endpoint":
            results = item.setdefault("results", {})
            r = results.setdefault(endpoint_id or "_global", self._empty_result())
        else:
            results = item.setdefault("results", {})
            r = results.setdefault("_global", self._empty_result())
        r.setdefault("status", "pending")
        r.setdefault("observations", "")
        r.setdefault("evidence", [])
        r.setdefault("ts", None)
        return r

    def update_check(
        self,
        check_id: str,
        status: str | None = None,
        observations: str | None = None,
        endpoint_id: str | None = None,
        agent: dict | None = None,
    ) -> dict:
        item = self._get_check(check_id)
        scope = item.get("scope")
        if observations is not None:
            _validate_observations(observations, field=f"check {check_id} observations")
        self._require_endpoint_id_match(item, endpoint_id)
        if scope == "per_endpoint":
            # A per-endpoint check is claimed via its endpoint's focus, never on
            # its own — 'focused' is not a status it can take directly.
            if status == "focused":
                raise ValueError(
                    f"check {check_id} is per-endpoint; it cannot be set to 'focused' directly — "
                    f"focus its endpoint instead (per-endpoint checks are claimed via endpoint focus)"
                )
            if endpoint_id and status is not None:
                endpoint = self.get_endpoint(endpoint_id)
                if endpoint is not None and endpoint.get("status") == "focused":
                    if check_id not in endpoint.get("checks", []):
                        raise ValueError(f"check {check_id} is not assigned to focused endpoint {endpoint_id}")
                    if not endpoint.get("checks_adjusted", False):
                        raise ValueError(f"checks for endpoint {endpoint_id} must be adjusted before running endpoint checks")
        r = self._get_or_init_result(item, endpoint_id)

        # Mandatory focus gate for global-scope checks: claim the check
        # (status='focused') before recording any result on it. Setting 'focused'
        # (the claim) or 'pending' (release) is always allowed.
        if scope == "global":
            currently_focused = r.get("status") == "focused"
            becoming_focused = status == "focused"
            if status in CHECK_RESULT_STATUSES and not currently_focused:
                raise self._check_focus_required(check_id, f"set its status to '{status}'")
            if observations is not None and not (currently_focused or becoming_focused):
                raise self._check_focus_required(check_id, "record observations")
            # One focused global check per agent: claim a new one only after
            # finishing or releasing the current one.
            if becoming_focused and not currently_focused and agent is not None:
                held = next(
                    (c for c in self.focused_global_checks()
                     if c["id"] != check_id
                     and (((c.get("results") or {}).get("_global") or {}).get("focused_by") or {}).get("id") == agent.get("id")),
                    None,
                )
                if held is not None:
                    raise ValueError(
                        f"you already have check {held['id']} focused; finish it "
                        f"(POST /api/v1/check/{held['id']}/finish) or release it "
                        f"(PUT /api/v1/check/{held['id']}/status {{\"status\": \"pending\"}}) "
                        f"before focusing another — one focused check per agent."
                    )

        if status is not None:
            if status not in CHECK_STATUSES:
                raise ValueError(f"bad status {status}")
            # Effective observations after this call — either the value passed in
            # the same request, or what's already stored.
            new_obs = observations if observations is not None else r.get("observations", "")
            if status == "not applicable":
                if not (new_obs or "").strip():
                    raise ValueError(
                        f"cannot mark check {check_id} 'not applicable' without observations: "
                        f"set observations (PUT /api/v1/check/{check_id}/observations) explaining "
                        f"why this check does not apply, then set status."
                    )
            elif self.observations_required and status not in ("pending", "focused"):
                if not (new_obs or "").strip():
                    raise ValueError(
                        f"cannot set check {check_id} status to '{status}' without observations: "
                        f"this project requires an observation before changing a check's status "
                        f"(set observations via PUT /api/v1/check/{check_id}/observations, then set status)."
                    )
            r["status"] = status
            if scope == "global":
                if status == "focused":
                    if agent is not None:
                        r["focused_by"] = agent
                    # Re-focusing a settled check reopens it: it must be finished
                    # again before phase advance.
                    r["finished"] = False
                elif status == "pending":
                    # Release: drop the claim and any stale attribution, and
                    # reopen if it had been finished.
                    r.pop("focused_by", None)
                    r.pop("done_by", None)
                    r["finished"] = False
                # 'not applicable' is the one terminal status with no further work
                # conceivable, and it already mandates observations (above), so it
                # satisfies the finish invariant on its own. Auto-settle it so it
                # drops out of the 'what's next' count and the advance gate without
                # a separate /finish.
                elif status == "not applicable":
                    r["finished"] = True
                # Backing a global-check out of a terminal status reopens it.
                elif r.get("finished"):
                    r["finished"] = False
        if observations is not None:
            r["observations"] = observations
        r["ts"] = _now()
        return r

    @staticmethod
    def _check_focus_required(check_id: str, action: str) -> FocusRequiredError:
        return FocusRequiredError(
            f"focus global check {check_id} before you {action}: this project requires a "
            "check to be claimed (status='focused') before any result is recorded on it, "
            "so concurrent agents and the operator can see it is in progress.",
            kind="check_not_focused",
            fix={
                "method": "PUT",
                "path": f"/api/v1/check/{check_id}/status",
                "example_body": {"status": "focused"},
            },
        )

    @staticmethod
    def _require_endpoint_id_match(item: dict, endpoint_id: str | None) -> None:
        scope = item.get("scope")
        if scope == "per_endpoint" and not endpoint_id:
            raise ValueError(
                f"check {item['id']} is per-endpoint; endpoint_id is required (edit it on the endpoint, not the checklist)"
            )
        if scope == "global" and endpoint_id:
            raise ValueError(
                f"check {item['id']} is global-scope; endpoint_id must not be provided"
            )

    def set_check_status(self, check_id: str, status: str, endpoint_id: str | None = None, agent: dict | None = None) -> dict:
        return self.update_check(check_id, status=status, endpoint_id=endpoint_id, agent=agent)

    def set_check_observations(self, check_id: str, observations: str, endpoint_id: str | None = None, agent: dict | None = None) -> dict:
        return self.update_check(check_id, observations=observations, endpoint_id=endpoint_id, agent=agent)

    def current_check_observations(self, check_id: str, endpoint_id: str | None = None) -> str:
        """The observations currently stored for a check's result — the global
        result for a global-scope check, or the endpoint-specific result for a
        per-endpoint check. Read-only; used to gate blind overwrites."""
        item = self._get_check(check_id)
        key = endpoint_id if item.get("scope") == "per_endpoint" else "_global"
        return ((item.get("results") or {}).get(key) or {}).get("observations", "")

    def add_check_evidence(
        self,
        check_id: str,
        name: str,
        data: bytes,
        mime_type: str = "application/octet-stream",
        endpoint_id: str | None = None,
        source_type: str = "other",
        description: str = "",
    ) -> dict:
        item = self._get_check(check_id)
        self._require_endpoint_id_match(item, endpoint_id)
        r = self._get_or_init_result(item, endpoint_id)
        # Focus gate: raw captures are a result write, so a global check must have
        # been claimed. Allow it once a result is recorded (or the check is
        # finished) too — captures are commonly attached right after settling the
        # result — but still block an unclaimed 'pending' check. (Per-endpoint
        # capture is gated by endpoint focus instead.)
        if item["scope"] == "global" and r.get("status", "pending") == "pending":
            raise self._check_focus_required(check_id, "attach raw captures to it")
        if item["scope"] == "per_endpoint":
            rel_dir = Path(ENDPOINTS_DIR) / (endpoint_id or "_unassigned") / check_id
        else:
            rel_dir = Path(CHECKLIST_DIR) / check_id
        entry = self._write_evidence(rel_dir, name, data, mime_type, source_type, description)
        r["evidence"].append(entry)
        r["ts"] = _now()
        return entry

    def get_check_evidence(
        self,
        check_id: str,
        evidence_id: str,
        endpoint_id: str | None = None,
    ) -> tuple[dict, Path]:
        item = self._get_check(check_id)
        r = self._get_or_init_result(item, endpoint_id)
        for e in r.get("evidence", []):
            if e["id"] == evidence_id:
                return e, (self.folder / e["path"]).resolve()
        raise ValueError(f"unknown evidence {evidence_id} for check {check_id}")

    def check_view(self, check_id: str) -> dict:
        item = self._get_check(check_id)
        return dict(item)

    def next_step(self, endpoint_id: str | None = None) -> dict | None:
        phase_id = self.current_phase
        for item in self.checklist:
            if item.get("phase") != phase_id:
                continue
            if item["scope"] == "global":
                r = item.get("results", {}).get("_global") or self._empty_result()
                if r.get("status", "pending") == "pending":
                    return {"check_id": item["id"], "title": item["title"], "phase": phase_id, "scope": "global"}
            else:
                results = item.setdefault("results", {})
                target_endpoints = [a for a in self.endpoints if endpoint_id is None or a["id"] == endpoint_id]
                for a in target_endpoints:
                    r = results.setdefault(a["id"], self._empty_result())
                    if r.get("status", "pending") == "pending":
                        return {
                            "check_id": item["id"],
                            "title": item["title"],
                            "phase": phase_id,
                            "scope": "per_endpoint",
                            "endpoint_id": a["id"],
                            "endpoint_name": a["name"],
                        }
        return None

    def global_check_counts(self) -> tuple[int, int]:
        """(settled, total) across global-scope checks. A check counts as settled
        once it is finished or marked 'not applicable' — the same rule the
        checklist page's progress meter and the advance gate use."""
        total = 0
        done = 0
        for item in self.checklist:
            if item.get("scope") != "global":
                continue
            total += 1
            r = (item.get("results") or {}).get("_global") or {}
            if r.get("finished") or r.get("status") == "not applicable":
                done += 1
        return done, total

    def _pending_global_checks(self) -> list[dict]:
        out = []
        for item in self.checklist:
            if item.get("scope") != "global":
                continue
            r = (item.get("results") or {}).get("_global") or {}
            if r.get("status", "pending") == "pending":
                out.append(item)
        return out

    def _unfinished_global_checks(self) -> list[dict]:
        """Global-scope checks that have not been marked finished. Drives the
        advance gate out of the Work-on-Checklist phase. A 'not applicable'
        check counts as settled even if it predates auto-finish, so it never
        lingers in the 'what's next' count or blocks the gate."""
        out = []
        for item in self.checklist:
            if item.get("scope") != "global":
                continue
            r = (item.get("results") or {}).get("_global") or {}
            if r.get("finished") or r.get("status") == "not applicable":
                continue
            out.append(item)
        return out

    def focused_global_checks(self) -> list[dict]:
        """Global-scope checks currently focused (status == 'focused') by an
        agent/operator — the claim that precedes work."""
        out = []
        for item in self.checklist:
            if item.get("scope") != "global":
                continue
            r = (item.get("results") or {}).get("_global") or {}
            if r.get("status") == "focused":
                out.append(item)
        return out

    def finish_global_check(self, check_id: str, agent: dict | None = None) -> dict:
        """Mark a global-scope check finished. Requires a recorded result status
        (not pending and not the 'focused' working state) and a non-empty
        observations field. Clears the live focus attribution."""
        item = self._get_check(check_id)
        if item.get("scope") != "global":
            raise ValueError(
                f"check {check_id} is not a global-scope check; finish only applies to global-scope checks"
            )
        r = self._get_or_init_result(item, None)
        missing = []
        if r.get("status", "pending") in ("pending", "focused"):
            missing.append("status (record a result; not pending/focused)")
        if not (r.get("observations") or "").strip():
            missing.append("observations")
        if missing:
            raise ValueError(
                f"cannot finish check {check_id}: missing {', '.join(missing)}. "
                f"Set status (PUT /api/v1/check/{check_id}/status) and observations "
                f"(PUT /api/v1/check/{check_id}/observations) first."
            )
        already_finished = bool(r.get("finished"))
        # Runs gate: when the check is configured to run again, reset it to pending
        # (keeping observations) and nudge for another run instead of finishing.
        # Skipped when re-finishing an already-finished check.
        if not already_finished:
            target = _normalize_runs(item.get("runs"))
            completed = int(r.get("runs_completed", 0) or 0)
            if _runs_should_loop(target, completed):
                r["status"] = "pending"
                r["finished"] = False
                r["runs_completed"] = completed + 1
                r.pop("focused_by", None)
                r.pop("done_by", None)
                r["ts"] = _now()
                raise RunAgainError(
                    _runs_message("check", f"Check '{item.get('title', check_id)}'", completed + 2, target),
                    runs_completed=completed + 1, target=target,
                )
        r["finished"] = True
        r.pop("focused_by", None)
        if agent is not None:
            r["done_by"] = agent
        r["ts"] = _now()
        return r

    def recently_worked_global_checks(self, limit: int = 3) -> list[dict]:
        """Finished global-checks ordered by most recently touched (ts desc),
        excluding those currently focused. Drives the workflow-page recent panel."""
        rows = []
        for item in self.checklist:
            if item.get("scope") != "global":
                continue
            r = (item.get("results") or {}).get("_global") or {}
            if not r.get("finished"):
                continue
            if r.get("status") == "focused":
                continue
            rows.append((r.get("ts") or 0, item))
        rows.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in rows[:limit]]

    def check_runs(self, check_id: str) -> "int | str":
        """How many times this global check should be worked before it settles —
        a positive int or 'indefinite' (1 = once). Edited per-project on the check."""
        return _normalize_runs(self._get_check(check_id).get("runs"))

    def check_runs_completed(self, check_id: str) -> int:
        r = (self._get_check(check_id).get("results") or {}).get("_global") or {}
        return int(r.get("runs_completed", 0) or 0)

    def set_check_runs(self, check_id: str, value) -> dict:
        """Operator: set how many times this check runs before it can finish."""
        item = self._get_check(check_id)
        item["runs"] = _normalize_runs(value)
        return dict(item)

    def set_try_harder_checks(self, enabled: bool) -> None:
        """The checklist page's 'Try harder' switch. Turning it on adds one run
        (+1) to every global check; turning it off removes one (floored at 1).
        'indefinite' counts are left untouched. Scoped to checks only."""
        enabled = bool(enabled)
        if enabled == self.try_harder_checks:
            return
        delta = 1 if enabled else -1
        for item in self.checklist:
            if item.get("scope") == "global":
                item["runs"] = _bump_runs(item.get("runs"), delta)
        self.data["try_harder_checks"] = enabled

    def update_check_context(
        self,
        check_id: str,
        description: str | None = None,
        examples: str | None = None,
    ) -> dict:
        item = self._get_check(check_id)
        if description is not None:
            item["description"] = description
        if examples is not None:
            item["examples"] = examples
        return dict(item)
