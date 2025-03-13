import logging
from uuid import uuid4
import functools
import time
import asyncio
from multiprocessing import Pool
import os

import aiohttp

logger = logging.getLogger(__name__)
logger_level = logging.INFO
log_format = (
    "%(levelname).1s %(asctime)s %(name)s %(funcName)s %(lineno)d: %(message)s"
)

async def sim_client(requests_to_send, uri="ws://localhost:8080/ws"):
    sim_client_id = uuid4().hex
    session_stats = {
        "sim_client_id": sim_client_id,
        "start_time": time.time(),
        "session_id": uuid4().hex,
        "requests": [],
    }
    request_queue = {}    
    request_count = 0
    response_count = 0
    
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(uri) as websocket:
            
            async def send_request(service="inprocess", method="small", request_delay=0):
                nonlocal request_count
                request_count += 1
                
                start_time = time.time()
                correlation_id = uuid4().hex
                context = {
                    "correlation_id": correlation_id,
                    "sim_client_id": sim_client_id,
                    "request_delay": request_delay,
                }
                request_message = {
                    "type": "request",
                    "service": service,
                    "method": method,
                    "params": {"sim_client_id": sim_client_id},
                    "context": context,
                }
                request_queue[correlation_id] = request_message
                await websocket.send_json(request_message)
                request_message["start_time"] = start_time
                logger.debug(f"Sent request {request_count}: {correlation_id} {service}.{method} {request_delay}")
            
            async def send_requet_in(seconds, service="inprocess", method="small", request_delay=0):
                if seconds > 0:
                    await asyncio.sleep(seconds)
                await send_request(service, method, request_delay)
            
            if request_count < requests_to_send:
                await send_requet_in(0, "inprocess", "long_method", 3)
            if request_count < requests_to_send:
                await send_requet_in(1, "inprocess", "short_method", 1)
            if request_count < requests_to_send:
                await send_request()
            
            while True:
                msg = await websocket.receive()
                response_count += 1
                
                if msg.type is aiohttp.WSMsgType.TEXT:
                    message = msg.json()
                    # logger.debug(f"Received: {message.get('type')}")
                    if message["type"] == "response":
                        correlation_id = message["correlation_id"]
                        request = request_queue.pop(correlation_id, None)
                        
                        if request:
                            logger.debug(f"Response received for {correlation_id} {request['service']}.{request['method']}")
                        
                            end_time = time.time()
                            if message.get("error"):
                                logger.error(f"Error response message: {correlation_id} - {message}")
                                
                            session_stats["requests"].append({
                                "start_time": request["start_time"],
                                "end_time": end_time,
                                "method": request["method"],
                                "taken": end_time - request["start_time"],
                            })
                        else:
                            logger.warning(f"No request found for response message: {correlation_id}")
                                            
                elif msg.type is aiohttp.WSMsgType.BINARY:
                    logger.warning("Ignoring binary message received")
                    logger.debug("Binary message payload: ", msg.data)
                elif msg.type is aiohttp.WSMsgType.PING:
                    await websocket.pong()
                elif msg.type is aiohttp.WSMsgType.PONG:
                    logger.debug("Pong received")
                else:
                    if msg.type is aiohttp.WSMsgType.CLOSE:
                        await websocket.close()
                    elif msg.type is aiohttp.WSMsgType.ERROR:
                        logger.error("Error during receive %s" % websocket.exception())
                    elif msg.type is aiohttp.WSMsgType.CLOSED:
                        pass

                    break
            
                if requests_to_send < request_count:
                    await send_request()

                if response_count >= requests_to_send and len(request_queue) == 0:
                    break                
                # if request_count >= request_count and len(request_queue) == 0:
                #     logger.info(f"Client {sim_client_id} completed {request_count} requests")
                #     break
                
    logger.debug("request_count: %s, response_count: %s", request_count, response_count)
    session_stats["request_count"] = request_count
    session_stats["response_count"] = response_count
    session_stats["end_time"] = time.time()
    session_stats["taken"] = session_stats["end_time"] - session_stats["start_time"]
    session_stats["average"] =  len(session_stats["requests"]) / session_stats["taken"]
    return session_stats


async def _mutli_aio_sim_clients(client_count,  requests_to_send):
    tasks = [sim_client(requests_to_send) for _ in range(client_count)]
    result = await asyncio.gather(*tasks)
    return result


def mutli_aio_sim_clients(client_count, requests_to_send, index=0):
    global logger
    logger = logging.getLogger(__name__)
    start_time = time.time()
    
    # Set up logging for the client simulator instance as it is called from a different process
    logging.basicConfig(level=logger_level, format=log_format)

    client_requests = asyncio.run(_mutli_aio_sim_clients(
        client_count=client_count, 
        requests_to_send=requests_to_send))
        
    return {
        "index": index,
        "client_requests": client_requests,
        "taken": time.time() - start_time,
    }


def main():
    logger.info("Initilising client test simulator instance")
    
    pool_size = 1
    async_clients = 1
    requests_to_send = 3
    f = functools.partial(mutli_aio_sim_clients, async_clients, requests_to_send)
    
    
    all_requests = []
    
    with Pool(pool_size) as p:
        start_time = time.time()
        pool_results = p.map(f, range(pool_size))
        end_time = time.time()

        for pool_result in pool_results:
            pool_index = pool_result["index"]
            pool_taken = pool_result["taken"]
            
            pool_requests = []
            client_results = []
            for client in pool_result["client_requests"]:
                all_requests.extend(client["requests"])
                pool_requests.extend(client["requests"])
                
                client_results.append({
                    "pool_index": pool_index,
                    "client_requests": client["requests"],
                    "client_taken": client["taken"],
                    "client_average": client["average"],
                })
                                    
            client_avg = [item["client_average"] for item in client_results]
            average = sum(client_avg) / len(client_avg)
            logger.info(f"Simulator batch {pool_index}, pid {os.getpid()}, "
                        f"{len(pool_result['client_requests'])} async client sessions, "
                        f"{len(pool_requests)} requests in {pool_taken} s, "
                        f"{len(pool_requests)/pool_taken} requests/s, per client average {round(average, 2)} requests/s")


    taken = end_time - start_time
    request_count = len(all_requests)

    logger.info(f"A total of {len(client_results)} client connections across {pool_size} CPU threads")     
    logger.info(f"A total of {taken} seconds to process {request_count} requests, {request_count/taken} requests/s") 

    logger.info("Terminated client test simulator instance")

    
if __name__ == "__main__":
    logging.basicConfig(level=logger_level, format=log_format)
    try:
        main()
    except Exception as e:
        logger.exception(f"Client test simulator runtime error occurred: {e}")

        