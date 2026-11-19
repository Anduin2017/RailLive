"""Compile a complete static railway site from a prepared snapshot."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import tempfile

from prepare import ROOT, prepare
from snapshot_layout import PUBLIC_MAP, runtime_files


def build(snapshot, output, jobs):
    runtime_files(snapshot)
    if output.exists():
        raise SystemExit(f'目标目录已存在，请指定新的输出目录：{output}')
    if any(source in output.parents for source in (ROOT/'web', ROOT/'licenses')):
        raise SystemExit('请选择网页和许可源码目录之外的输出位置。')
    if snapshot in output.parents:
        raise SystemExit('请选择快照目录之外的输出位置。')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.rails-static-', dir=output.parent) as temp:
        app = Path(temp) / 'app'
        site = Path(temp) / 'site'
        prepare(app, snapshot)
        sys.path.insert(0, str(app))
        from railway_graph import verify_packaged_snapshot
        from region_live import verify
        from build_relations import fingerprint
        from train_relations import TRACKS
        import sqlite3
        from contextlib import closing
        verify_packaged_snapshot()
        verify()
        with closing(sqlite3.connect(TRACKS.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('SELECT signature FROM meta').fetchone()[0] != fingerprint():
                raise ValueError('运行索引与本版源码不匹配。')
        from static_records import inventory, compile_one, connection, write_json
        site.mkdir()
        data = site / 'records'
        data.mkdir()
        initial, tasks = inventory(data)
        # Workers open their own read-only SQLite connections.
        connection().close()
        connection.cache_clear()
        indices = {}
        print(f'编译 {len(tasks):,} 个站、线、班次及车序记录，使用 {jobs} 个进程。', flush=True)
        with ProcessPoolExecutor(jobs, mp_context=multiprocessing.get_context('fork')) as pool:
            results = pool.map(compile_one, ((data, kind, key) for kind, key in tasks), chunksize=12)
            for i, group in enumerate(results, 1):
                for kind, key, digest in group:
                    indices.setdefault(kind, {})[key] = digest
                if i % 2000 == 0:
                    print(f'{i:,}/{len(tasks):,}', flush=True)
        from static_records import blob
        # Partition ID lookups so selecting one train on a slow connection
        # does not download the nationwide directory of detail records.
        for kind, index in indices.items():
            shards = {}
            for key, value in index.items():
                bucket = sum(key.encode('utf-8')) % 64
                shards.setdefault(bucket, {})[key] = value
            for bucket, shard in shards.items():
                initial[f'index/{kind}/{bucket}'] = blob(data, shard)
        write_json(data / 'index.json', initial)
        copy_assets(snapshot, site)
        site.rename(output)
        print(f'静态站点已生成：{output}', flush=True)


def copy_assets(snapshot, site):
    shutil.copytree(ROOT / 'web', site, dirs_exist_ok=True)
    for name in PUBLIC_MAP:
        if name == 'tiles':  # the split vector layers supply the public map
            continue
        source = snapshot / 'map' / name
        dest = site / 'map' / name
        dest.parent.mkdir(exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, dest)
        else:
            shutil.copy2(source, dest)
    # The API server answers missing glyph ranges with an empty protobuf.
    # Materialize the same response for the bundled fonts on a file-only host.
    for font in (site / 'map/fonts').iterdir():
        if font.is_dir():
            for low in range(0, 65536, 256):
                (font / f'{low}-{low+255}.pbf').touch(exist_ok=True)
    manifest = snapshot / 'space-time/manifest.json'
    version = json.loads(manifest.read_bytes())['version']
    shutil.copytree(manifest.parent / version, site / 'space-time' / version)
    shutil.copy2(manifest, site / 'space-time/manifest.json')
    shutil.copy2(ROOT / 'LICENSE', site)
    notices = (ROOT / 'THIRD_PARTY.md').read_text().replace('web/vendor/', 'vendor/')
    (site / 'THIRD_PARTY.md').write_text(notices)
    shutil.copytree(ROOT / 'licenses', site / 'licenses')
    html = site / 'index.html'
    html.write_text(html.read_text().replace('<!-- static-data -->', '<script defer src="./static-data.js"></script>'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'snapshot')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/site')
    parser.add_argument('--jobs', type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    build(args.snapshot.resolve(), args.output.resolve(), args.jobs)
