from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.dependencies.admin_auth import require_admin_access
from app.backend.routers.admin_graph import create_admin_graph_router


class GraphServiceStub:
    """记录 admin graph router 调用的服务桩。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def get_graph_labels(self):
        self.calls.append(("labels", None))
        return {"ok": True, "data": ["Disease"]}

    async def get_graph_nodes(self, **kwargs):
        self.calls.append(("nodes", kwargs))
        return {"ok": True, "data": [{"id": 1}], "total": 1}

    async def get_graph_quality_report(self, **kwargs):
        self.calls.append(("quality_report", kwargs))
        return {"ok": True, "data": {"total_nodes": 2}}

    async def create_graph_node(self, data):
        self.calls.append(("create_node", data))
        return {"ok": True, "data": {"id": 1}}

    async def execute_cypher(self, data):
        self.calls.append(("cypher", data))
        return {"ok": True, "data": [{"name": "高血压"}]}

    async def list_graph_jobs(self, limit=10):
        self.calls.append(("jobs", limit))
        return {"ok": True, "data": [{"job_id": "graph-job-1"}]}

    async def get_graph_job(self, job_id):
        self.calls.append(("job", job_id))
        return {"ok": True, "data": {"job_id": job_id}}


def create_client(service: GraphServiceStub) -> TestClient:
    """构建只挂载 admin graph router 的测试客户端。"""
    app = FastAPI()
    app.include_router(create_admin_graph_router(service))
    app.dependency_overrides[require_admin_access] = lambda: None
    return TestClient(app)


def test_admin_graph_router_core_routes():
    service = GraphServiceStub()
    client = create_client(service)

    labels = client.get("/admin/graph/labels")
    assert labels.status_code == 200
    assert labels.json()["data"] == ["Disease"]

    nodes = client.get(
        "/admin/graph/nodes",
        params={"label": "Disease", "keyword": "高血压", "limit": 20, "offset": 0},
    )
    assert nodes.status_code == 200
    assert nodes.json()["total"] == 1

    quality = client.get(
        "/admin/graph/quality-report",
        params={"max_nodes": 20, "max_relationships": 40},
    )
    assert quality.status_code == 200
    assert quality.json()["data"]["total_nodes"] == 2

    created = client.post(
        "/admin/graph/nodes",
        json={"label": "Disease", "properties": {"name": "高血压"}},
    )
    assert created.status_code == 200
    assert created.json()["data"]["id"] == 1

    cypher = client.post(
        "/admin/graph/execute-cypher", json={"query": "MATCH (n) RETURN n LIMIT 1"}
    )
    assert cypher.status_code == 200
    assert cypher.json()["data"][0]["name"] == "高血压"

    jobs = client.get("/admin/graph/jobs", params={"limit": 5})
    assert jobs.status_code == 200
    assert jobs.json()["data"][0]["job_id"] == "graph-job-1"

    job = client.get("/admin/graph/jobs/graph-job-1")
    assert job.status_code == 200
    assert job.json()["data"]["job_id"] == "graph-job-1"

    assert ("labels", None) in service.calls
    assert (
        "quality_report",
        {"max_nodes": 20, "max_relationships": 40},
    ) in service.calls
    assert ("job", "graph-job-1") in service.calls
