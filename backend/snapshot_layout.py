"""Runtime snapshot files shared by the server check and the offline exporter."""
import json
from pathlib import Path
import re


PUBLIC_MAP = (
    'tiles', 'split-style.json', 'rail-detail.geojson', 'railway', 'scenery', 'fonts',
    'sprite.png', 'sprite.json', 'sprite@2x.png', 'sprite@2x.json', 'station-symbols',
)
DATA = (
    'trains.sqlite', 'relations.sqlite', 'region.sqlite', 'line-facts.json',
    'cnrail-graph.json.gz', 'cnrail-lines.json', 'cnrail-audit.json',
    'GTFS-LICENSE.txt', 'live-audit',
)
MAP = (*PUBLIC_MAP, 'coverage.json', 'rail-selection.json', 'rail-tracks.json.gz')


def runtime_files(root):
    paths = [Path('data') / name for name in DATA] + [Path('map') / name for name in MAP]
    manifest = Path('space-time/manifest.json')
    for path in [*paths, manifest]:
        if not (root / path).exists():
            raise ValueError(f'缺少快照文件 {path}。请准备同一版本的 data、map 和 space-time，见 README。')
    version = json.loads((root / manifest).read_text())['version']
    if not re.fullmatch(r'[0-9a-f]{20}', version):
        raise ValueError('space-time/manifest.json 中的版本号无效')
    generation = manifest.parent / version
    for path in (generation / 'catalog.gz', Path('map/station-symbols/index.json'),
                 Path('data/live-audit/index.html')):
        if not (root / path).is_file():
            raise ValueError(f'快照不完整，缺少 {path}')
    return [*paths, manifest, generation]
