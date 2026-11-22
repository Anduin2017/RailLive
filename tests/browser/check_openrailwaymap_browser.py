"""Exercise tile ownership and provider failures without contacting the public server."""
import asyncio
import sys

from browser_driver import drive


async def check(ev, wait):
    await ev("""window.ormResponses = [];
      window.ormRequests = [];
      const original = window.fetch;
      window.fetch = (url, options) => {
        if (!String(url).startsWith('https://tiles.openrailwaymap.org/')) return original(url, options);
        ormRequests.push([url,options.signal,map.isMoving()]);
        return new Promise(resolve => ormResponses.push(resolve));
      };
      blankButton.click();
      map.jumpTo({center:toMapCoordinates([120.795,31.066]),zoom:14}); true;""")
    assert await ev("ormRequests.length === 0")
    await ev("ormButton.click()")
    await wait("ormResponses.length > 0")
    assert await ev("ormResponses.length <= 3 && baseMode === 'orm'")
    await ev("""window.releaseOrmRequests = setInterval(() => {
        ormResponses.splice(0).forEach(done => done(new Response(null,{status:204})));
      },20); true;""")
    await wait("ormRequests.length >= 3 && ormResponses.length === 0")
    await ev("""window.requestsBeforePan = ormRequests.length;
      map.panBy([850,0],{duration:1400,easing:t=>t}); true;""")
    await wait("ormRequests.slice(requestsBeforePan).some(([, ,moving]) => moving)")
    await wait("!map.isMoving()")
    await ev("clearInterval(releaseOrmRequests)")
    await ev("blankButton.click(); ormResponses.splice(0).forEach(done=>done(new Response(null,{status:204})))")
    assert await ev("ormRequests.every(([,signal])=>signal.aborted) && !map.getSource('scenery')")
    await ev("ormButton.click()")
    await wait("ormResponses.length > 0")
    await ev("ormResponses.shift()(new Response(null,{status:429}))")
    await wait("baseMode === 'blank' && document.querySelector('#orm-credit').hidden")
    assert await ev("""(() => {
      const count = ormRequests.length;
      ormButton.click();
      return ormRequests.length === count && baseMode === 'blank' &&
        document.querySelector('#basemap-status').textContent.includes('限流');
    })()""")
    print('Online tiles loaded only on selection, cancellation restored blank mode and a 429 stopped new requests.')


if __name__ == '__main__':
    asyncio.run(drive(sys.argv[1], check, viewport=(393,820)))
