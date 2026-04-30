"""后端服务入口。"""

# ruff: noqa: E402

from pathlib import Path

from dotenv import load_dotenv

from app.backend.app_factory import create_app

project_root = Path(__file__).resolve().parent.parent.parent
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=False)

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
