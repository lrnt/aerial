from asyncio import coroutine, sleep, wait_for, shield, Semaphore, TimeoutError
from aiohttp import request
from xml.etree import ElementTree

sem = Semaphore(40)

@coroutine
def get(*args, **kwargs):
    with (yield from sem):
        response = yield from request('GET', *args, **kwargs)
    return (yield from response.text())

@coroutine
def get_etree(xpath, url, **kwargs):
    body = yield from get(url, **kwargs)
    return ElementTree.fromstring(body).findall(xpath)

@coroutine
def run_periodically(coro, delay, timeout, *args, **kwargs):
    while True:
        try:
            yield from wait_for(coro(*args, **kwargs), timeout)
        except TimeoutError:
            pass

        yield from sleep(delay)
