"""
Splits a flat dataset (one folder per class, all images together)
into train/ and val/ folders needed by train_model.py.

BEFORE:
  raw_dataset/
    clean/            (all clean images)
    contaminated/      (all contaminated images)

AFTER (creates a new ./dataset folder, doesn't touch original):
  dataset/
    train/clean/  train/contaminated/
    val/clean/    val/contaminated/

USAGE:
  python split_dataset.py --input ./raw_dataset --output ./dataset --val_split 0.2
"""

import argparse
import os
import random
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Folder with one subfolder per class")
    parser.add_argument("--output", default="./dataset")
    parser.add_argument("--val_split", type=float, default=0.2)
    args = parser.parse_args()

    random.seed(42)
    classes = [d for d in os.listdir(args.input) if os.path.isdir(os.path.join(args.input, d))]
    print(f"Found classes: {classes}")

    for cls in classes:
        src_folder = os.path.join(args.input, cls)
        images = [f for f in os.listdir(src_folder) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        random.shuffle(images)

        split_idx = int(len(images) * (1 - args.val_split))
        train_imgs, val_imgs = images[:split_idx], images[split_idx:]

        for subset, imgs in [("train", train_imgs), ("val", val_imgs)]:
            dest_folder = os.path.join(args.output, subset, cls)
            os.makedirs(dest_folder, exist_ok=True)
            for img in imgs:
                shutil.copy2(os.path.join(src_folder, img), os.path.join(dest_folder, img))

        print(f"{cls}: {len(train_imgs)} train, {len(val_imgs)} val")

    print(f"\nDone. Dataset ready at {args.output}")


if __name__ == "__main__":
    main()
