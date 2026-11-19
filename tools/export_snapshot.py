"""Copy the active offline snapshot into a new directory for use on another host."""
import argparse
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from snapshot_layout import runtime_files


def export(source, destination):
    paths = runtime_files(source)
    if destination.exists() or source in destination.parents:
        raise SystemExit('请指定快照源目录之外的新目录作为导出目标。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        stage = Path(tmp) / 'snapshot'
        for rel in paths:
            src, dst = source / rel, stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        if runtime_files(stage) != paths:
            raise SystemExit('快照在导出期间发生了变化，请在数据更新完成后重试。')
        shutil.copy2(ROOT / 'THIRD_PARTY.md', stage)
        (stage / 'licenses').mkdir()
        shutil.copy2(ROOT / 'licenses/GTFS-CC-BY-4.0.txt', stage / 'licenses')
        stage.rename(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--source', type=Path, default=ROOT / 'snapshot')
    args = parser.parse_args()
    export(args.source.resolve(), args.destination.resolve())
