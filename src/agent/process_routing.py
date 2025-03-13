import logging
from uuid import uuid4
from aiohttp import ClientConnectionError, ClientConnectionResetError, web
import json
import time
import asyncio

logger = logging.getLogger(__name__)


def decode_message(string_message):
    try:
        message = json.loads(string_message)
        message_type = message.pop("type", None)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON message: {e}")
    
    if message_type == "request":
        return "request", message
    elif message_type == "response":
        return "response", message
    elif message_type == "event":
        return "notification", message
    else:
        raise ValueError(f"Invalid message type: {message_type}")
    
    
def encode_message(message_type, **kwargs):
    kwargs.update({"type": message_type})
    return json.dumps(kwargs)


class Client:
    def __init__(self, web_socket, handle_request_callback):
        self.id = uuid4().hex
        self.handle_request_callback = handle_request_callback
        self.ws = web_socket
        self.sent_messages = 0
        self.received_messages = 0
        
    async def handle_request(self, service, method, params, context=None, correlation_id=None):
        """ Handles a request message and returns the result or error.

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
        if correlation_id and "correlation_id" not in context:
            context["correlation_id"] = correlation_id
        correlation_id = context["correlation_id"]
        logger.debug(f"Received request {correlation_id}, {service}.{method}")
        try:
            reply_msg = await self.handle_request_callback(service, method, params, context, correlation_id)            
        except Exception as e:
            error = f"Error handling request: {e}"
            logger.exception(error)
            reply_msg = {
                "correlation_id": correlation_id, 
                "resut": None, 
                "error": error,
                "taken": time.time() - message_received_at
            }
        finally:
            logger.debug(f"Respond to request {correlation_id}, {service}.{method}")
            await self.send("response", **reply_msg)
            
    async def send(self, msg_type, **msg):
        try:
            encoded_message = encode_message(msg_type, **msg)
            await self.ws.send_str(encoded_message)
        except ClientConnectionResetError as e:
            logger.warning(f"Client {self.id} has closed and will not received the latest message")
        except ClientConnectionError as e:
            logger.exception(f"ClientConnectionError: {e}")
        except Exception as e:
            logger.exception(f"Runtime error sending message: {e}")
        finally:
            self.sent_messages += 1

    async def handle_message(self, message):
        """ Handles incoming websocket messages. Only request messages and errors are responded to.
        """
        # logger.debug(f"Received message")
        self.received_messages += 1
        
        message_received_at = time.time()
        msg_type = None
        msg = {}
        result = None
        error = None
        
        try:
            msg_type, msg = decode_message(message)
        except ValueError as e:
            logger.error(f"Message payload: {message}")
            logger.error(f"Message decode error: {e}")
            error=str(e)
        except Exception as e:
            logger.error(f"Message payload: {message}")
            logger.exception(f"Runtime error decoding message: {e}")            
            error=str(e)

        context = msg.get("context")
        context = context if context else {}
        correlation_id = msg.get("correlation_id")
        if correlation_id and "correlation_id" not in context:
            context["correlation_id"] = correlation_id            
        
        reply_msg = {
            "correlation_id": correlation_id,
            "error": error,
            "result": result,
            "taken": time.time() - message_received_at,
        }
            
        if error:
            await self.send("response", **reply_msg)
            
        elif msg_type == "request":
            try:
                asyncio.create_task(self.handle_request(**msg))
                await asyncio.sleep(0)
            except Exception as e:
                logger.exception(f"Error handling request: {e}")
                reply_msg = {
                    "correlation_id": correlation_id,
                    "result": result,
                    "error": str(e), 
                    "taken": time.time() - message_received_at
                }
                await self.send("response", **reply_msg)
            
        else:
            logger.warning(f"Ignoring message type: {msg_type} {msg}")

    async def close(self):
        await self.ws.close()
