#!/usr/bin/env python3
"""Save CNRail's public vector map tiles and rendering resources."""
import argparse
import concurrent.futures
import gzip
import itertools
import json
import math
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
MAP = ROOT / "map"
ORIGIN = "http://railmap.geogv.org"
STYLE = "styles/wtrans2-general-default-zhcn"
BOUNDS = (69, 17, 136, 56)
lock = threading.Lock()
next_request = 0.0


def download(url, path, optional=False):
    global next_request
    if path.exists():
        return "cached"
    for attempt in range(3):
        with lock:
            delay = max(0, next_request - time.monotonic())
            next_request = time.monotonic() + delay + 0.22
        time.sleep(delay)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Rails-local-map/1.0"})
            with urllib.request.urlopen(request, timeout=18) as response:
                data = response.read()
            if data.startswith(b"\x1f\x8b"):
                data = gzip.decompress(data)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            return "downloaded"
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and optional:
                return "absent"
            if exc.code in (403, 429, 503):
                with lock:
                    next_request = max(next_request, time.monotonic() + 12)
                if exc.code == 403:
                    raise
            if attempt == 2:
                raise
        except OSError:
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError(url)


def tile(lon, lat, zoom):
    size = 2 ** zoom
    return (int((lon + 180) / 360 * size),
            int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--maxzoom", type=int, default=8, choices=range(1, 9))
    opts = parser.parse_args()
    source = json.loads((MAP / "tilejson.json").read_text())
    template = source["tiles"][0]
    style = json.loads((MAP / "style.json").read_text())
    for name in ["sprite.json", "sprite.png", "sprite@2x.json", "sprite@2x.png"]:
        download(f"{ORIGIN}/{STYLE}/{name}", MAP / name, optional="@" in name)
    fonts = {",".join(layer["layout"]["text-font"]) for layer in style["layers"]
             if "text-font" in layer.get("layout", {})}
    tasks = []
    for font in sorted(fonts):
        for start in [0, 256, 512, 768, 1024, 8192, 12288, 65280]:
            span = f"{start}-{start+255}.pbf"
            encoded = urllib.parse.quote(font, safe="")
            tasks.append((f"{ORIGIN}/fonts/{encoded}/{span}", MAP / "fonts" / font / span))
    for zoom in range(1, opts.maxzoom + 1):
        x0, y0 = tile(BOUNDS[0], BOUNDS[3], zoom)
        x1, y1 = tile(BOUNDS[2], BOUNDS[1], zoom)
        for x, y in itertools.product(range(x0, x1 + 1), range(y0, y1 + 1)):
            tasks.append((template.format(z=zoom, x=x, y=y), MAP / f"tiles/{zoom}/{x}/{y}.pbf"))
    counts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = {pool.submit(download, url, path, True): url for url, path in tasks}
        for index, future in enumerate(concurrent.futures.as_completed(results), 1):
            try:
                status = future.result()
            except Exception as exc:
                status = "failed"
                print(f"{results[future]}: {exc}", flush=True)
            counts[status] = counts.get(status, 0) + 1
            if index % 100 == 0 or index == len(tasks):
                print(f"{index}/{len(tasks)} {counts}", flush=True)
    if counts.get("failed"):
        raise SystemExit("Some downloads failed. Run again to retry missing files.")


if __name__ == "__main__":
    main()
