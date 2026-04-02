from __future__ import annotations

import uvicorn

from app.config import load_settings
from app.logging_setup import configure_logging
from app.web import create_web_app


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    app = create_web_app(settings)
    uvicorn.run(
        app,
        host=settings.web_host,
        port=settings.web_port,
        reload=settings.web_reload,
    )


if __name__ == "__main__":
    main()
