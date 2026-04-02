"""后端服务入口。"""

# ruff: noqa: E402

from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent.parent
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=True)

from app.backend.app_factory import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
