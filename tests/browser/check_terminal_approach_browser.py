"""Replay G6721 into Shijiazhuang, including the head rendered by regional tiles."""
import asyncio
import sys

from browser_driver import drive


async def check(ev, wait):
    await ev("""(async()=>{
        const trip = await get('/api/trip/24000G672104');
        const epoch = liveTimeline.midnight('2026-11-19');
        window.seekTrain = seconds => liveTimeline.jump(epoch + seconds*1000);
        window.head = () => map.getSource('selected-train')?._data.features[0];
        seekTrain(83760);
        await showTrip(trip.id,'2026-11-19','region');
        const end = trip.stops.at(-1).mapPosition;
        window.remaining = () => {
            const [x,y] = head().geometry.coordinates;
            return Math.hypot((x-end[0])*85000,(y-end[1])*111000);
        };
    })()""")
    positions = {}
    distances = []
    for seconds in range(83760, 85080, 10):  # 23:16 departure through 23:38 arrival
        await ev(f'seekTrain({seconds})')
        await wait('head()')
        distances.append(await ev('remaining()'))
        if seconds in (83820, 84420, 84960):
            positions[seconds] = await ev('head().geometry.coordinates')
    assert 74000 < distances[0] < 84000 and distances[-1] < 450, (distances[0], distances[-1])
    assert all(d >= 500 or max(distances[i:]) < 800 for i,d in enumerate(distances))
    print('G6721 approaches the terminal once:', round(distances[0]), round(distances[-1]), flush=True)

    await ev("""clearMapSelection();
        window.regionalHead = () => map.getSource('region-trains')?._data?.features.find(
            x => !x.properties.car && x.properties.number==='G6721')""")
    for seconds, coords in positions.items():
        await ev(f"seekTrain({seconds}); void map.jumpTo({{center:{coords},zoom:10}})")
        await wait(f"regionalHead() && map.project(regionalHead().geometry.coordinates).dist(map.project({coords}))<5")
        x,y = await ev('regionalHead().geometry.coordinates')
        assert ((x-coords[0])*85000)**2 + ((y-coords[1])*111000)**2 < 150**2
    print('Regional tiles match the selected train at three instants', flush=True)


if __name__ == '__main__':
    asyncio.run(drive(sys.argv[1], check, (1400,850)))
