"""Exercise search, map dismissal and the station board on a phone viewport."""
import asyncio
import sys

from browser_driver import drive


async def check(ev, wait):
    await ev("""window.q=s=>document.querySelector(s);
      window.visible=s=>!!q(s)?.getClientRects().length;
      window.blank=()=>{
        const canvas=map.getCanvas(), rect=canvas.getBoundingClientRect();
        const layers=map.getStyle().layers.filter(l=>l['source-layer']==='rail'||l.source==='rail-detail'||
          l.source==='selected-station'||l.source==='selected-route'||l.source==='region-trains'||
          l.source==='selected-train'||l.id.startsWith('station-pax')).map(l=>l.id);
        for(let y=100;y<rect.height-110;y+=12)for(let x=27;x<rect.width-65;x+=12){
          if(map.queryRenderedFeatures([x,y],{layers}).length)continue;
          const clientX=rect.left+x,clientY=rect.top+y;
          if(document.elementFromPoint(clientX,clientY)!==canvas)continue;
          for(const type of ['mousedown','mouseup','click'])canvas.dispatchEvent(new MouseEvent(type,{
            clientX,clientY,button:0,buttons:type==='mousedown'?1:0,bubbles:true,view:window}));
          return true;
        }
        return false;
      }""")
    assert await ev("visible('#query') && q('#search-toggle').getAttribute('aria-expanded')==='true'")
    assert await ev("blank() && sidebar.hidden && !visible('#query')")
    await ev("q('#search-toggle').click(); query.value='苏州北'; form.requestSubmit()")
    await wait("sidebar.dataset.window==='search' && panel.querySelector('.result')?.textContent.includes('苏州北')")
    await ev("panel.querySelector('.result').click()")
    await wait("sidebar.dataset.window==='info' && q('.station-tabs') && !visible('#query')")
    assert await ev("visible('.station-panel-switch') && !visible('#station-board')")
    assert await ev("visible('#sheet-minimize') && !sidebar.querySelector('#snapshot, .legend')")
    await ev("q('[data-station-view=board]').click()")
    await wait("visible('#station-board .board-row') && !visible('#panel')")
    assert await ev("q('#station-board').getBoundingClientRect().bottom < q('.catalog-dock').getBoundingClientRect().top")
    await ev("q('#sheet-minimize').click()")
    assert await ev("sidebar.hidden && !visible('#station-board') && document.activeElement===sidebarToggle")
    await ev("q('#sidebar-toggle').click()")
    assert await ev("visible('#station-board') && !visible('#query')")
    await wait("!map.isMoving()")
    assert await ev("blank() && sidebar.hidden && !q('#station-board')")
    await ev("q('#search-toggle').click(); query.value='G1211'; form.requestSubmit()")
    await wait("sidebar.dataset.window==='info' && q('#panel h2')?.textContent==='G1211'")
    assert await ev("!visible('#query')")
    await ev("q('#sheet-handle').click()")
    await wait("sidebar.dataset.expanded==='true'")
    await ev("q('#sheet-minimize').click()")
    assert await ev("sidebar.hidden && sidebar.dataset.expanded==='false'")
    await ev("q('#sidebar-toggle').click()")
    assert await ev("visible('#sheet-minimize') && q('#panel h2')?.textContent==='G1211'")
    await ev("q('#search-toggle').click()")
    assert await ev("visible('#query') && !q('#panel h2') && route.features.length===0")
    await ev("q('#about-toggle').click()")
    assert await ev("""sidebar.hidden && visible('#about-panel #snapshot') &&
      q('#snapshot').textContent.includes('座车站') && q('#about-panel .legend').children.length===5""")
    print('Phone panels, station board, train selection, minimize, map dismissal and About passed.')


if __name__ == '__main__':
    asyncio.run(drive(sys.argv[1], check, viewport=(390, 844)))
