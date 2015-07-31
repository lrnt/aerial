import inspect, asyncio, sys, json
from aiohttp import web
from utils import get_etree
from asyncio_redis import Connection
from collections import Iterable

API_BASE_URL = 'http://m.mivb.be/api/'
API_DEFAULT_PARAMS = {'lang': 'nl'}
API_LINES_URL = API_BASE_URL + 'getlinesnew.php'
API_ROUTE_URL = API_BASE_URL + 'getitinerary.php'
API_STOP_URL = API_BASE_URL + 'getwaitingtimes.php'

def objectify(keys):
    # Get all the model types in this module
    types = dict(inspect.getmembers(sys.modules[__name__], inspect.isclass))

    # Split the keys into typename, name
    keys = [x.split(':', 1) for x in keys]

    # Lookup and instantiate each object
    objects = [types[typename](id) for typename, id in keys]
    return objects

class Model(object):
    _redis = None

    @classmethod
    @property
    def redis(cls):
        return cls._redis

    def __init__(self, id):
        self.id = id
        self.key = '%s:%s' % (self.__class__.__name__, self.id)

    def delete(self):
        self.redis.delete(self.key)

    def __hash__(self):
        return hash(self.key)

    def __eq__(self, other):
        return (isinstance(other, self.__class__)
            and self.key == other.key)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return '<%s>' % self.key

class DictModel(Model):
    def __init__(self, id):
        Model.__init__(self, id)
        self.dictkey = '%s:dict' % self.key

    @asyncio.coroutine
    def get(self, key):
        return (yield from self.redis.hget(self.dictkey, key))

    @asyncio.coroutine
    def set(self, key, value):
        return (yield from self.redis.hset(self.dictkey, key, value))

    @asyncio.coroutine
    def getall(self):
        return (yield from self.redis.hgetall_asdict(self.dictkey))

class SetModel(Model):
    def __init__(self, id):
        Model.__init__(self, id)
        self.setkey = '%s:set' % self.key

    @asyncio.coroutine
    def sadd(self, obj):
        if isinstance(obj, Iterable):
            objs = [i.key for i in obj]
        else:
            objs = [obj.key]

        return (yield from self.redis.sadd(self.setkey, objs))

    @asyncio.coroutine
    def srem(self, obj):
        if isinstance(obj, Iterable):
            objs = [i.key for i in obj]
        else:
            objs = [obj.key]

        return (yield from self.redis.srem(self.setkey, objs))

    @asyncio.coroutine
    def __iter__(self):
        return objectify((yield from self.redis.smembers_asset(self.setkey)))

class SortedSetModel(Model):
    def __init__(self, id):
        Model.__init__(self, id)
        self.zsetkey = '%s:zset' % self.key

    @asyncio.coroutine
    def zadd(self, obj, score):
        return (yield from self.redis.zadd(self.zsetkey, {obj.key: score}))

    @asyncio.coroutine
    def zrem(self, obj):
        return (yield from self.redis.zrem(self.zsetkey, [obj.key]))

    @asyncio.coroutine
    def __iter__(self):
        dct = yield from self.redis.zrange_asdict(self.zsetkey)
        lst = sorted(dct, key=dct.__getitem__)
        return objectify(lst)

class Operator(SetModel):
    def __init__(self):
        SetModel.__init__(self, 'MIVB')

    @asyncio.coroutine
    def update_lines(self):
        nodes = yield from get_etree('line', API_LINES_URL,
                                      params=API_DEFAULT_PARAMS)

        for node in nodes:
            line = Line(node.find('id').text)

            for child in node:
                if child.text:
                    yield from line.set(child.tag, child.text)

            yield from self.sadd(line)

class Line(DictModel, SetModel):
    def __init__(self, id):
        DictModel.__init__(self, id)
        SetModel.__init__(self, id)

    @asyncio.coroutine
    def update_routes(self):
        for iti in range(1, 3): # There are only 2 routes (1 and 2) in each line
            route = Route('%s.%s' % (self.id, iti))
            direction = yield from self.get('destination%s' % iti)

            yield from route.set('destination', direction)
            yield from route.set('line', self.id)
            yield from route.set('iti', str(iti))
            yield from route.update(full_update=True)
            yield from self.sadd(route)

class Route(DictModel, SortedSetModel):
    def __init__(self, id):
        DictModel.__init__(self, id)
        SortedSetModel.__init__(self, id)

    @asyncio.coroutine
    def _report_change(self, origin, destination):
        origin = {'id': (origin.id if origin else '-1'),
                  'lat': (yield from origin.get('latitude') if origin else ''),
                  'lon': (yield from origin.get('longitude') if origin else '')}

        destination = {'id': (destination.id if destination else '-1'),
                       'lat': \
            (yield from destination.get('latitude') if destination else ''),
                       'lon': \
            (yield from destination.get('longitude') if destination else '')}

        message = {'route': self.id,
                   'origin': origin,
                   'destination': destination}

        yield from self.redis.publish('mivb', json.dumps(message))

    @asyncio.coroutine
    def update(self, full_update=False):
        params = {'line': (yield from self.get('line')),
                  'iti': (yield from self.get('iti'))}
        params.update(API_DEFAULT_PARAMS)
        nodes = yield from get_etree('stop', API_ROUTE_URL, params=params)
        route_present = RoutePresent(self)

        old = set((yield from route_present))
        new = set()

        for score, node in enumerate(nodes):
            stop = Stop(node.find('id').text)
            present = node.find('present')

            if present is not None and present.text == 'TRUE':
                new.add(stop)

            if full_update:
                for child in node:
                    if child.text:
                        yield from stop.set(child.tag, child.text)

                yield from self.zadd(stop, score)

        rem = old - new
        add = new - old

        if len(rem) > 0:
            yield from route_present.srem(rem)

        if len(add) > 0:
            yield from route_present.sadd(add)

        stops = yield from self

        o_i = len(stops)

        for s_i, s in reversed(list(enumerate(stops))):
            if not s in new:
                if o_i > s_i:
                    o_i -= 1

                if s in old:
                    for n in reversed(stops[s_i:]):
                        if n in new:
                            break
                    else:
                        yield from self._report_change(s, None)
                continue

            for o in reversed(stops[:o_i]):
                o_i -= 1

                if o in old:
                    if o != s:
                        yield from self._report_change(o, s)
                    break
            else:
                if o_i == 0:
                    yield from self._report_change(None, s)

class RoutePresent(SetModel):
    def __init__(self, route):
        SetModel.__init__(self, route.id)

class Stop(DictModel):
    def __init__(self, id):
        DictModel.__init__(self, id)

    @asyncio.coroutine
    def update(self):
        params = {'halt': self.id}
        params.update(API_DEFAULT_PARAMS)
        nodes = yield from get_etree('position', API_STOP_URL, params=params)

        for node in nodes:
            for child in node:
                yield from self.set(child.tag, child.text)
