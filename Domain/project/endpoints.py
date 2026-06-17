from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class EndpointsMixin:
    @property
    def endpoints(self) -> list[dict]:
        items = self.data.setdefault("endpoints", [])
        for a in items:
            self._ensure_endpoint_defaults(a)
        return items

    def _per_endpoint_check_ids(self) -> list[str]:
        return [c["id"] for c in self.checklist if c.get("scope") == "per_endpoint"]

    def _ensure_endpoint_defaults(self, endpoint: dict) -> None:
        if endpoint.get("status") not in ENDPOINT_STATUSES:
            endpoint["status"] = "todo"
        if "checks" not in endpoint or not isinstance(endpoint["checks"], list):
            endpoint["checks"] = self._per_endpoint_check_ids()
        if "feature_group" not in endpoint or not isinstance(endpoint["feature_group"], str):
            endpoint["feature_group"] = ""
        if "checks_adjusted" not in endpoint or not isinstance(endpoint["checks_adjusted"], bool):
            endpoint["checks_adjusted"] = False
        if "title" not in endpoint or not isinstance(endpoint["title"], str):
            endpoint["title"] = ""

    @property
    def feature_groups(self) -> list[dict]:
        return self.data.setdefault("feature_groups", [])

    def add_endpoint(
        self,
        name: str,
        atype: str = "host",
        source: str = "manual",
        observations: str = "",
        title: str = "",
    ) -> dict:
        _validate_endpoint_name(name)
        _validate_observations(observations, field=f"endpoint {name!r} observations")
        ep = {
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "title": title,
            "type": atype,
            "observations": observations,
            "source": source,
            "added_at": _now(),
            "status": "todo",
            "checks": self._per_endpoint_check_ids(),
            "feature_group": "",
            "checks_adjusted": False,
        }
        self.endpoints.append(ep)
        self._ensure_endpoint_results(ep["id"])
        return ep

    def update_endpoint(self, endpoint_id: str, agent: dict | None = None, **fields) -> dict:
        endpoint = next((a for a in self.endpoints if a["id"] == endpoint_id), None)
        if endpoint is None:
            raise ValueError(f"unknown endpoint {endpoint_id}")
        if fields.get("observations") is not None:
            _validate_observations(
                fields["observations"],
                field=f"endpoint {endpoint.get('name') or endpoint_id!r} observations",
            )
        # The endpoint address (name) is the endpoint's identity and is fixed once
        # created. The title is a free label and can be edited.
        if fields.get("name") is not None and fields["name"] != endpoint.get("name"):
            raise ValueError("endpoint address cannot be changed after creation")
        for k in ("title", "type", "observations"):
            if k in fields and fields[k] is not None:
                endpoint[k] = fields[k]
        if fields.get("status") is not None:
            self.set_endpoint_status(endpoint_id, fields["status"], agent=agent)
        return endpoint

    def _set_endpoint_status(self, endpoint: dict, status: str) -> None:
        if status not in ENDPOINT_STATUSES:
            raise ValueError(f"bad endpoint status {status}")
        prev = endpoint.get("status")
        endpoint["status"] = status
        # Stamp when an endpoint becomes tested so the workflow panel can
        # surface the most recently finished three.
        if status == "tested" and prev != "tested":
            endpoint["tested_at"] = _now()

    def focused_endpoints(self) -> list[dict]:
        return [a for a in self.endpoints if a.get("status") == "focused"]

    def recently_tested_endpoints(self, limit: int = 3) -> list[dict]:
        """Endpoints with status=tested, sorted by tested_at desc (missing
        timestamps sort last). Drives the workflow panel's recent list."""
        tested = [a for a in self.endpoints if a.get("status") == "tested"]
        tested.sort(key=lambda a: a.get("tested_at") or 0, reverse=True)
        return tested[:limit]

    def endpoint_testing_candidates(self) -> list[dict]:
        return [a for a in self.endpoints if a.get("status", "todo") == "todo"]

    def focus_endpoint(self, endpoint_id: str, agent: dict | None = None) -> dict:
        endpoint = self.get_endpoint(endpoint_id)
        if endpoint is None:
            raise ValueError(f"unknown endpoint {endpoint_id}")
        if endpoint.get("status", "todo") in ("tested", "out-of-scope"):
            raise ValueError(f"endpoint {endpoint_id} is already {endpoint.get('status')}")
        if not endpoint.get("feature_group"):
            raise ValueError(f"endpoint {endpoint_id} is unassigned; assign it to a feature group before focusing")
        endpoint["status"] = "focused"
        endpoint.setdefault("checks_adjusted", False)
        if agent is not None:
            endpoint["focused_by"] = agent
        return endpoint

    def unfocus_endpoint(self, endpoint_id: str) -> dict:
        endpoint = self.get_endpoint(endpoint_id)
        if endpoint is None:
            raise ValueError(f"unknown endpoint {endpoint_id}")
        if endpoint.get("status") != "focused":
            raise ValueError(f"endpoint {endpoint_id} is not focused")
        endpoint["status"] = "todo"
        endpoint.pop("focused_by", None)
        return endpoint

    def confirm_endpoint_checks_adjusted(self, endpoint_id: str) -> dict:
        endpoint = self.get_endpoint(endpoint_id)
        if endpoint is None:
            raise ValueError(f"unknown endpoint {endpoint_id}")
        if endpoint.get("status") != "focused":
            raise ValueError(f"endpoint {endpoint_id} must be focused before confirming its checks")
        endpoint["checks_adjusted"] = True
        return endpoint

    def _endpoint_check_progress(self, endpoint: dict) -> list[dict]:
        links = self.checklist_links_for_endpoint(endpoint["id"])
        by_id = {link["check_id"]: link for link in links}
        return [by_id[cid] for cid in endpoint.get("checks", []) if cid in by_id]

    def next_endpoint_check(self, endpoint: dict) -> dict | None:
        if not endpoint.get("checks_adjusted", False):
            return None
        for link in self._endpoint_check_progress(endpoint):
            if link.get("status", "pending") == "pending":
                return link
        return None

    def _endpoint_testing_view(self, endpoint: dict) -> dict:
        checks = self._endpoint_check_progress(endpoint)
        done = sum(1 for c in checks if c.get("status", "pending") != "pending")
        return {
            "endpoint": endpoint,
            "checks_adjusted": endpoint.get("checks_adjusted", False),
            "next_check": self.next_endpoint_check(endpoint),
            "checks": checks,
            "done_count": done,
            "pending_count": len(checks) - done,
        }

    def focused_endpoints_workflow(self) -> dict:
        return {
            "focused": [self._endpoint_testing_view(a) for a in self.focused_endpoints()],
            "candidates": self.endpoint_testing_candidates(),
        }

    def completed_endpoint_check_count(self) -> int:
        """How many per-endpoint check results, summed across every endpoint,
        have moved off 'pending'. Any settled status counts as done — the same
        rule the endpoint testing view uses for its per-endpoint progress."""
        done = 0
        for endpoint in self.endpoints:
            for link in self._endpoint_check_progress(endpoint):
                if link.get("status", "pending") != "pending":
                    done += 1
        return done

    def _unfinished_endpoint_checks(self, endpoint: dict) -> list[dict]:
        return [c for c in self._endpoint_check_progress(endpoint) if c.get("status", "pending") == "pending"]

    def set_endpoint_status(self, endpoint_id: str, status: str, agent: dict | None = None) -> dict:
        endpoint = next((a for a in self.endpoints if a["id"] == endpoint_id), None)
        if endpoint is None:
            raise ValueError(f"unknown endpoint {endpoint_id}")
        if status not in ENDPOINT_STATUSES:
            raise ValueError(f"bad endpoint status {status}")
        prev = endpoint.get("status", "todo")
        if status == prev:
            return endpoint
        if status not in ENDPOINT_TRANSITIONS.get(prev, set()):
            if status == "out-of-scope":
                hint = (
                    "Reopen it first (tested → todo) if you really need to retest it."
                    if prev == "tested"
                    else "Unfocus it back to todo first if you meant to exclude it."
                )
                raise ValueError(
                    f"⚠ ILLEGAL TRANSITION — {prev} → out-of-scope is not allowed. "
                    "'out-of-scope' means the endpoint was deliberately excluded and never "
                    f"tested, so it can only be set from 'todo'. This endpoint is '{prev}'. {hint}"
                )
            raise ValueError(f"illegal endpoint transition: {prev} → {status}")
        if status == "focused":
            return self.focus_endpoint(endpoint_id, agent=agent)
        if status == "tested":
            if not (endpoint.get("observations") or "").strip():
                raise ValueError(
                    f"endpoint {endpoint_id} needs an observation before it can be marked tested: "
                    f"record what you tested and what you saw "
                    f"(PUT /api/v1/endpoint/{endpoint_id} with {{\"observations\": ...}}), then set status."
                )
            if not endpoint.get("checks_adjusted", False):
                raise ValueError(f"endpoint {endpoint_id} checks must be adjusted before it can be marked tested")
            unfinished = self._unfinished_endpoint_checks(endpoint)
            if unfinished:
                titles = ", ".join(c["title"] for c in unfinished[:5])
                more = f" + {len(unfinished) - 5} more" if len(unfinished) > 5 else ""
                raise ValueError(f"endpoint {endpoint_id} still has pending checks ({titles}{more})")
        if status == "tested":
            endpoint.pop("focused_by", None)
            if agent is not None:
                endpoint["done_by"] = agent
        if status == "todo" and prev == "tested":
            # Reopen: drop the tested stamp so the endpoint leaves the
            # "recently tested" list and reads as genuinely un-tested again,
            # and clear the stale attribution tags.
            endpoint.pop("tested_at", None)
            endpoint.pop("done_by", None)
            endpoint.pop("focused_by", None)
        if status == "out-of-scope":
            endpoint.pop("focused_by", None)
        self._set_endpoint_status(endpoint, status)
        return endpoint

    def update_endpoint_checks(self, endpoint_id: str, check_ids: list[str]) -> dict:
        endpoint = next((a for a in self.endpoints if a["id"] == endpoint_id), None)
        if endpoint is None:
            raise ValueError(f"unknown endpoint {endpoint_id}")
        valid = set(self._per_endpoint_check_ids())
        seen: set[str] = set()
        kept: list[str] = []
        for cid in check_ids:
            if cid in valid and cid not in seen:
                kept.append(cid)
                seen.add(cid)
        endpoint["checks"] = kept
        endpoint["checks_adjusted"] = False
        return endpoint

    def assign_endpoint_check(self, endpoint_id: str, check_id: str) -> dict:
        endpoint = next((a for a in self.endpoints if a["id"] == endpoint_id), None)
        if endpoint is None:
            raise ValueError(f"unknown endpoint {endpoint_id}")
        if check_id not in self._per_endpoint_check_ids():
            raise ValueError(f"{check_id} is not a per-endpoint check")
        if check_id not in endpoint["checks"]:
            endpoint["checks"].append(check_id)
            endpoint["checks_adjusted"] = False
        return endpoint

    def get_endpoint(self, endpoint_id: str) -> dict | None:
        return next((a for a in self.endpoints if a["id"] == endpoint_id), None)

    def remove_endpoint(self, endpoint_id: str) -> bool:
        before = len(self.endpoints)
        self.data["endpoints"] = [a for a in self.endpoints if a["id"] != endpoint_id]
        for item in self.checklist:
            if item["scope"] == "per_endpoint":
                item.get("results", {}).pop(endpoint_id, None)
        return len(self.endpoints) < before

    def add_feature_group(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("feature group name is required")
        group = {"id": uuid.uuid4().hex[:8], "name": name}
        self.feature_groups.append(group)
        return group

    def remove_feature_group(self, group_id: str) -> bool:
        before = len(self.feature_groups)
        self.data["feature_groups"] = [g for g in self.feature_groups if g["id"] != group_id]
        if len(self.feature_groups) == before:
            return False
        for endpoint in self.endpoints:
            if endpoint.get("feature_group") == group_id:
                endpoint["feature_group"] = ""
        return True

    def set_endpoint_feature_group(self, endpoint_id: str, group_id: str | None) -> dict:
        endpoint = next((a for a in self.endpoints if a["id"] == endpoint_id), None)
        if endpoint is None:
            raise ValueError(f"unknown endpoint {endpoint_id}")
        gid = (group_id or "").strip()
        if gid and not any(g["id"] == gid for g in self.feature_groups):
            raise ValueError(f"unknown feature group {gid}")
        endpoint["feature_group"] = gid
        return endpoint

    def _ensure_endpoint_results(self, endpoint_id: str):
        for item in self.checklist:
            if item["scope"] == "per_endpoint":
                item.setdefault("results", {})
                item["results"].setdefault(endpoint_id, self._empty_result())

    def checklist_links_for_endpoint(self, endpoint_id: str) -> list[dict]:
        endpoint = next((a for a in self.endpoints if a["id"] == endpoint_id), None)
        assigned = set(endpoint.get("checks", [])) if endpoint else set()
        links = []
        for item in self.checklist:
            if item["scope"] != "per_endpoint":
                continue
            if item["id"] not in assigned:
                continue
            r = item.get("results", {}).get(endpoint_id) or self._empty_result()
            links.append({
                "check_id": item["id"],
                "title": item["title"],
                "phase": item.get("phase", "checklist"),
                "category": item.get("category", "uncategorized"),
                "category_name": item.get("category_name", "Uncategorized"),
                "status": r.get("status", "pending"),
                "observations": r.get("observations", ""),
            })
        return links

    def endpoint_status_counts(self) -> dict:
        counts = {"todo": 0, "focused": 0, "tested": 0, "out-of-scope": 0, "total": 0}
        for endpoint in self.endpoints:
            status = endpoint.get("status", "todo")
            if status not in counts:
                status = "todo"
            counts[status] += 1
            counts["total"] += 1
        return counts

    def endpoint_testing_ready(self) -> dict:
        counts = self.endpoint_status_counts()
        blocking = counts["todo"] + counts["focused"]
        return {
            "ready": blocking == 0,
            "blocking": blocking,
            "counts": counts,
            "message": (
                "all endpoints are tested or out-of-scope"
                if blocking == 0
                else f"{counts['todo']} endpoint(s) still todo, {counts['focused']} still focused"
            ),
        }

    def all_endpoints_unassigned(self) -> bool:
        if not self.endpoints:
            return False
        valid_groups = {g["id"] for g in self.feature_groups}
        return all(not a.get("feature_group") or a.get("feature_group") not in valid_groups for a in self.endpoints)
