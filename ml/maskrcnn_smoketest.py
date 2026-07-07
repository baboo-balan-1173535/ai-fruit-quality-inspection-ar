"""
Mask R-CNN pretrained-inference smoke test (ML upgrade, Phase 0).

Proves the segmentation pipeline WITHOUT Detectron2 or any training: loads
torchvision's COCO-pretrained maskrcnn_resnet50_fpn and checks whether instance
masks separate clustered/overlapping fruit. Auto-uses CUDA (RTX 5060 / Blackwell)
if available, else CPU. Writes mask+box overlays to --out.

Setup (dedicated GPU venv, kept separate from the Flask app's venv):
    py -3.11 -m venv ml/venv
    ml/venv/Scripts/python -m pip install -U pip
    ml/venv/Scripts/python -m pip install torch==2.11.0 torchvision==0.26.0 \
        --index-url https://download.pytorch.org/whl/cu128   # cu128 = Blackwell

Run:
    ml/venv/Scripts/python ml/maskrcnn_smoketest.py IMG [IMG ...] --out ml/out
"""
import os, time, argparse
import torch
from torchvision.models.detection import (
    maskrcnn_resnet50_fpn, MaskRCNN_ResNet50_FPN_Weights)
from torchvision.io import read_image
from torchvision.utils import draw_segmentation_masks, draw_bounding_boxes
from torchvision.transforms.functional import convert_image_dtype
from PIL import Image

FRUIT = {"apple", "banana", "orange"}   # COCO classes relevant to Kiwi Sorter
SCORE_THRESH = 0.5
MASK_THRESH = 0.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out", default="ml/out")
    args = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"torch {torch.__version__} | device: {dev.upper()}")
    if dev == "cuda":
        print(f"gpu: {torch.cuda.get_device_name(0)}")

    weights = MaskRCNN_ResNet50_FPN_Weights.DEFAULT
    cats = weights.meta["categories"]
    print("loading pretrained Mask R-CNN (COCO)...")
    model = maskrcnn_resnet50_fpn(weights=weights).eval().to(dev)

    os.makedirs(args.out, exist_ok=True)
    for path in args.images:
        if not os.path.exists(path):
            print(f"  [skip] missing: {path}"); continue
        img = read_image(path)[:3]                 # drop alpha if present
        x = convert_image_dtype(img, torch.float).to(dev)
        t0 = time.time()
        with torch.no_grad():
            out = model([x])[0]
        if dev == "cuda":
            torch.cuda.synchronize()
        dt = (time.time() - t0) * 1000

        keep = out["scores"] >= SCORE_THRESH
        labels = [cats[i] for i in out["labels"][keep].tolist()]
        scores = out["scores"][keep].tolist()
        masks = out["masks"][keep]
        boxes = out["boxes"][keep]

        n_fruit = sum(1 for l in labels if l in FRUIT)
        name = os.path.basename(path)
        print(f"\n{name}  ({img.shape[2]}x{img.shape[1]})  {dt:.0f} ms")
        print(f"  instances>={SCORE_THRESH}: {len(labels)}  | fruit: {n_fruit}")
        for l, s in sorted(zip(labels, scores), key=lambda z: -z[1])[:12]:
            tag = "*" if l in FRUIT else " "
            print(f"   {tag} {l:<14} {s:.2f}")

        if len(labels):
            bool_masks = (masks.squeeze(1) > MASK_THRESH)
            vis = draw_segmentation_masks(img, bool_masks, alpha=0.55)
            vis = draw_bounding_boxes(
                vis, boxes.round().to(torch.int),
                labels=[f"{l} {s:.2f}" for l, s in zip(labels, scores)], width=2)
            outp = os.path.join(args.out, f"mask_{name}.png")
            Image.fromarray(vis.permute(1, 2, 0).cpu().numpy()).save(outp)
            print(f"   -> {outp}")


if __name__ == "__main__":
    main()
