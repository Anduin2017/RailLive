"""Sample rendered G3649 glyph pixels across the 11:55 window boundary in both directions."""
import asyncio,sys
from browser_driver import drive

async def check(ev,wait):
    await ev("""window.seek=s=>liveTimeline.jump(liveTimeline.midnight('2026-11-19')+1000*s);
      window.feats=()=>map.getSource('region-trains')?._data.features || [];
      window.head=()=>feats().find(f=>f.properties.number==='G3649');
      seek(42885);void map.jumpTo({center:toMapCoordinates([114.48,38]),zoom:10});""")
    await wait("head()")
    await ev("void map.jumpTo({center:head().geometry.coordinates,zoom:10});")
    await wait("map.isStyleLoaded()")
    for sign,start in [(1,42885),(-1,42930)]:
        await ev(f"""seek({start});var speedInputForTest=document.querySelector('#live-speed');speedInputForTest.value='32';speedInputForTest.dispatchEvent(new Event('change'));document.querySelector('[data-rate="{sign}"]').click()""")
        # queryRenderedFeatures also returns fading symbols. Count the dark
        # antialiased strokes of the actual first line in the WebGL framebuffer.
        result=await ev("""new Promise(resolve=>{
          let samples=0, absent=0, weak=0;
          const begin=performance.now(), canvas=map.getCanvas(), gl=canvas.getContext('webgl')||canvas.getContext('experimental-webgl');
          function frame(){
            const f=map.queryRenderedFeatures({layers:['region-label']}).find(f=>f.properties.number==='G3649');
            samples++;
            if(!f)absent++;
            else {
              const p=map.project(f.geometry.coordinates), k=canvas.width/canvas.clientWidth;
              const w=Math.ceil(46*k), h=Math.ceil(17*k), pixels=new Uint8Array(w*h*4);
              gl.readPixels(Math.round((p.x-23)*k),canvas.height-Math.round((p.y-10-14.4*f.properties.label.split('\\n').length)*k)-h,w,h,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
              let ink=0;
              for(let i=0;i<pixels.length;i+=4)
                if(pixels[i]<130 && pixels[i+1]-pixels[i]>14 && pixels[i+1]-pixels[i]<32 && pixels[i+2]>pixels[i+1] && pixels[i+2]-pixels[i+1]<8)ink++;
              if(ink<12)weak++;
            }
            if(performance.now()-begin>2200){map.off('render',frame);resolve({samples,absent,weak})}
          }
          map.on('render',frame);map.triggerRepaint();
        })""")
        print(sign,result,flush=True)
        await ev("document.querySelector('[data-rate=\"0\"]').click()")
        assert result['samples']>10 and not result['absent'] and not result['weak'], result
asyncio.run(drive(sys.argv[1],check))
