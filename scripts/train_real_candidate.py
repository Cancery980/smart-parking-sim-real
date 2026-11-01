"""Train an isolated real-photo candidate without replacing the shipped model."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--epochs', type=int, default=60)
    parser.add_argument('--batch', type=int, default=2)
    parser.add_argument('--imgsz', type=int, default=1280)
    args = parser.parse_args()
    weights, data, manifest = [p.resolve(strict=True) for p in (args.weights, args.data, args.manifest)]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / args.name).exists():
        raise FileExistsError('Candidate run already exists; use a new name or explicitly resume outside this script')
    os.environ['YOLO_CONFIG_DIR'] = str(output / 'ultralytics')
    os.environ['MPLCONFIGDIR'] = str(output / 'matplotlib')
    os.chdir(output)
    from ultralytics import YOLO
    import torch
    import ultralytics
    request = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'starting_weights': str(weights), 'starting_weights_sha256': digest(weights),
        'dataset_yaml': str(data), 'dataset_yaml_sha256': digest(data),
        'dataset_manifest': str(manifest), 'dataset_manifest_sha256': digest(manifest),
        'name': args.name, 'epochs': args.epochs, 'batch': args.batch, 'imgsz': args.imgsz,
        'seed': 20261102, 'torch': torch.__version__, 'ultralytics': ultralytics.__version__,
        'selection': 'Ultralytics best validation fitness on development images only; test data not loaded',
    }
    (output / f'{args.name}_request.json').write_text(json.dumps(request, indent=2), encoding='utf-8')
    model = YOLO(str(weights))
    model.train(data=str(data), epochs=args.epochs, patience=18, imgsz=args.imgsz,
                batch=args.batch, device=0, workers=0, project=str(output), name=args.name,
                exist_ok=False, seed=20261102, deterministic=True, pretrained=True,
                cache=False, amp=True, plots=True, save=True, verbose=False,
                mosaic=.5, close_mosaic=10, fliplr=.5, translate=.1, scale=.3,
                degrees=0, max_det=500)
    run = Path(model.trainer.save_dir)
    best = run / 'weights/best.pt'
    request.update({'finished_at_utc': datetime.now(timezone.utc).isoformat(),
                    'best_weights': str(best), 'best_weights_sha256': digest(best)})
    (run / 'provenance.json').write_text(json.dumps(request, indent=2), encoding='utf-8')
    shutil.copy2(manifest, run / 'input_manifest.json')
    print(json.dumps(request), flush=True)


if __name__ == '__main__':
    main()
