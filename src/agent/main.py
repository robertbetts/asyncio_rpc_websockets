import logging
import os

from agent.process import ProcessRunTime, create_app

log_format = (
    "%(levelname).1s %(asctime)s %(name)s %(funcName)s %(lineno)d: %(message)s"
)
logging.basicConfig(level=logging.DEBUG, format=log_format)
logging.getLogger("nuropb").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("pika").setLevel(logging.WARNING)
logger = logging.getLogger("agent.main")

def main():
    ProcessRunTime().run()


if __name__ != "__main__":
    logger.info(f"Process {os.getpid()} running as a WSGI application")
    gunicorn_app = create_app()
else:
    logger.info(f"Process {os.getpid()} running as a standalone application")
    main()
