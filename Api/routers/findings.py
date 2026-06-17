from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from Application import ProjectService

from ..deps import require_loaded
from ..schemas import (
    AddFindingRawCaptureRequest,
    CreateFindingRequest,
    DeleteResponse,
    RawCaptureMeta,
    Finding,
    UpdateFindingRequest,
)
from .._common import _reject_unless_attested

router = APIRouter()


@router.get("/findings", response_model=list[Finding])
def list_findings(service: ProjectService = Depends(require_loaded)):
    return service.current.findings


@router.get("/finding/{finding_id}", response_model=Finding)
def get_finding(finding_id: str, service: ProjectService = Depends(require_loaded)):
    f = service.current.get_finding(finding_id)
    if f is None:
        raise HTTPException(status_code=404, detail=f"unknown finding {finding_id}")
    return f


@router.post("/finding", response_model=Finding, status_code=201)
def create_finding(body: CreateFindingRequest, service: ProjectService = Depends(require_loaded)):
    p = service.current
    f = p.add_finding(body.title, severity=body.severity, description=body.description, recommendation=body.recommendation)
    service.save(p)
    return f


@router.put("/finding/{finding_id}", response_model=Finding)
def update_finding(
    finding_id: str,
    body: UpdateFindingRequest,
    service: ProjectService = Depends(require_loaded),
):
    p = service.current
    try:
        f = p.update_finding(finding_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    service.save(p)
    return f


@router.delete("/finding/{finding_id}", response_model=DeleteResponse)
def delete_finding(finding_id: str, service: ProjectService = Depends(require_loaded)):
    p = service.current
    removed = p.remove_finding(finding_id)
    service.save(p)
    return DeleteResponse(removed=removed)


@router.post(
    "/finding/{finding_id}/raw-captures",
    response_model=RawCaptureMeta,
    status_code=201,
    summary="Add Finding Raw Capture  (upload only logs, screenshots, HTTP responses, 3rd party tool output. NEVER EVER upload AI/Agent generated output / summaries / bash-script outputs)",
)
def add_finding_raw_capture(
    finding_id: str,
    body: AddFindingRawCaptureRequest,
    service: ProjectService = Depends(require_loaded),
):
    _reject_unless_attested(
        body.agent_composed,
        body.this_really_is_raw_capture_and_not_an_ai_script,
    )
    p = service.current
    try:
        entry = p.add_finding_evidence(
            finding_id,
            body.name,
            body.data,
            mime_type=body.mime_type,
            source_type=body.source_type,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    service.save(p)
    return entry


@router.get("/finding/{finding_id}/raw-captures/{capture_id}")
def download_finding_raw_capture(
    finding_id: str,
    capture_id: str,
    service: ProjectService = Depends(require_loaded),
):
    p = service.current
    try:
        entry, abs_path = p.get_finding_evidence(finding_id, capture_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="raw capture file missing on disk")
    return FileResponse(
        path=str(abs_path),
        media_type=entry.get("mime_type", "application/octet-stream"),
        filename=entry["name"],
    )
