# ML upgrade — dedicated environment

Custom-model work (Detectron2 / Mask R-CNN ripeness head, see
`../Documentation/ML_upgrade_plan.md`). Kept in its own venv so the Flask app's
`venv/` (CPU torch) is never disturbed.

## Environment (GPU, Blackwell RTX 5060 = sm_120)

The 50-series needs a CUDA 12.8+ build of PyTorch; older wheels fall back to CPU.

```
py -3.11 -m venv ml/venv
ml/venv/Scripts/python -m pip install -U pip
ml/venv/Scripts/python -m pip install torch==2.11.0 torchvision==0.26.0 \
    --index-url https://download.pytorch.org/whl/cu128
```

Verify: `torch.cuda.is_available()` True, device `NVIDIA GeForce RTX 5060 Laptop
GPU`, capability `sm_120`.

## Phase 0 smoke test — DONE (7 Jul 2026)

`maskrcnn_smoketest.py` runs COCO-pretrained Mask R-CNN (torchvision, no
Detectron2, no training) to prove the segmentation pipeline and check instance
masks on clustered fruit.

```
ml/venv/Scripts/python ml/maskrcnn_smoketest.py \
    test_images/depositphotos_72511435-stock-photo-moldy-rotten-orange-fruit-near.jpg \
    --out ml/out
```

Result: pretrained Mask R-CNN inference working on the RTX 5060 at ~80-140 ms /
image (steady state; first call ~900 ms cuDNN warmup) vs ~1500 ms on CPU.
Segments two touching oranges as separate instances. Confirms the two gaps the
custom model must close: COCO has **no kiwi class**, and stock weights find only
**one fruit** in clustered AR frames.

## Custom model training — first results (Jul 2026)

Fine-tuned Mask R-CNN via transfer learning (Detectron2, COCO-pretrained
weights, Colab T4 GPU, data from Roboflow).

- **Fruit model** — apple / banana / orange instance segmentation (with masks).
  Detects apples at 98-99% confidence on held-out images.
- **Kiwifruit detector** — detects kiwifruit at 85-95%. Bounding-box only for
  now (the source dataset had no masks). This closes the motivating benchmark:
  off-the-shelf COCO Mask R-CNN labels a cluster of kiwifruit "teddy bear" at
  88% and cannot judge ripeness -- the whole reason for a custom model.

Approach: start from COCO-pretrained weights, re-initialise the output heads for
the fruit classes, and fine-tune -- so only tens of labelled images per class
are needed. Healthy convergence (total training loss 2.36 -> 0.33 over 300
iterations). Trained weights and notebooks are not committed (large /
environment-specific); this documents the method and results.

## In progress

- Clean instance-segmentation kiwi data (masks, not just boxes).
- Merge all four fruit classes into a single model.
- A parallel ripeness-regression head (multi-task loss), with ripeness labels
  distilled from the existing Claude Vision pipeline.
