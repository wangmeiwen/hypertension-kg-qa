from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.dependencies.admin_auth import require_admin_login
from app.backend.routers.admin_documents import create_admin_documents_router


class DocumentsServiceStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def upload_document(self, file):
        self.calls.append(("upload", file.filename))
        return {"ok": True, "data": {"job_id": "legacy-job"}}

    async def get_document_stats(self):
        self.calls.append(("stats", None))
        return {"ok": True, "data": {"row_count": 12}}

    async def submit_text_embedding_job(self, file):
        self.calls.append(("submit", file.filename))
        return {"ok": True, "data": {"job_id": "text-job-1"}}

    async def list_text_embedding_jobs(self, limit=10):
        self.calls.append(("list", limit))
        return {"ok": True, "data": [{"job_id": "text-job-1"}]}

    async def get_text_embedding_job(self, job_id):
        self.calls.append(("detail", job_id))
        return {"ok": True, "data": {"job_id": job_id, "status": "queued"}}


def create_client(service: DocumentsServiceStub) -> TestClient:
    app = FastAPI()
    app.include_router(create_admin_documents_router(service))
    app.dependency_overrides[require_admin_login] = lambda: None
    return TestClient(app)


def test_admin_documents_router_routes():
    service = DocumentsServiceStub()
    client = create_client(service)

    uploaded = client.post("/admin/text-embedding/jobs/embed", files={"file": ("guide.pdf", b"hello", "application/pdf")})
    assert uploaded.status_code == 200
    assert uploaded.json()["data"]["job_id"] == "text-job-1"

    stats = client.get("/admin/documents/stats")
    assert stats.status_code == 200
    assert stats.json()["data"]["row_count"] == 12

    jobs = client.get("/admin/text-embedding/jobs", params={"limit": 5})
    assert jobs.status_code == 200
    assert jobs.json()["data"][0]["job_id"] == "text-job-1"

    detail = client.get("/admin/text-embedding/jobs/text-job-1")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "queued"

    assert ("submit", "guide.pdf") in service.calls
    assert ("detail", "text-job-1") in service.calls
