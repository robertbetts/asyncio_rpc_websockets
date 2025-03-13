import logging
import signal
import os
import sys
import subprocess
import asyncio
import time

from aiohttp import web
from nuropb.rmq_api import RMQAPI

from agent.process_routing import Client


logger = logging.getLogger(__name__)


class WebSocketApp:
    def __init__(self):
        app = web.Application()
        app.add_routes([web.get("/ws", self.websocket_handler)])
        app["clients"] = {}
        app["max_concurrency"] = 0
        self.app = app

    @property
    def pid(self):
        return os.getpid()
        
    async def handle_request(self, service, method, params, context=None, correlation_id=None):
        """_summary_

        Args:
            service (str): tareget serivce name
            method (str): target method name
            params (dict): parameters to pass to the service method
            context (dict, optional): Defaults to None.
            correlation_id (str, optional): Defaults to None.

        Returns:
            dict: {correlation_id, result, error, taken}
        """
        message_received_at = time.time()
        context = context if context else {}
        if correlation_id and "correlation_id" not in context:
            context["correlation_id"] = correlation_id
        correlation_id = context["correlation_id"]
        # logger.debug(f"Received request {correlation_id}, {service}.{method}")
        try:
            if service == "inprocess" or "nuropb" not in self.app:
                request_delay = context.get("request_delay", 0)
                if request_delay > 0:
                    await asyncio.sleep(request_delay)
                stw_delay = context.get("stw_delay", 0)
                if stw_delay > 0:
                    logger.debug(f"Stopping the world for {stw_delay} seconds")
                    time.sleep(stw_delay)
                response = {
                    "correlation_id": correlation_id,
                    "result": method,
                    "error": None,
                    "taken": time.time() - message_received_at
                }                
            else:
                result = await self.app['nuropb'].request(service, method, params, context)
                response = {
                    "correlation_id": correlation_id,
                    "result": result,
                    "error": None,
                    "taken": time.time() - message_received_at
                }

        except Exception as e:
            error = f"Runtime error handling request {service}.{method}: {e}"
            logger.exception(error)
            response = {
                "correlation_id": correlation_id,
                "result": None,
                "error": error,
                "taken": time.time() - message_received_at
            }
        return response

    async def listen(self, client, websocket):
        while True:
            msg = await websocket.receive()
            
            if msg.type is web.WSMsgType.TEXT:
                await client.handle_message(msg.data.strip())
            elif msg.type is web.WSMsgType.BINARY:
                logger.warning("Binary message received")
                logger.debug("Binary message payload: ", msg.data)
            elif msg.type is web.WSMsgType.PING:
                await websocket.pong()
            elif msg.type is web.WSMsgType.PONG:
                logger.debug("Pong received")
            else:
                if msg.type is web.WSMsgType.CLOSE:
                    logger.debug("Websocket closing")
                    await websocket.close()
                elif msg.type is web.WSMsgType.ERROR:
                    logger.error("Error during receive %s" % self.ws.exception())
                elif msg.type is web.WSMsgType.CLOSED:
                    logger.debug("Websocket closed")
                    pass
                
                logger.debug("Breaking out of listen loop")
                break
                        
    async def close(self):
        await self.ws.close()

    async def websocket_handler(self, request):
        # Prepare the websocket connection
        ws = web.WebSocketResponse()
        try:
            logger.debug(f"Preparing websocket connection: {request.remote}")
            await ws.prepare(request)

            clients = self.app["clients"]
            client = Client(ws, handle_request_callback=self.handle_request)
            clients[client.id] = client
            logger.debug(
                f"Connection opened: {request.remote}: {client.id} for pid {self.pid}"
            )
            if self.app["max_concurrency"] < len(clients):
                self.app["max_concurrency"] = len(clients)
                logger.info(
                    f"Highest number of concurrent clients {len(clients)} for pid {self.pid}"
                )
        except Exception as e:
            logger.exception(
                f"Runtime error preparing websocket connection: {request.remote}"
                f"current connections for {self.pid}"
            )
            return ws
        
        # Handle the websocket connection messages
        try:
            await self.listen(client, ws)
        except Exception as e:
            logger.exception(
                f"Runtime error while handling websocket connection: {request.remote}: {client.id}: {e}"
            )
        
        logger.debug(f"Client {client.id} received {client.received_messages} messages before closing")
        logger.debug(f"Client {client.id} sent {client.sent_messages} messages before closing")


        # Client clean up
        clients.pop(client.id)
        logger.debug(
            f"Connection closed: {request.remote}: {client.id} for pid {self.pid}"
        )
            
        return ws

        
async def init_nuropb(app):
    amqp_url = "amqp://guest:guest@localhost:5673/nuropb-example"
    nuropb_client = RMQAPI(
        amqp_url=amqp_url,
    )
    await nuropb_client.connect()
    app['nuropb']  = nuropb_client
    logger.info(f"Process {os.getpid()} nuropb rmqapi initialised")
    
    
def create_app():
    websocket_app = WebSocketApp()
    websocket_app.app.on_startup.append(init_nuropb)
    return websocket_app.app


class ProcessRunTime:
    def __init__(self):
        self.shutdown_future = None
        self.ws_bind_host = "localhost"
        self.ws_bind_port = 8080        
        self.app = create_app()

    @property
    def pid(self):
        return os.getpid()

    def handle_signal(self, sig):
        logger.info(f"Runtime signal received: {sig.name}")
        asyncio.create_task(self.shutdown_agent())
        
    def restart_agent(self):
        logger.info("Restarting process")
        process_args = sys.argv[:]
        
        subprocess.Popen(
            args=[sys.executable, *process_args],
            start_new_session=True,
            stdout=sys.stdout,  # Redirect new process stdout to the same output discriptor
            stderr=sys.stderr   # Redirect new process stdout to the same output discriptor
            )

    async def shutdown_agent(self):
        logger.info(f"Shutting down process {self.pid}")
        try:
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]    
            self.shutdown_future = asyncio.Future()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.shutdown_future.set_result(None)
            
        except asyncio.CancelledError as e:
            logger.error(f"asyncio.CancelledError while cancelling tasks: {e}")
            
        except Exception as e:
            logger.error(f"Runtime exception while cancelling tasks: {e}")        
            
        finally:
            logger.debug("All asyncio tasks are now completed or cancelled.")
            
    async def waiting_for_shutdown(self):
        if self.shutdown_future is not None:
            await self.shutdown_future
            
    async def async_run(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.ws_bind_host, self.ws_bind_port)
        await site.start()
        logger.info(f"Process {self.pid} listening on ws://{self.ws_bind_host}:{self.ws_bind_port}/ws")    
        await asyncio.Future()  # Run forever
    
    def run(self):
        logger.info(f"Initialising process {self.pid}")
        loop = asyncio.get_event_loop()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda sig=sig: self.handle_signal(sig))
            loop.run_until_complete(self.async_run())
        
        except asyncio.CancelledError as e:
            logger.error(f"asyncio.CancelledError while process {self.pid} is running: {e}")
        
        except Exception as e:
            logger.exception(f"Runtime exception while process {self.pid} is running: {e}") 
        
        finally:
            loop.run_until_complete(self.waiting_for_shutdown())
            logger.info(f"Process has shut down {self.pid}.")
        