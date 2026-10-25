"""
Water Contamination Detection - YOLOv8 Object Detection Training
--------------------------------------------------------------------
Trains a YOLOv8 model on your labeled dataset (images/ + labels/ in
YOLO format, with a data.yaml describing the classes).

REQUIREMENTS:
  pip install ultralytics

YOUR DATA.YAML SHOULD LOOK SOMETHING LIKE:
  train: ../images/train
  val: ../images/val
  nc: 3
  names: ['algae', 'debris', 'discoloration']   <- your actual class names

  (If your data.yaml uses a different structure, that's fine too -
  YOLO is flexible, just make sure the paths inside it point to the
  right image folders relative to where you run this script from.)

USAGE:
  python train_yolo.py --data /path/to/data.yaml --epochs 50

OUTPUT:
  Trained weights saved automatically to:
  runs/detect/train/weights/best.pt
  Copy that file into this cv_model/ folder as "water_yolo_model.pt"
  so the dashboard can find it.
"""

import argparse
import shutil


def main():
    from ultralytics import YOLO

    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="Path to your data.yaml")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--model", default="yolov8n.pt", help="Base model (n=nano, fastest, good for small datasets/low-end laptops)")
    args = parser.parse_args()

    model = YOLO(args.model)  # starts from pretrained COCO weights (transfer learning)

    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=640,
        batch=8,
        patience=10,  # stop early if no improvement
    )

    # Auto-copy best weights to a predictable location for predict_yolo.py
    best_weights = "runs/detect/train/weights/best.pt"
    dest = "water_yolo_model.pt"
    try:
        shutil.copy(best_weights, dest)
        print(f"\nCopied best weights to {dest} - dashboard will pick this up automatically.")
    except FileNotFoundError:
        print(f"\nCouldn't auto-copy weights. Find them at {best_weights} and "
              f"manually copy/rename to {dest}")

    # Run validation and print metrics
    metrics = model.val()
    print(f"\nmAP50: {metrics.box.map50:.2%}")


if __name__ == "__main__":
    main()
