import asyncio,json,os,urllib.request
import websockets

async def drive(port,scenario,viewport=None):
    debug_port=int(os.environ.get('RAILS_CDP_PORT',19851))
    tab=next(tab for tab in json.load(urllib.request.urlopen(f'http://127.0.0.1:{debug_port}/json/list')) if tab.get('type')=='page')
    async with websockets.connect(tab['webSocketDebuggerUrl'],max_size=10**7) as ws:
        idx=0
        async def call(m,**p):
            nonlocal idx
            idx+=1
            await ws.send(json.dumps(dict(id=idx,method=m,params=p)))
            while True:
                msg=json.loads(await ws.recv())
                if msg.get('method')=='Runtime.exceptionThrown': raise RuntimeError(msg)
                if msg.get('id')==idx:return msg['result']
        async def ev(e):
            r=await call('Runtime.evaluate',expression=e,returnByValue=True,awaitPromise=True)
            if 'exceptionDetails' in r:raise RuntimeError(r)
            return r['result'].get('value')
        async def wait(expr):
            for _ in range(150):
                if await ev('!!('+expr+')'):return
                await asyncio.sleep(.08)
            raise AssertionError((expr,await ev('document.body.innerText.slice(-1800)')))
        await call('Network.enable')
        await call('Network.setCacheDisabled',cacheDisabled=True)
        await call('Runtime.enable')
        await call('Page.bringToFront')
        if viewport:
            await call('Emulation.setDeviceMetricsOverride',width=viewport[0],height=viewport[1],deviceScaleFactor=1,mobile=False)
        await call('Page.navigate',url='http://127.0.0.1:'+port)
        await wait("document.body?.dataset.mapReady==='true'")
        try:
            await scenario(ev,wait)
        finally:
            if viewport: await call('Emulation.clearDeviceMetricsOverride')
