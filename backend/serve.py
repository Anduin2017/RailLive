"""Serve the public web tree and the read-only timetable API."""
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from snapshot_layout import runtime_files


def main():
    root = Path(__file__).resolve().parent
    try:
        runtime_files(root)
    except (OSError, KeyError, ValueError) as error:
        raise SystemExit(str(error)) from error
    from server import Handler
    from railway_graph import verify_packaged_snapshot
    from region_live import verify
    from build_relations import fingerprint
    from train_relations import TRACKS
    from contextlib import closing
    import sqlite3

    verify_packaged_snapshot(preview=os.getenv('RAILS_PREVIEW') == '1')
    with closing(sqlite3.connect(TRACKS.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('SELECT signature FROM meta').fetchone()[0] != fingerprint():
            raise SystemExit('运行索引与源码或数据版本不匹配，请重新生成 relations.sqlite。')
    verify()

    class PublicHandler(Handler):
        def __init__(self, *args, **kwargs):
            SimpleHTTPRequestHandler.__init__(self, *args, directory=root / 'public', **kwargs)

    bind = os.getenv('RAILS_BIND', '127.0.0.1')
    port = int(os.getenv('RAILS_PORT', '8080'))
    with ThreadingHTTPServer((bind, port), PublicHandler) as httpd:
        print(f'中国铁路实况 http://{bind}:{port}', flush=True)
        httpd.serve_forever()


if __name__ == '__main__':
    main()
