import asyncio, json, utils
from asyncio_redis import Pool
from aiohttp import web
from os import path

from mivb import Model, Operator, Line, Route, RoutePresent, Stop

HOME_FILE = path.join(path.dirname(__file__), 'index.html')
STATIC_PATH = path.join(path.dirname(__file__), 'static')

class Aerial:
    def __init__(self, loop, address='127.0.0.1', port=8080):
        self.loop = loop
        self.address = address
        self.port = port
        self.sockets = []
        self.app = web.Application(loop=self.loop)
        self.app.router.add_static('/static/', STATIC_PATH)
        self.app.router.add_route('GET', '/', self.homehandler)
        self.app.router.add_route('GET', '/lines/', self.lineshandler)
        self.app.router.add_route('GET', '/present/', self.presenthandler)
        self.app.router.add_route('GET', '/socket/', self.sockethandler)
        self.srv_handler = self.app.make_handler()

    @asyncio.coroutine
    def run(self):
        self.srv = yield from self.loop.create_server(self.srv_handler,
                                                      self.address, self.port)
        self.redis = yield from Pool.create(poolsize=100)
        Model.redis = self.redis
        self.mivb = Operator()

        for line in (yield from self.mivb):
            for route in (yield from line):
                # TODO: Gracefully cancel tasks when killing the serever
                asyncio.async(utils.run_periodically(route.update, 5, 5))

        print('Running server on http://%s:%s' % (self.address, self.port))

    @asyncio.coroutine
    def finish(self):
        yield from self.srv_handler.finish_connections()
        self.srv.close()
        yield from self.srv.wait_closed()

        Model.redis.close()
        yield # Hack to avoid bug in asyncio_redis

    @asyncio.coroutine
    def homehandler(self, request):
        with open(HOME_FILE, 'rb') as f:
            return web.Response(body=f.read(), content_type='text/html')

    @asyncio.coroutine
    def lineshandler(self, request):
        lines_list = [((yield from line.get('id')), (yield from line.getall()))
                      for line in (yield from self.mivb)]
        lines_dict = dict((yield from lines_list))
        body = json.dumps(lines_dict).encode('utf-8')
        return web.Response(body=body, content_type='application/javascript')

    @asyncio.coroutine
    def presenthandler(self, request):
        present = []

        for line in (yield from self.mivb):
            for route in (yield from line):
                for stop in (yield from RoutePresent(route)):
                    present.append({'line': (yield from line.get('id')),
                                    'route': (yield from route.getall()),
                                    'stop': (yield from stop.getall())})

        body = json.dumps(present).encode('utf-8')

        return web.Response(body=body, content_type='application/javascript')

    @asyncio.coroutine
    def sockethandler(self, request):
        response = web.WebSocketResponse()
        ok, protocol = response.can_start(request)

        if not ok:
            return web.Response(status=404)

        response.start(request)
        self.sockets.append(response)

        subscriber = yield from self.redis.start_subscribe()
        yield from subscriber.subscribe(['mivb'])

        while True:
            reply = yield from subscriber.next_published()
            response.send_str(reply.value)

        return response

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    aerial = Aerial(loop)
    loop.run_until_complete(aerial.run())

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(aerial.finish())
