"""启动仅用于真实图谱导入的低内存后端。"""

from pathlib import Path
import sys

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root))
    load_dotenv(project_root / ".env", override=True)

    from app.backend.app_factory import BackendAppTestOverrides, create_app
    from app.backend.services.startup_runtime_service import StartupRuntimeBehavior

    app = create_app(
        test_overrides=BackendAppTestOverrides(
            startup_behavior=StartupRuntimeBehavior(
                initialize_database=True,
                check_databases=False,
                migrate_neo4j_schema=True,
                prewarm_retrieval_resources=False,
                bootstrap_mcp=False,
                bootstrap_a2a=False,
                close_neo4j_on_shutdown=False,
            )
        )
    )
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
