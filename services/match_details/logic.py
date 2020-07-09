"""Match History updater. Pulls matchlists for all player."""
from exceptions import RatelimitException, NotFoundException, Non200Exception
from worker import WorkerClass
import json

class Worker(WorkerClass):

    async def process_task(self, session, content):

        identifier = content['match_id']
        if await self.marker.execute_read(
                'EXISTS(SELECT * FROM match_id WHERE id = %s);' % identifier):
            return
        if await self.service.redisc.sismember('matches', str(identifier)):
            await self.marker.execute_write(
                'INSERT INTO match_id (id) VALUES (%s);' % identifier)
            await self.service.redisc.srem('matches', str(identifier))
            return

        if identifier in self.service.buffered_elements:
            return
        self.service.buffered_elements[identifier] = True
        url = self.service.url % identifier
        try:
            response = await self.service.fetch(session, url)
            await self.marker.execute_write(
                'INSERT INTO match_id (id) VALUES (%s);' % identifier)
            await self.service.add_package(response)
        except (RatelimitException, Non200Exception):
            await self.service.redisc.lpush('tasks', json.dumps(content))  # Return to task queue
        except NotFoundException:
            pass
        finally:
            del self.service.buffered_elements[identifier]
