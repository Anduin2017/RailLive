"""Assemble the same runtime layout for Python development and Docker."""
import argparse
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from snapshot_layout import PUBLIC_MAP


def link(target, path):
    if path.is_symlink() and os.readlink(path) != str(target):
        path.unlink()
    if not path.is_symlink():
        path.symlink_to(target)


def prepare(output, snapshot):
    output.mkdir(parents=True, exist_ok=True)
    # The snapshot fingerprints the original generator and motion model bytes.
    # Keep these modules adjacent, as when the packaged indices were generated.
    for folder in [ROOT / 'backend', ROOT / 'tools/snapshot']:
        for source in folder.iterdir():
            if source.suffix in ('.py', '.cjs'):
                shutil.copy2(source, output / source.name)
    public = output / 'public'
    shutil.copytree(ROOT / 'web', public, dirs_exist_ok=True)
    link('public/train-motion.js', output / 'train-motion.js')
    for name in ('data', 'map', 'space-time'):
        link(snapshot / name, output / name)
    for name in ('map', 'data'):
        (public / name).mkdir(exist_ok=True)
    for name in PUBLIC_MAP:
        link('../../map/' + name, public / 'map' / name)
    link('../../data/live-audit', public / 'data/live-audit')
    link('../space-time', public / 'space-time')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/app')
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'snapshot')
    args = parser.parse_args()
    prepare(args.output.resolve(), args.snapshot.resolve())
