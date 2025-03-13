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

async def sim_client(requests_to_send, request_delay, stw_delay, service, method, uri="ws://localhost:8080/ws"):
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
    connection_timed_out = False
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(uri) as websocket:
                
                async def send_request():
                    nonlocal request_count
                    request_count += 1
                    
                    start_time = time.time()
                    correlation_id = uuid4().hex
                    request_message = {
                        "type": "request",
                        "context": {
                            "correlation_id": correlation_id,
                            "request_delay": request_delay,
                            "stw_delay": stw_delay},
                        "service": service,
                        "method": method,
                        "params": {"sim_client_id": sim_client_id, "pid": os.getpid()}
                    }
                    request_queue[correlation_id] = request_message
                    await websocket.send_json(request_message)
                    request_message["start_time"] = start_time
                    logger.debug(f"Sent request {request_count}: {correlation_id}")
                
                if request_count < requests_to_send:
                    await send_request()
                if request_count < requests_to_send:
                    await send_request()
        
                async for msg in websocket:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        message = msg.json()
                        logger.debug(f"Received: {message.get('type')}")
                        if message["type"] == "response":
                            response_count += 1
                            correlation_id = message["correlation_id"]
                            request = request_queue.pop(correlation_id, None)
                            logger.debug(f"Response received for {correlation_id}")
                            
                            # logger.debug(f"Request: {request}")
                            if request:
                                end_time = time.time()
                                if message.get("error"):
                                    logger.error(f"Error response message: {correlation_id} - {message}")
                                session_stats["requests"].append({
                                    "start_time": request["start_time"],
                                    "end_time": end_time,
                                    "method": method,
                                    "taken": end_time - request["start_time"],
                                })
                            else:
                                logger.warning(f"Ignoring response message: {message}")
                                break

                    if request_count < requests_to_send:
                        await send_request()
                    
                    if response_count >= requests_to_send and len(request_queue) == 0:
                        break
                    
    except asyncio.exceptions.TimeoutError as e:
        connection_timed_out = True
        timedout_after = time.time() - session_stats["start_time"]
        logger.error(f"Connection timedout after {timedout_after} s: {e}")
        
    finally:  
        if request_count != response_count:
            logger.warning(f"{response_count} responses of {request_count} requests received, {requests_to_send} was required")
            
        logger.debug("request_count: %s, response_count: %s", request_count, response_count)
        
    session_stats["timedpout"] = connection_timed_out
    session_stats["timedout_after"] = timedout_after
    session_stats["requests_to_send"] = requests_to_send
    session_stats["request_count"] = request_count
    session_stats["response_count"] = response_count
    session_stats["end_time"] = time.time()
    session_stats["taken"] = session_stats["end_time"] - session_stats["start_time"]
    session_stats["average"] =  len(session_stats["requests"]) / session_stats["taken"]
    return session_stats


async def _mutli_aio_sim_clients(client_count,  requests_to_send, request_delay, stw_delay, service, method):
    tasks = [sim_client(requests_to_send, request_delay, stw_delay, service, method) for _ in range(client_count)]
    result = await asyncio.gather(*tasks)
    return result


def mutli_aio_sim_clients(client_count, requests_to_send, request_delay, stw_delay, service, method, index=0):
    global logger
    logger = logging.getLogger(__name__)
    start_time = time.time()
    
    # Set up logging for the client simulator instance as it is called from a different process
    logging.basicConfig(level=logger_level, format=log_format)
    
    client_requests = asyncio.run(_mutli_aio_sim_clients(
        client_count=client_count, 
        requests_to_send=requests_to_send,
        request_delay=request_delay,
        stw_delay=stw_delay,
        service=service,
        method=method))
        
    return {
        "index": index,
        "client_requests": client_requests,
        "taken": time.time() - start_time,
    }


def main():
    logger.info("Initilising client test simulator instance")
    
    pool_size = 1
    async_clients = 50
    request_service = "inprocess"
    request_method = "small"
    requests_to_send = 10
    request_delay = 0
    stw_delay = 60 * 5
    f = functools.partial(
        mutli_aio_sim_clients, 
        async_clients, 
        requests_to_send, 
        request_delay, 
        stw_delay, 
        request_service, 
        request_method)
    
    
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

        