import asyncio
import json
import logging

from marshmallow.exceptions import ValidationError
import websockets

from constants import Target
from handlers.users import Users
from schema import WebSocketMessage


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('game_server')


class GameServer:
    def __init__(self, host, port, session=None):
        self.host = host
        self.port = port
        self.ws_server = None
        self.sync_queue = asyncio.Queue()
        self.session = session

        self.users = Users(session, self.sync_queue)
        self.chats = None

    @property
    def clients(self):
        if self.ws_server:
            return self.ws_server.websockets

    async def sync_worker(self):
        while True:
            sync_message = json.dumps(await self.sync_queue.get())

            if self.clients:
                await asyncio.wait([ws.send(sync_message) for ws in self.clients])
                logger.info(f"Sent sync message: {sync_message}")

            self.sync_queue.task_done()

    async def consume_message(self, ws: websockets.WebSocketServerProtocol, message: str):
        try:
            data = WebSocketMessage().loads(message)

            target = data.pop('target')
            action = data.pop('action')

            if target == Target.USER.value:
                await self.users.handle(ws, action, data)
            else:
                logger.info("Message ignored: %s", message)

        except json.decoder.JSONDecodeError as err:
            errmsg = '%s: line %d column %d (char %d)' % (err.msg, err.lineno, err.colno, err.pos)
            message = {"status": "failure", "errors": errmsg}
            return await ws.send(json.dumps(message))

        except ValidationError as err:
            message = {"status": "failure", "errors": err.messages}
            return await ws.send(json.dumps(message))

    async def online(self, ws):
        messages = []
        if self.users.online:
            users_online = json.dumps({"target": "users", "data": self.users.online})
            messages.append(ws.send(users_online))

        if messages:
            await asyncio.wait(messages)

    async def offline(self, ws):
        if ws in self.users.users:
            user = self.users.users.pop(ws)
            message = {"target": "sync", "action": "user_offline", "data": user.email}
            self.sync_queue.put_nowait(message)

    async def run(self, ws: websockets.WebSocketServerProtocol, path: str):
        await self.online(ws)
        try:
            async for message in ws:
                logger.info(f"Message received: {message}")
                await self.consume_message(ws, message)
        except websockets.WebSocketException:
            pass
        finally:
            await self.offline(ws)

    def serve(self):
        server = websockets.serve(self.run, self.host, self.port)
        self.ws_server = server.ws_server
        asyncio.get_event_loop().run_until_complete(server)
        asyncio.get_event_loop().run_until_complete(self.sync_worker())
        asyncio.get_event_loop().run_forever()
