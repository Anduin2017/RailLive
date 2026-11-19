// Build compact time/distance samples from the same function used by the map.
const fs = require('node:fs');
const readline = require('node:readline');
const vm = require('node:vm');
const env = {
  timetableSeconds: value => value ? value.split(':').reduce((n, x) => n * 60 + Number(x), 0) : null,
  hasPosition: stop => Number.isFinite(stop.lon) && Number.isFinite(stop.lat) && (!stop.coordinateQuality || stop.coordinateQuality === 'reference'),
  fromMapCoordinates: (x,y) => {
    const a=1.015972842,b=-0.000070497,c=0.000009795,d=1.012049241;
    x+=5.116568470; y-=1.113596968;
    return [(d*x-b*y)/(a*d-b*c),(a*y-c*x)/(a*d-b*c)];
  }
};
vm.runInNewContext(fs.readFileSync(__dirname+'/train-motion.js','utf8'),env);
(async () => {
  for await (const row of readline.createInterface({input:process.stdin})) {
    const trip = JSON.parse(row);
    trip.matchedPath = env.matchTrainPath(trip);
    const model = env.makeTrainMotion(trip);
    if (!model || model.reason) {
      process.stdout.write('[[],[],[]]\n');
      continue;
    }
    const knots=[], legMetrics=[];
    for (const [i,s] of trip.matchedPath.stops.entries()) {
      const next=trip.matchedPath.stops[i+1];
      knots.push([s.arrival,s.at===null ? null : Math.round(s.at)]);
      knots.push([s.departure,s.at===null ? null : Math.round(s.at)]);
      if (!next || next.arrival<=s.departure) continue;
      const mid=model.state((s.departure+next.arrival)/2);
      if (!mid?.leg) { knots.push([(s.departure+next.arrival)/2,null]); continue; }
      legMetrics.push([i,mid.leg.distance,mid.leg.peakKmh]);
      // Fine sampling at either end preserves acceleration and precise dwell boundaries.
      for(let t=s.departure+5;t<next.arrival;t+=Math.min(t-s.departure<300 || next.arrival-t<300 ? 5:20,next.arrival-t)) {
        knots.push([t,Math.round(mid.leg.sample(t-s.departure).at)]);
      }
    }
    process.stdout.write(JSON.stringify([knots,trip.matchedPath.stops.map(s=>s.at===null?null:Math.round(s.at)),legMetrics])+'\n');
  }
})();
