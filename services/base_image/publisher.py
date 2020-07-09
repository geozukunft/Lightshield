"""The Publisher is the output segment of the service. It will broadcast all packages processed.

Each service can limit the output by providing a list of Required Subscriber. As long as not all
required subscriber are connected the publisher will pause.
Additional service can connect without halting/interrupting the publishing (e.g. logging systems).
"""
import os
import logging
import asyncio
import threading
import aioredis
import websockets


class Publisher(threading.Thread):
    """Threaded class that handles the sending of local requests to downstream services.

    Data is taken from an outgoing buffer and broadcasted.
    """

    def __init__(self, host='0.0.0.0', port=9999):
        """Initiate logging and global variables."""
        super().__init__()
        self.required_subs = None
        if 'REQUIRED_SUBSCRIBER' in os.environ and os.environ['REQUIRED_SUBSCRIBER'] != '':
            self.required_subs = os.environ['REQUIRED_SUBSCRIBER'].split(',')

        self.connection_params = [host, port]
        self.stopped = False
        self.redisc = None

        self.clients = set()
        self.client_names = {}

        self.logging = logging.getLogger("Publisher")
        self.logging.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter('%(asctime)s [Publisher] %(message)s'))
        self.logging.addHandler(handler)

    def stop(self) -> None:
        """Initiate shutdown."""
        self.logging.info("Received shutdown signal. Shutting down.")
        self.stopped = True

    def run(self) -> None:
        """Initiate the async loop/websocket server."""
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.async_run())

    async def init(self) -> None:
        """Initiate redis connection."""
        self.redisc = await aioredis.create_redis_pool(
            ('redis', 6379), db=0, encoding='utf-8')

    async def async_run(self) -> None:
        """Run async initiation and start websocket server."""
        await self.init()
        await asyncio.gather(
            self.worker(),
            websockets.serve(self.server, *self.connection_params))

    async def worker(self) -> None:
        """Handle sending out tasks to clients."""
        while not self.stopped:
            if self.required_subs:
                if missing := [item for item in self.required_subs if
                               item not in self.client_names.keys()]:
                    self.logging.info("Following required subs still missing: %s", missing)
                    while [item for item in self.required_subs if
                           item not in self.client_names.keys()]:
                        await asyncio.sleep(1)
                    self.logging.info("Connection to all required subs established.")
                    continue

            if not all([self.client_names[name] for name in self.client_names]):
                while not all([self.client_names[name] for name in self.client_names]):
                    await asyncio.sleep(0.2)
                continue
            if (task := await self.redisc.lpop('packages')) and self.clients:
                await asyncio.wait([client.send(task) for client in self.clients])

    async def server(self, websocket, _) -> None:
        """Handle the websocket client connection."""
        self.clients.add(websocket)
        client_name = None
        try:
            msg = await websocket.recv()
            if not msg.startswith('ACK'):
                return
            client_name = msg.split("_")[1]
            self.client_names[client_name] = True
            async for message in websocket:
                if message == "PAUSE":
                    self.client_names[client_name] = False
                if message == "UNPAUSE":
                    self.client_names[client_name] = True
        except Exception as err:  # pylint: disable=broad-except
            self.logging.info("Websocket lost connection. [%s]", err.__class__.__name__)
        finally:
            del self.client_names[client_name]
            self.clients.remove(websocket)