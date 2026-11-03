"""Management extension: the credentials API endpoints.

Injected into the FastAPI app via the factory's `extra_api_routers` knob, so the
bare core API does not expose credential storage at all.
"""
from fastapi import APIRouter, Depends

from Api.deps import require_loaded
from Api.schemas import Credential, CredentialsListResponse, AddCredentialRequest
from Application import ProjectService

router = APIRouter()


@router.get("/project/credentials", response_model=CredentialsListResponse)
def list_credentials(service: ProjectService = Depends(require_loaded)):
    return CredentialsListResponse(credentials=service.current.credentials)


@router.post("/project/credentials", response_model=Credential, status_code=201)
def add_credential(body: AddCredentialRequest, service: ProjectService = Depends(require_loaded)):
    p = service.current
    cred = p.add_credential(body.username, body.password, body.observations, source=body.source)
    service.save(p)
    return cred
