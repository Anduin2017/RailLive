"""Report browser frames and distinct displayed G1211 positions for four seconds at 32×."""
import asyncio
import sys
from browser_driver import drive


async def measure(ev, wait):
    await ev("""window.startTime=liveTimeline.midnight('2026-11-19')+46920000;
        window.head=()=>map.getSource('region-trains')?._data.features.find(f=>f.properties.number==='G1211');
        liveTimeline.jump(startTime);
        void map.jumpTo({center:toMapCoordinates([120.45,31.4]),zoom:10});""")
    await wait("head() && map.isStyleLoaded()")
    await ev("""void map.jumpTo({center:head().geometry.coordinates,zoom:10});
        liveTimeline.replay(startTime);
        const speed=document.querySelector('#live-speed');
        speed.value='32';speed.dispatchEvent(new Event('change'));""")
    print(await ev("""new Promise(resolve=>{
        const start=performance.now(), positions=new Set();
        let frames=0, renders=0;
        function sampleMap(){
          renders++;
          const train=map.queryRenderedFeatures({layers:['region-heading']})
            .find(f=>f.properties.number==='G1211');
          if(train)positions.add(String(train.geometry.coordinates));
        }
        function sampleFrame(stamp){
          frames++;
          if(stamp-start<4000)requestAnimationFrame(sampleFrame);
          else {
            map.off('render',sampleMap);
            const seconds=(stamp-start)/1000;
            resolve({browserFramesPerSecond:frames/seconds,mapDrawsPerSecond:renders/seconds,
              distinctHeadPositionsPerSecond:positions.size/seconds});
          }
        }
        map.on('render',sampleMap);requestAnimationFrame(sampleFrame);
    })"""))


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],measure))
