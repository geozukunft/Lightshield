"""Summoner ID Updater  - Logical elements.

No Service defined as the service is exactly the same as the default case.
Import is done directly.
"""
import asyncio
import logging
import os
import traceback
from datetime import datetime, timedelta

import aiohttp
import aioredis
import asyncpg
from exceptions import RatelimitException, NotFoundException, Non200Exception


class Service:
    """Core service worker object."""

    def __init__(self):
        """Initiate sync elements on creation."""
        self.logging = logging.getLogger("SummonerIDs")
        level = logging.INFO
        if "LOGGING" in os.environ:
            level = getattr(logging, os.environ['LOGGING'])
        self.logging.setLevel(level)
        handler = logging.StreamHandler()
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter('%(asctime)s [SummonerIDs] %(message)s'))
        self.logging.addHandler(handler)

        self.server = os.environ['SERVER']
        self.url = f"http://{self.server.lower()}.api.riotgames.com/lol/" + \
                   "summoner/v4/summoners/%s"
        self.stopped = False
        self.retry_after = datetime.now()

        self.completed_tasks = []

    def shutdown(self):
        """Called on shutdown init."""
        self.stopped = True

    async def flush_manager(self):
        """Update entries in postgres once enough tasks are done."""
        self.logging.info("Started flush manager.")
        try:
            conn = await asyncpg.connect("postgresql://postgres@postgres/raw")
            while not self.stopped:
                if (size := len(self.completed_tasks)) >= 50:
                    await conn.executemany('''
                        UPDATE summoner
                        SET account_id = $1, puuid = $2
                        WHERE summoner_id = $3;
                        ''', self.completed_tasks)
                    self.completed_tasks = []
                    self.logging.info("Inserted %s summoner IDs.", size)
                await asyncio.sleep(0.5)
            await conn.close()
        except Exception as err:
            traceback.print_tb(err.__traceback__)
            self.logging.info(err)
        self.logging.info("Stopped flush manager.")

    async def get_task(self):
        """Return tasks to the async worker."""
        while not (task := await self.redis.spop('tasks')) or self.stopped:
            await asyncio.sleep(0.5)
        if self.stopped:
            return
        start = datetime.utcnow().timestamp()
        await self.redis.zadd('in_progress', start, task)
        return task

    async def async_worker(self):
        """Create only a new call if the summoner is not yet in the db."""
        self.logging.info("Started worker.")
        try:
            while not self.stopped:
                summoner_id = await self.get_task()
                if self.stopped:
                    return
                url = self.url % summoner_id
                try:
                    async with aiohttp.ClientSession() as session:
                        response = await self.fetch(session, url)
                        self.completed_tasks.append(
                            (response['accountId'], response['puuid'], summoner_id))
                except (RatelimitException, NotFoundException, Non200Exception):
                    continue
                if datetime.now() < self.retry_after:
                    delay = max(0.5, (self.retry_after - datetime.now()).total_seconds())
                    await asyncio.sleep(delay)
        except Exception as err:
            traceback.print_tb(err.__traceback__)
            self.logging.info(err)
        self.logging.info("Stopped worker.")

    async def fetch(self, session, url):
        """Execute call to external target using the proxy server.

        Receives aiohttp session as well as url to be called. Executes the request and returns
        either the content of the response as json or raises an exeption depending on response.
        :param session: The aiohttp Clientsession used to execute the call.
        :param url: String url ready to be requested.

        :returns: Request response as dict.

        :raises RatelimitException: on 429 or 430 HTTP Code.
        :raises NotFoundException: on 404 HTTP Code.
        :raises Non200Exception: on any other non 200 HTTP Code.
        """
        try:
            async with session.get(url, proxy="http://lightshield_proxy_%s:8000" % self.server.lower()) as response:
                await response.text()
                self.logging.info(response.status)
        except aiohttp.ClientConnectionError:
            raise Non200Exception()
        if response.status in [429, 430]:
            if "Retry-After" in response.headers:
                delay = int(response.headers['Retry-After'])
                self.retry_after = datetime.now() + timedelta(seconds=delay)
            raise RatelimitException()
        if response.status == 404:
            raise NotFoundException()
        if response.status != 200:
            raise Non200Exception()
        return await response.json(content_type=None)

    async def init(self):
        """Override of the default init function.

        Initiate the Rankmanager object.
        """
        self.redis = await aioredis.create_redis_pool(
            ('redis', 6379), encoding='utf-8')

    async def run(self):
        """Runner."""
        await self.init()
        flush_manager = asyncio.create_task(self.flush_manager())
        await asyncio.gather(*[
            asyncio.create_task(self.async_worker()) for _ in range(10)
        ])
        await flush_manager
