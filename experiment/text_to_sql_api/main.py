from fastapi import FastAPI
import uvicorn

from app.api.build import build_services
from app.api.routes.generate_sql import router as generate_sql_router
from app.api.routes.retrieve import router as retrieve_router
from app.core.config import get_default_settings, parse_args
from app.core.logging import configure_logging


def create_app(settings=None) -> FastAPI:
    configure_logging()
    settings = settings or get_default_settings()
    app = FastAPI(title="Text-to-SQL API")
    app.state.settings = settings
    app.state.services = build_services(settings)
    app.include_router(retrieve_router)
    app.include_router(generate_sql_router)
    return app


def main():
    settings = parse_args()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
else:
    app = create_app()
