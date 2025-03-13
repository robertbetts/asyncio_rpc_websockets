import logging
from typing import Any, Dict
from uuid import uuid4
import asyncio
import time
from multiprocessing import Pool

from nuropb.rmq_api import RMQAPI

logger = logging.getLogger("process-worker")


class BasicExampleService:
    _service_name = "process-worker"
    _instance_id = uuid4().hex

    async def test_async_method(self, **params: Any) -> str:
        logger.info("test_async_method called")
        _ = self
        asyncio.sleep(0.1)
        return "response from test_method"

    def small(self, **kwargs) -> Dict[str, Any]:
        logger.debug("small called")
        _ = self
        result ={
            "reply": "response from small",
        }
        result.update(**kwargs)
        return result


    async def medium(self, **kwargs) -> Dict[str, Any]:
        logger.debug("small called")
        _ = self
        result ={
            "reply": "response from small",
        }
        await asyncio.sleep(0.01)
        result.update(**kwargs)
        return result

async def _worker_main():
    amqp_url = "amqp://guest:guest@localhost:5673/nuropb-example"
    service_instance = BasicExampleService()
    transport_settings = {
        "rpc_bindings": [service_instance._service_name],
    }
    mesh_api = RMQAPI(
        service_instance=service_instance,
        service_name=service_instance._service_name,
        instance_id=service_instance._instance_id,
        amqp_url=amqp_url,
        transport_settings=transport_settings,
        prefetch_count=1,
    )
    await mesh_api.connect()
    try:
        logging.info("Service %s ready", service_instance._service_name)
        await asyncio.Event().wait()
        logging.info("Shutting down signal received")
        await mesh_api.disconnect()
    except BaseException as err:
        logging.info("Shutting down. %s: %s", type(err).__name__, err)
        await mesh_api.disconnect()
    finally:
        logging.info("Service %s done", service_instance._service_name)

    logging.info("Service %s done", service_instance._service_name)


def worker_main(index=0):
    log_format = (
        "%(levelname).1s %(asctime)s %(name) -25s %(funcName) "
        "-35s %(lineno) -5d: %(message)s"
    )
    logging.basicConfig(level=logging.INFO, format=log_format)
    logging.getLogger("pika").setLevel(logging.WARNING)
    asyncio.run(_worker_main())


def main():
    pool_size = 4
    with Pool(pool_size) as p:
        start_time = time.time()
        _ = p.map(worker_main, range(pool_size))
        taken = time.time() - start_time

    logger.info(f"A total time of {taken} to process") 

    
    logger.info("Terminated client test simulator instance")

if __name__ == "__main__":
    log_format = (
        "%(levelname).1s %(asctime)s %(name) -25s %(funcName) "
        "-35s %(lineno) -5d: %(message)s"
    )
    logging.basicConfig(level=logging.INFO, format=log_format)
    logging.getLogger("pika").setLevel(logging.WARNING)
    try:
        main()
    except KeyboardInterrupt:
        pass