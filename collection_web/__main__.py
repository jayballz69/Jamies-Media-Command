"""Production entry point shared by Windows preview and Linux containers."""

import logging
import os

from waitress import serve

from .app import create_app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app(start_scheduler=True)
    logging.info("Collection Manager ready; open the web app to create your account or sign in")
    try:
        serve(app, host=os.environ.get("CM_HOST", "127.0.0.1"), port=int(os.environ.get("CM_PORT", "8780")),
              threads=8, max_request_body_size=2 * 1024 * 1024, channel_timeout=60)
    finally:
        app.extensions["collection_service"].close()


if __name__ == "__main__":
    main()
