"""批量抓取。client.get(url) 是一个协程。"""

import asyncio


async def fetch(client, url):
    return await client.get(url)


async def fetch_all(client, urls):
    return list(await asyncio.gather(*(fetch(client, url) for url in urls), return_exceptions=True))
