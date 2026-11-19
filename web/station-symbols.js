'use strict';

const stationSymbols = (() => {
  const baseLayers=['station-pax-245-LT','station-pax-01-LT'];
  const nameLayers=baseLayers.map(id=>id+'-name');
  const folder='./map/station-symbols/';
  let manifest, activeDay, activeProfile, ready=false;
  const cache=new Map();
  const key=['to-string',['coalesce',['get','kid'],['get','id'],'']];
  const lookup=(table,fallback)=>['number',['get',key,['literal',table]],fallback];
  // Platforms reach their full sprite size at level 12.
  const fullSizeZoom=12;
  const scale=['interpolate',['exponential',2],['zoom'],0,2**-fullSizeZoom,fullSizeZoom,1];
  function sprite(selected,tiers) {
    const prefix=selected?'platform-focus-':'platform-';
    return ['case',['has',key,['literal',manifest.axes]],
      ['concat',prefix,['to-string',lookup(tiers,0)]],prefix+'unknown'];
  }

  // The map tile carries the drawn platform centre and its label anchor.
  // Station cards can use a slightly different surveying/reference point.
  let selectedKey=null, painted=null, pending=0;
  function alignSelection() {
    pending=0;
    if (!selectedStation || !ready) return;
    const tileFilter=['==',key,selectedKey];
    let match;
    for (const sourceLayer of ['railpoi0','railpoi1']) {
      match=map.querySourceFeatures('openmaptiles',{sourceLayer,filter:tileFilter})
        .find(f=>f.geometry.type==='Point');
      if (match) break;
    }
    const coordinates=match?.geometry.coordinates || selectedStation.geometry.coordinates;
    const anchor=match?.properties.anchor || 'R';
    const signature=[selectedKey,...coordinates,anchor].join('/');
    if (signature===painted) return;
    painted=signature;
    map.getSource('selected-station').setData({...selectedStation,
      properties:{...selectedStation.properties,anchor},geometry:{type:'Point',coordinates}});
  }

  function select() {
    if (!ready) return;
    selectedKey=selectedStation?.properties.kid || null;
    for (const layer of [...baseLayers,...nameLayers])
      map.setFilter(layer,selectedKey ? ['!=',key,selectedKey] : null);
    painted=null;
    if (selectedKey) alignSelection();
    else map.getSource('selected-station').setData(emptyStation);
  }

  async function prepare() {
    const atlas=new Image();
    atlas.src=folder+'platforms.svg';
    [manifest]=await Promise.all([get(folder+'index.json?v=track-span1'),atlas.decode()]);
    return atlas;
  }

  function install(atlas) {
    const canvas=document.createElement('canvas');
    canvas.width=atlas.width;
    canvas.height=atlas.height;
    const ctx=canvas.getContext('2d',{willReadFrequently:true});
    ctx.drawImage(atlas,0,0);
    // Cut each sprite to its own bounds so the transparent atlas cell does
    // not intercept clicks meant for a neighbouring track.
    for (const selected of [false,true]) {
      map.addImage((selected?'platform-focus-':'platform-')+'unknown',
        ctx.getImageData(0,160*Number(selected),22,22),{pixelRatio:2});
    }
    for (const [tier,[length,width]] of manifest.sizes.entries()) {
      for (const selected of [false,true]) {
        const image=ctx.getImageData(80*tier+40-width-2,160*Number(selected)+80-length-2,2*width+4,2*length+4);
        map.addImage((selected?'platform-focus-':'platform-')+tier,image,{pixelRatio:2});
      }
    }
    // Give station names their own draw order above train captions.
    const styleLayers=map.getStyle().layers;
    baseLayers.forEach((id,index)=>{
      const base=styleLayers.find(layer=>layer.id===id);
      const layout=Object.fromEntries(Object.entries(base.layout).filter(([key])=>!key.startsWith('icon-')));
      map.addLayer({...base,id:nameLayers[index],layout:{...layout,'text-rotation-alignment':'viewport'}});
      map.setLayoutProperty(id,'text-field','');
    });
    for (const id of [...baseLayers,'selected-station-ring']) {
      const layout={'icon-image':sprite(id==='selected-station-ring',{}),'icon-size':scale,
        'icon-rotate':lookup(manifest.axes,0),'icon-rotation-alignment':'map',
        'icon-allow-overlap':true,'icon-ignore-placement':true};
      for (const [name,value] of Object.entries(layout)) map.setLayoutProperty(id,name,value);
    }
    ready=true;
    if (selectedStation) select();
    map.on('sourcedata',event=>{
      if (selectedKey && event.sourceId==='openmaptiles' && event.sourceDataType==='content' && !pending)
        pending=requestAnimationFrame(alignSelection);
    });
    map.on('moveend',()=>{if (selectedKey && !pending) pending=requestAnimationFrame(alignSelection);});
    setDay(liveTimeline.iso(liveTimeline.time()).slice(0,10));
  }

  async function setDay(day) {
    if (!ready || activeDay===day) return;
    activeDay=day;
    const profile=manifest.days[day] || '';
    if (activeProfile===profile) return;
    try {
      if (!cache.has(profile)) cache.set(profile,profile ? get(folder+profile+'.json') : Promise.resolve({}));
      const tiers=await cache.get(profile);
      if (activeDay!==day) return;
      for (const id of [...baseLayers,'selected-station-ring'])
        map.setLayoutProperty(id,'icon-image',sprite(id==='selected-station-ring',tiers));
      activeProfile=profile;
    } catch (error) {
      cache.delete(profile);
      if (activeDay===day) activeDay=null;
      console.error(error);
    }
  }
  return {prepare,install,select,setDay,nameLayers};
})();
