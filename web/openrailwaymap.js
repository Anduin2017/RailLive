'use strict';

// Place standard XYZ tiles in RailLive's calibrated map grid. An image source
// lets Mapbox project all four corners while the camera moves, tilts or rotates.
function openRailwayOverlay(map, before, onReady, onError) {
  const tiles = new Map();
  let active = true;
  let fetching = 0;
  let viewTimer;
  let pending = [];
  const refreshInterval = 150;

  function tileCorner(x, y, count) {
    const lon = x / count * 360 - 180;
    const lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * y / count))) * 180 / Math.PI;
    return toMapCoordinates([lon,lat]);
  }

  function visibleTiles() {
    const bounds = map.getBounds();
    const corners = [bounds.getNorthWest(),bounds.getNorthEast(),bounds.getSouthWest(),bounds.getSouthEast()]
      .map(p => fromMapCoordinates(p.lng,p.lat));
    // The camera uses 512 px tiles and the provider uses 256 px tiles.
    // Request the next level at a whole zoom step, not halfway through it.
    let zoom = Math.min(19, Math.max(2, Math.floor(map.getZoom() + 1)));
    let left, right, top, bottom, count;
    // At a steep pitch the horizon can span many tiles. Select a coarser
    // source zoom in that case rather than requesting thousands of images.
    do {
      count = 2 ** zoom;
      const xs = corners.map(([lon]) => Math.floor((lon + 180) / 360 * count));
      const ys = corners.map(([,lat]) => {
        const phi = Math.max(-85,Math.min(85,lat)) * Math.PI / 180;
        return Math.floor((1 - Math.asinh(Math.tan(phi)) / Math.PI) / 2 * count);
      });
      left = Math.max(0,Math.min(...xs));
      right = Math.min(count-1,Math.max(...xs));
      top = Math.max(0,Math.min(...ys));
      bottom = Math.min(count-1,Math.max(...ys));
      if ((right-left+1)*(bottom-top+1) <= 48 || zoom === 2) break;
      zoom--;
    } while (true);

    const visible = [];
    const {clientWidth: width, clientHeight: height} = map.getCanvas();
    for (let y=top; y<=bottom; y++) {
      for (let x=left; x<=right; x++) {
        const id = 'orm-' + zoom + '-' + x + '-' + y;
        const coordinates = [tileCorner(x,y,count),tileCorner(x+1,y,count),
          tileCorner(x+1,y+1,count),tileCorner(x,y+1,count)];
        const points = coordinates.map(c => map.project(c));
        if (points.every(p=>p.x<0) || points.every(p=>p.y<0) ||
            points.every(p=>p.x>width) || points.every(p=>p.y>height)) continue;
        const centerX = points.reduce((sum, point) => sum + point.x, 0) / points.length;
        const centerY = points.reduce((sum, point) => sum + point.y, 0) / points.length;
        const distance = Math.hypot(centerX - width / 2, centerY - height / 2);
        visible.push({id, coordinates, distance, url:'https://tiles.openrailwaymap.org/standard/'+zoom+'/'+x+'/'+y+'.png'});
      }
    }
    return visible.sort((a,b) => a.distance - b.distance);
  }

  function remove(tile) {
    tile.controller?.abort();
    if (map.getLayer(tile.id)) map.removeLayer(tile.id);
    if (map.getSource(tile.id)) map.removeSource(tile.id);
    if (tile.image) URL.revokeObjectURL(tile.image);
  }

  async function load(tile) {
    tile.controller = new AbortController();
    fetching++;
    const timeout = setTimeout(() => tile.controller.abort(), 8000);
    try {
      const response = await fetch(tile.url,{signal:tile.controller.signal,credentials:'omit'});
      if (tiles.get(tile.id) !== tile) return;
      if (response.status === 429) { onError(true); return; }
      if (response.status === 204 || response.status === 404) {
        onReady();
        return;
      }
      if (!response.ok) throw new Error();
      const blob = await response.blob();
      if (tiles.get(tile.id) !== tile) return;
      tile.image = URL.createObjectURL(blob);
      map.addSource(tile.id,{type:'image',url:tile.image,coordinates:tile.coordinates});
      map.addLayer({id:tile.id,source:tile.id,type:'raster',paint:{'raster-fade-duration':0}},before);
    } catch {
      if (active && tiles.get(tile.id) === tile) onError(false);
    } finally {
      clearTimeout(timeout);
      fetching--;
      pump();
    }
  }

  function pump() {
    while (active && fetching < 3 && pending.length) void load(pending.shift());
  }

  function refresh() {
    clearTimeout(viewTimer);
    viewTimer = undefined;
    const visible = visibleTiles();
    const visibleIds = new Set(visible.map(tile => tile.id));
    for (const [id,tile] of tiles) {
      if (!visibleIds.has(id)) {
        tiles.delete(id);
        remove(tile);
      }
    }
    pending = [];
    for (const candidate of visible) {
      let tile = tiles.get(candidate.id);
      if (!tile) {
        tile = candidate;
        tiles.set(tile.id,tile);
      }
      if (!tile.controller) pending.push(tile);
    }
    pump();
  }

  function scheduleRefresh() {
    // Keep up with a pan without recomputing and churning the queue every frame.
    // During a zoom, keep the existing images scaled until the camera settles.
    if (viewTimer !== undefined || map.isZooming()) return;
    viewTimer = setTimeout(() => {
      viewTimer = undefined;
      if (!map.isZooming()) refresh();
    }, refreshInterval);
  }

  function loaded(event) {
    if (tiles.has(event.sourceId) && event.sourceDataType === 'content') onReady();
  }

  map.on('move',scheduleRefresh);
  map.on('moveend',refresh);
  map.on('resize',refresh);
  map.on('sourcedata',loaded);
  refresh();
  return () => {
    active = false;
    clearTimeout(viewTimer);
    pending = [];
    map.off('move',scheduleRefresh);
    map.off('moveend',refresh);
    map.off('resize',refresh);
    map.off('sourcedata',loaded);
    for (const tile of tiles.values()) remove(tile);
    tiles.clear();
  };
}
