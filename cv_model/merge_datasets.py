"""
Merge multiple Roboflow-exported YOLOv8 datasets into a single unified
dataset with one consistent set of class names/indices.

WHY THIS IS NEEDED:
  Each Roboflow dataset you download has its OWN data.yaml with its OWN
  class order (e.g. dataset A might have algae=0, debris=1 while dataset
  B has debris=0, algae=1). You can't just dump the label .txt files
  together - the class numbers would mean different things. This script
  reads each dataset's own data.yaml, remaps every label file to a single
  shared class list you define below, and copies everything into one
  merged/ folder with unique filenames (so images from different
  datasets never overwrite each other).

BEFORE RUNNING:
  1. Download + extract each Roboflow dataset (YOLOv8 export format) into
     its own folder, e.g.:
       ./raw_datasets/imgproc/        (has data.yaml, train/, valid/, ...)
       ./raw_datasets/hab_foam/
       ./raw_datasets/water_pollution/
  2. Edit UNIFIED_CLASSES below to the final class list you want your
     model to learn (this becomes your project's actual classes).
  3. Edit DATASET_DIRS below to point at the folders from step 1.

USAGE:
  python merge_datasets.py --output ./merged_dataset

OUTPUT:
  ./merged_dataset/
    data.yaml
    images/train/*.jpg   labels/train/*.txt
    images/valid/*.jpg   labels/valid/*.txt

  Then train with:
    python train_yolo.py --data ./merged_dataset/data.yaml --epochs 50
"""

import argparse
import os
import shutil

# ---------------------------------------------------------------------------
# 1) EDIT THIS: the final class list your merged model should predict.
#    Keep this matching (or a superset of) whatever fusion_model.py /
#    predict_yolo.py already expect.
# ---------------------------------------------------------------------------
UNIFIED_CLASSES = ["algae", "debris", "discoloration", "clear_water"]

# ---------------------------------------------------------------------------
# 2) EDIT THIS: folders where you extracted each downloaded Roboflow dataset.
#    Each must contain a data.yaml plus train/ (and valid/ or val/) splits.
# ---------------------------------------------------------------------------
DATASET_DIRS = [
    "./raw_datasets/imgproc",
    "./raw_datasets/hab_foam",
    "./raw_datasets/water_pollution",
]

# ---------------------------------------------------------------------------
# 3) OPTIONAL: map a source dataset's class name -> one of UNIFIED_CLASSES.
#    If a source class isn't listed here, the script tries an exact
#    case-insensitive match against UNIFIED_CLASSES; if that also fails,
#    that class's boxes are DROPPED (printed as a warning) rather than
#    guessed at.
#    Add entries here for any class names that don't match exactly, e.g.:
NAME_ALIASES = {
    "clean_water": "clear_water",
    "clean water": "clear_water",
    "algae-foams-cleanwater": "algae",
    "foams": "algae",
    "discolo": "discoloration",
    "debris_on_roof": "debris",
    "plastic": "debris",
}


def read_data_yaml(path):
    """Minimal YAML reader for Roboflow's simple data.yaml (avoids adding
    a pyyaml dependency just for this one field)."""
    names = None
    with open(path, "r") as f:
        text = f.read()
    # Roboflow's data.yaml has a line like: names: ['algae', 'debris', ...]
    # or a YAML list format. Handle both.
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("names:"):
            value = line.split("names:", 1)[1].strip()
            if value.startswith("["):
                names = [n.strip().strip("'\"") for n in value.strip("[]").split(",")]
            break
    if names is None:
        # Fall back to multi-line "names:\n  0: algae\n  1: debris" format
        names = {}
        in_names = False
        for line in text.splitlines():
            if line.strip().startswith("names:"):
                in_names = True
                continue
            if in_names:
                stripped = line.strip()
                if not stripped or ":" not in stripped:
                    break
                idx, val = stripped.split(":", 1)
                try:
                    names[int(idx.strip())] = val.strip().strip("'\"")
                except ValueError:
                    break
        names = [names[i] for i in sorted(names)] if names else []
    return names


def build_class_map(source_names):
    """Returns {source_class_index: unified_class_index or None}."""
    mapping = {}
    lower_unified = {c.lower(): i for i, c in enumerate(UNIFIED_CLASSES)}
    for idx, name in enumerate(source_names):
        key = name.strip().lower()
        target = NAME_ALIASES.get(key, NAME_ALIASES.get(name, None))
        if target is not None:
            mapping[idx] = lower_unified.get(target.lower())
        elif key in lower_unified:
            mapping[idx] = lower_unified[key]
        else:
            print(f"  ! No mapping for class '{name}' - its boxes will be dropped. "
                  f"Add it to NAME_ALIASES if you want to keep it.")
            mapping[idx] = None
    return mapping


def find_split_dirs(dataset_dir):
    """Roboflow exports use train/valid/test, but sometimes val/. Returns
    a dict of {split_name: (images_dir, labels_dir)} for splits that exist."""
    splits = {}
    for split in ["train", "valid", "val", "test"]:
        img_dir = os.path.join(dataset_dir, split, "images")
        lbl_dir = os.path.join(dataset_dir, split, "labels")
        if os.path.isdir(img_dir) and os.path.isdir(lbl_dir):
            norm_split = "valid" if split == "val" else split
            splits[norm_split] = (img_dir, lbl_dir)
    return splits


def merge_one_dataset(dataset_dir, dataset_tag, output_dir):
    data_yaml = os.path.join(dataset_dir, "data.yaml")
    if not os.path.exists(data_yaml):
        print(f"Skipping {dataset_dir}: no data.yaml found")
        return
    source_names = read_data_yaml(data_yaml)
    print(f"\n{dataset_dir}")
    print(f"  source classes: {source_names}")
    class_map = build_class_map(source_names)

    splits = find_split_dirs(dataset_dir)
    if not splits:
        print(f"  ! No train/valid/test image+label folders found, skipping.")
        return

    for split, (img_dir, lbl_dir) in splits.items():
        out_img_dir = os.path.join(output_dir, "images", split)
        out_lbl_dir = os.path.join(output_dir, "labels", split)
        os.makedirs(out_img_dir, exist_ok=True)
        os.makedirs(out_lbl_dir, exist_ok=True)

        images = [f for f in os.listdir(img_dir)
                  if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        copied = 0
        for img_name in images:
            stem, ext = os.path.splitext(img_name)
            lbl_name = stem + ".txt"
            lbl_path = os.path.join(lbl_dir, lbl_name)
            if not os.path.exists(lbl_path):
                continue  # skip images with no label file

            # Remap class indices, dropping any lines for unmapped classes
            new_lines = []
            with open(lbl_path, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if not parts:
                        continue
                    old_cls = int(parts[0])
                    new_cls = class_map.get(old_cls)
                    if new_cls is None:
                        continue
                    new_lines.append(" ".join([str(new_cls)] + parts[1:]))

            if not new_lines:
                continue  # nothing left worth keeping for this image

            unique_stem = f"{dataset_tag}__{stem}"
            shutil.copy2(os.path.join(img_dir, img_name),
                         os.path.join(out_img_dir, unique_stem + ext))
            with open(os.path.join(out_lbl_dir, unique_stem + ".txt"), "w") as f:
                f.write("\n".join(new_lines) + "\n")
            copied += 1

        print(f"  {split}: copied {copied}/{len(images)} images (rest had no usable boxes)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="./merged_dataset")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    for dataset_dir in DATASET_DIRS:
        tag = os.path.basename(os.path.normpath(dataset_dir))
        merge_one_dataset(dataset_dir, tag, args.output)

    # Write the unified data.yaml
    yaml_path = os.path.join(args.output, "data.yaml")
    with open(yaml_path, "w") as f:
        f.write(f"train: images/train\n")
        f.write(f"val: images/valid\n")
        f.write(f"nc: {len(UNIFIED_CLASSES)}\n")
        f.write(f"names: {UNIFIED_CLASSES}\n")

    print(f"\nDone. Merged dataset ready at: {args.output}")
    print(f"Train with:\n  python train_yolo.py --data {yaml_path} --epochs 50")


if __name__ == "__main__":
    main()
