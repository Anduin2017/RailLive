"""Split the offline vector tiles so railway views can skip all scenery bytes."""
import json
from pathlib import Path

from index_rail_map import fields, varint

MAP = Path(__file__).resolve().parent / "map"
RAIL_LAYERS = {"rail", "platform", "railcluster", "subway", "railpoi0", "railpoi1"}


def split_tile(tile):
    tracks, scenery = bytearray(), bytearray()
    start = 0
    while start < len(tile):
        tag, pos = varint(tile, start)
        if tag != 26:  # MVT Tile.layers, length-delimited field 3
            raise ValueError(f"Unexpected tile field {tag}")
        size, pos = varint(tile, pos)
        end = pos + size
        name = next(value for key, value in fields(tile[pos:end]) if key == 1)
        dest = tracks if name.decode() in RAIL_LAYERS else scenery
        dest.extend(tile[start:end])
        start = end
    return tracks, scenery


def main():
    style = json.loads((MAP / "offline-style.json").read_text())
    source = style["sources"]["openmaptiles"]
    for key, folder in (("scenery", "scenery"), ("openmaptiles", "railway")):
        style["sources"][key] = {**source, "tiles": [f"/map/{folder}/{{z}}/{{x}}/{{y}}.pbf"]}
    for layer in style["layers"]:
        if layer.get("source") == "openmaptiles" and layer["source-layer"] not in RAIL_LAYERS:
            layer["source"] = "scenery"
    totals = [0, 0]
    for src in (MAP / "tiles").glob("*/*/*.pbf"):
        for i, (folder, data) in enumerate(zip(("railway", "scenery"), split_tile(src.read_bytes()))):
            path = MAP / folder / src.relative_to(MAP / "tiles")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            totals[i] += len(data)
    (MAP / "split-style.json").write_text(json.dumps(style, ensure_ascii=False, separators=(",", ":")))
    print(f"Railway {totals[0]:,} bytes, scenery {totals[1]:,} bytes")


if __name__ == "__main__":
    main()
