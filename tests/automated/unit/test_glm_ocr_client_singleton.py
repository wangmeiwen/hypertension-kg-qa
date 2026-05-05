from concurrent.futures import ThreadPoolExecutor
import threading


def test_get_glm_ocr_client_initializes_once_under_concurrency(monkeypatch):
    """验证 OCR 全局客户端在并发首次访问时只初始化一次。"""
    from app.document_parser import glm_ocr_client as module

    created = []
    created_lock = threading.Lock()

    class FakeConfig:
        ZHIPU_API_KEY = "test-key"

    class FakeGlmOcrClient:
        def __init__(self, api_key):
            self.api_key = api_key
            with created_lock:
                created.append(self)

    monkeypatch.setattr(module, "_glm_ocr_client", None)
    monkeypatch.setattr(module, "Config", FakeConfig)
    monkeypatch.setattr(module, "GlmOcrClient", FakeGlmOcrClient)

    with ThreadPoolExecutor(max_workers=16) as executor:
        clients = list(executor.map(lambda _: module.get_glm_ocr_client(), range(64)))

    assert len(created) == 1
    assert all(client is created[0] for client in clients)
    assert created[0].api_key == "test-key"
