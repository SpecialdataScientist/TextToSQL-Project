import argparse
import logging

import uvicorn

from agent_server.api import create_app
from agent_server.config import Settings


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    defaults = Settings.from_env()
    parser = argparse.ArgumentParser(description="LLM client server for the PostgreSQL column MCP")
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    args = parser.parse_args()
    settings = Settings.model_validate({**defaults.model_dump(), "host": args.host, "port": args.port})
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
else:
    app = create_app()
