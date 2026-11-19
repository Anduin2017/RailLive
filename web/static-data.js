// Loaded only in the compiled site. The object manifest pins a complete snapshot
// for this tab; individual trip, station and line records load on demand.
'use strict';
window.staticRailData = (() => {
  const memo = new Map();
  function cached(key, load) {
    if (!memo.has(key)) memo.set(key, load().catch(error => { memo.delete(key); throw error; }));
    return memo.get(key);
  }
  async function read(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw Error(`HTTP ${response.status}: ${url}`);
    return response;
  }
  async function object(key) {
    if (!key) throw Error('记录不存在');
    const url = './records/objects/' + key.slice(0,2) + '/' + key.slice(2) + '.gz';
    const data = await (await read(url)).blob();
    const magic = new Uint8Array(await data.slice(0,2).arrayBuffer());
    if (magic[0] !== 31 || magic[1] !== 139) return JSON.parse(await data.text());
    const stream = data.stream().pipeThrough(new DecompressionStream('gzip'));
    return new Response(stream).json();
  }
  async function loadManifest() {
    return (await read('./records/index.json', {cache:'no-cache'})).json();
  }
  function table(name) {
    return cached(name, async () => {
      const manifest = await cached('manifest', loadManifest);
      return object(manifest[name]);
    });
  }
  async function lookup(kind, id) {
    const bucket = new TextEncoder().encode(id).reduce((sum, byte) => sum + byte, 0) % 64;
    const name = 'index/' + kind + '/' + bucket;
    const manifest = await cached('manifest', loadManifest);
    return manifest[name] ? (await table(name))[id] : undefined;
  }
  async function detail(kind, id) {
    return object(await lookup(kind, id));
  }
  // Match the server's stable ordering (Unicode code points, never locale collation).
  function compare(a, b) {
    if (typeof a === 'string' && typeof b === 'string' && (/[\uD800-\uDBFF]/.test(a) || /[\uD800-\uDBFF]/.test(b))) {
      const ac=Array.from(a), bc=Array.from(b);
      for (let i=0; i<Math.min(ac.length,bc.length); i++) {
        const d=ac[i].codePointAt(0)-bc[i].codePointAt(0);
        if (d) return d;
      }
      return ac.length-bc.length;
    }
    return a < b ? -1 : a > b ? 1 : 0;
  }
  function tuple(a,b) {
    for (let i=0;i<a.length;i++) {
      const d=compare(a[i],b[i]);
      if (d) return d;
    }
    return 0;
  }
  const names = row => [row.name, ...(row.aliases || [])].map(s => s.toLowerCase());
  function lineRank(row, term) {
    return names(row).filter(s=>s.includes(term))
      .map(s=>[s!==term,!s.startsWith(term),Array.from(s).length,s]).sort(tuple)[0];
  }
  async function search(params) {
    const q = (params.get('q') || '').trim().slice(0,60);
    if (!q) return {trains:[], stations:[], lines:[]};
    const term=q.toLowerCase();
    const [trains, stations, lines] = await Promise.all([
      /^[a-z0-9]+$/i.test(q) ? table('search-trains') : [], table('search-stations'),
      params.get('scope') === 'station' ? [] : table('catalog/lines')
    ]);
    return {
      trains:trains.filter(t=>t.number.toLowerCase().startsWith(term))
        .sort((a,b)=>tuple([a.number!==q,a.number.length,a.number,a.id],[b.number!==q,b.number.length,b.number,b.id]))
        .slice(0,80),
      stations:stations.filter(s=>s.name.includes(q)).sort((a,b)=>
        tuple([a.name!==q,a.name.length,a.name,!!a.sourceOnly,a.id],[b.name!==q,b.name.length,b.name,!!b.sourceOnly,b.id])).slice(0,30),
      lines:lines.filter(r=>names(r).some(s=>s.includes(term))).sort((a,b)=>tuple(lineRank(a,term),lineRank(b,term))).slice(0,20)
        .map(({id,name,aliases,origin,destination})=>({id,name,aliases,origin,destination}))
    };
  }
  function sortKey(kind, field, row) {
    const value=row[field];
    if (value == null) return null;
    if (kind==='trains' && field==='name') {
      const train=/^([A-Z]*)(\d+)$/.exec(value);
      return train ? [train[1],Number(train[2])] : [value,0];
    }
    if (kind==='trains' && (field==='arrival' || field==='departure')) {
      const [h,m,s]=value.split(':').map(Number);
      return [h*3600+m*60+s];
    }
    if (kind==='lines' && (field==='speed' || field==='tracks')) {
      const pattern=field==='speed' ? /(?<!\d)[1-9]\d{1,2}(?!\d)/g : /\b[1-9]\d?\b/g;
      const digits=String(value).match(pattern);
      return digits ? [Math.max(...digits.map(Number))] : null;
    }
    return [value];
  }
  async function catalog(kind, params) {
    const rows=await table('catalog/'+kind);
    const term=(params.get('q') || '').trim().slice(0,80).toLowerCase();
    const tags=params.getAll('tag'), multi=params.get('multi')==='1';
    const field=params.get('sort') || (kind==='stations'?'total':'name');
    const desc=(params.get('order') || (kind==='stations' && field==='total'?'desc':'asc'))==='desc';
    const selected=rows.filter(row=>{
      if (term && !names(row).some(s=>s.includes(term))) return false;
      if (kind==='stations') return (!multi || row.lines>=2) && (!tags.length || tags.some(t=>row[t]));
      if (kind==='lines' && tags.includes('link') && row.name.includes('联络线')) return true;
      return !tags.length || tags.includes(kind==='trains'?row.prefix:row.type);
    }).map(row=>({key:sortKey(kind,field,row),row})).sort((a,b)=>{
      if (a.key===null || b.key===null) return Number(a.key===null)-Number(b.key===null);
      return (desc?-1:1)*tuple(a.key,b.key);
    });
    const total=selected.length, pages=Math.max(1,Math.ceil(total/50));
    const page=Math.max(1,Math.min(pages,Number(params.get('page')) || 1));
    return {total,page,pages,pageSize:50,rows:selected.slice((page-1)*50,page*50).map(v=>v.row)};
  }
  async function relations(id, day) {
    const [services, template] = await Promise.all([table('services'), detail('relations',id)]);
    const runsOn=(trip,date)=>services.days[services.trips[trip]]?.includes(date);
    if (!runsOn(id, day)) return null;
    const base=Date.parse(day+'T00:00:00Z');
    const peers=[], indices=new Map();
    template.peers.forEach((p,i)=>{
      const date=new Date(base+p.offset*86400000).toISOString().slice(0,10);
      if (runsOn(p.id,date)) { indices.set(i,peers.length); peers.push(p); }
    });
    return {peers,events:template.events.filter(e=>indices.has(e.peer)).map(e=>({...e,peer:indices.get(e.peer)}))};
  }
  async function get(path) {
    const url=new URL(path,location.href);
    const [, ,kind, token]=url.pathname.split('/');
    const id=decodeURIComponent(token || ''), params=url.searchParams;
    switch (kind) {
      case 'info': return table('info');
      case 'search': return search(params);
      case 'catalog': return catalog(id,params);
      case 'region-live': return detail('display',params.get('trip'));
      case 'relations': return relations(id,params.get('day'));
      case 'line': {
        const type=params.get('from');
        const key=type==='map'?'map-line':type==='gtfs'?'gtfs-line':'line';
        return object(await lookup(key,id) || await lookup('line',id));
      }
      case 'station-calendar': {
        const [station,services,info]=await Promise.all([detail('station',id),table('services'),table('info')]);
        const calendars={};
        for (const service of new Set(station.trains.map(t=>t.service))) {
          calendars[service]=(services.days[service] || []).map(d=>d.replaceAll('-','')).join(',');
        }
        return {calendars,start:info.feed_start_date,end:info.feed_end_date};
      }
      case 'station': case 'station-lines': case 'trip': return detail(kind,id);
      default: throw Error('未知静态数据类型 '+kind);
    }
  }
  return {get};
})();
