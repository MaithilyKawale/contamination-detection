"""
Evaluate the trained YOLO model against a labeled validation/test set
and print the metrics you need for the project report / viva:
precision, recall, mAP50, mAP50-95, and a per-class breakdown.
Also saves a confusion matrix image.

USAGE:
  python evaluate_model.py --data /path/to/data.yaml
  python evaluate_model.py --data /path/to/data.yaml --model water_yolo_model.pt --split test

data.yaml must point to a labeled set (images + YOLO-format labels)
that the model was NOT trained on - ideally the "test" split you set
aside with split_dataset.py or merge_datasets.py. Evaluating on the
training data itself gives inflated, meaningless numbers.

OUTPUT:
  Prints a summary table to the console.
  Saves plots (confusion matrix, PR curve, etc.) under runs/val/ -
  screenshot these for your project report.
"""

import argparse
import os

from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser(description="Evaluate the water quality YOLO model")
    parser.add_argument("--data", required=True, help="Path to data.yaml for the validation/test set")
    parser.add_argument(
        "--model",
        default=os.path.join(os.path.dirname(__file__), "water_yolo_model.pt"),
        help="Path to the trained model (.pt)",
    )
    parser.add_argument("--split", default="val", choices=["val", "test"], help="Which split in data.yaml to evaluate")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold used during evaluation")
    args = parser.parse_args()

    if not os.path.exists(args.model):
        print(f"Model not found at {args.model}")
        return

    model = YOLO(args.model)
    metrics = model.val(data=args.data, split=args.split, conf=args.conf, plots=True)

    print("\n========== OVERALL METRICS ==========")
    print(f"  mAP50      : {metrics.box.map50:.4f}")
    print(f"  mAP50-95   : {metrics.box.map:.4f}")
    print(f"  Precision  : {metrics.box.mp:.4f}")
    print(f"  Recall     : {metrics.box.mr:.4f}")

    print("\n========== PER-CLASS METRICS ==========")
    class_names = model.names
    try:
        for i, class_id in enumerate(metrics.box.ap_class_index):
            name = class_names[int(class_id)]
            ap50 = metrics.box.ap50[i]
            print(f"  {name:20s}  AP50={ap50:.4f}")
    except Exception:
        print("  (per-class AP not available for this ultralytics version - see plots instead)")

    print(f"\nPlots (confusion matrix, PR curve, etc.) saved under: {metrics.save_dir}")
    print("Screenshot these for your report / viva slides.")


if __name__ == "__main__":
    main()
