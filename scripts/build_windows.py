"""Rebuild the Windows CPU onedir demo; see docs/WINDOWS_BUILD.md."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.venv', 'venv', 'cpu-venv', 'runtime', '__pycache__', '.pytest_cache', '.build', 'dist', 'Ultralytics'}


def runtime_resources() -> list[tuple[Path, str]]:
    """Resolve the frozen model and offline photographs before invoking the build."""
    config_path = ROOT / 'models/real_photo_config.json'
    manifest_path = ROOT / 'data/real/manifest.json'
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        relative_weights = config['weights']
        if not isinstance(relative_weights, str) or not relative_weights or Path(relative_weights).is_absolute():
            raise ValueError('真实照片模型 weights 必须为项目内的相对路径')
        real_weights = (ROOT / relative_weights).resolve()
        if not real_weights.is_relative_to(ROOT.resolve()):
            raise ValueError('真实照片模型路径不能超出项目目录')
        photos = manifest['photos']
        if not isinstance(photos,list) or not photos:
            raise ValueError('真实照片清单为空或格式不正确')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f'无法读取真实照片打包配置；请先准备 manifest.json 和 real_photo_config.json：{exc}') from exc
    resources = [(ROOT/'static','static'), (ROOT/'models/parking_yolov8n.pt','models'),
                 (config_path,'models'), (real_weights,real_weights.relative_to(ROOT).parent.as_posix()),
                 (manifest_path,'data/real'), (ROOT/'data/real/images','data/real/images'),
                 (ROOT/'data/real/ATTRIBUTION.md','data/real'),
                 (ROOT/'reports/report.html','reports'), (ROOT/'reports/real_photo_report.html','reports')]
    for source,_ in resources:
        if not source.exists():
            raise FileNotFoundError(f'缺少打包必需资源：{source.relative_to(ROOT)}；请先生成报告并完成模型选择。')
    if config.get('weights_sha256') and hashlib.sha256(real_weights.read_bytes()).hexdigest() != config['weights_sha256']:
        raise ValueError('真实照片模型 SHA-256 与冻结配置不一致，请重新核对模型选择结果。')
    image_dir = (ROOT/'data/real/images').resolve()
    for photo in photos:
        relative = photo.get('image') if isinstance(photo,dict) else None
        if not isinstance(relative,str):
            raise ValueError('真实照片清单中的 image 路径缺失。')
        image_path = (ROOT/relative).resolve()
        if Path(relative).is_absolute() or not image_path.is_relative_to(image_dir):
            raise ValueError(f'真实照片必须位于 data/real/images 内：{relative}')
        if not image_path.is_file():
            raise FileNotFoundError(f'缺少离线真实照片：{relative}')
    # A selected model may also be one of the fixed resources.
    return list(dict.fromkeys(resources))


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
    resources = runtime_resources()
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
               '--exclude-module', 'notebook', '--exclude-module', 'tkinter', '--exclude-module', 'tensorboard']
    for source,destination in resources:
        command.extend(['--add-data',f'{source};{destination}'])
    command.append(str(ROOT/'scripts/portable_entry.py'))
    subprocess.run(command, check=True, cwd=ROOT)
    bundle = output / 'ParkingDemo'
    copy_source_and_licenses(bundle)
    print(f'Built: {bundle / "ParkingDemo.exe"}')
    print('Run the actual EXE: verify parking/session flow and real-photo inference before zipping.')


if __name__ == '__main__':
    main()
