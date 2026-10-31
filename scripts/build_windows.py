"""Rebuild the Windows CPU onedir demo; see docs/WINDOWS_BUILD.md."""
from __future__ import annotations
import argparse
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.venv', 'venv', 'cpu-venv', 'runtime', '__pycache__', '.pytest_cache', '.build', 'dist', 'Ultralytics'}


def copy_source_and_licenses(bundle: Path) -> None:
    """Keep the complete current project, excluding local/generated environments."""
    bundle = bundle.resolve()
    for directory, dirs, files in os.walk(ROOT):
        relative = Path(directory).relative_to(ROOT)
        dirs[:] = [d for d in dirs if d not in EXCLUDED and (Path(directory) / d).resolve() != bundle
                   and not (relative == Path('data') and d == 'training_runs')]
        for name in files:
            if name == '.local-python.txt' or name.endswith('.pyc'):
                continue
            target = bundle / 'source' / relative / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(directory) / name, target)
    shutil.copytree(ROOT / 'third_party', bundle / 'licenses/project_notices', dirs_exist_ok=True)
    for dist in importlib.metadata.distributions():
        for entry in dist.files or []:
            name = Path(str(entry)).name.lower()
            if not (name.startswith(('license', 'licence', 'copying', 'copyright', 'notice')) or '.dist-info/licenses/' in str(entry).replace('\\', '/')):
                continue
            original = Path(dist.locate_file(entry))
            if original.is_file():
                relative = Path(*[p for p in Path(str(entry)).parts if p not in {'.', '..'}])
                target = bundle / 'licenses' / dist.metadata.get('Name', 'unknown') / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)


def main() -> None:
    parser = argparse.ArgumentParser(description='Build Windows CPU demo with tested PyInstaller settings')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist/windows')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Build this executable on Windows x64')
    import torch
    if torch.__version__ != '2.5.1+cpu' or torch.version.cuda is not None:
        raise RuntimeError('Use torch==2.5.1+cpu in an isolated build environment; do not bundle CUDA')
    if importlib.metadata.version('pyinstaller') != '6.22.0':
        raise RuntimeError('Install the tested pyinstaller==6.22.0')
    work = ROOT / '.build/windows'
    work.mkdir(parents=True, exist_ok=True)
    output = args.output_dir.resolve()
    os.environ['YOLO_CONFIG_DIR'] = str(work / 'ultralytics')
    os.environ['MPLCONFIGDIR'] = str(work / 'matplotlib')
    os.environ['MPLBACKEND'] = 'Agg'
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console',
               '--name', 'ParkingDemo', '--distpath', str(output), '--workpath', str(work / 'pyinstaller'),
               '--specpath', str(work), '--paths', str(ROOT), '--collect-all', 'ultralytics',
               '--copy-metadata', 'torch', '--copy-metadata', 'torchvision', '--copy-metadata', 'ultralytics',
               '--hidden-import', 'parking_hu.reference_planner', '--hidden-import', 'uvicorn.logging',
               '--hidden-import', 'uvicorn.loops.asyncio', '--hidden-import', 'uvicorn.protocols.http.h11_impl',
               '--hidden-import', 'uvicorn.lifespan.on', '--exclude-module', 'pytest', '--exclude-module', 'IPython',
               '--exclude-module', 'notebook', '--exclude-module', 'tkinter', '--exclude-module', 'tensorboard',
               '--add-data', f'{ROOT / "static"};static',
               '--add-data', f'{ROOT / "models/parking_yolov8n.pt"};models',
               '--add-data', f'{ROOT / "reports/report.html"};reports', str(ROOT / 'scripts/portable_entry.py')]
    subprocess.run(command, check=True, cwd=ROOT)
    bundle = output / 'ParkingDemo'
    copy_source_and_licenses(bundle)
    print(f'Built: {bundle / "ParkingDemo.exe"}')
    print('Run the actual EXE and check detection, allocation, confirmation, finding, and departure before zipping.')


if __name__ == '__main__':
    main()
