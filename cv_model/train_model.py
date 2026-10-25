"""
Water Contamination Detection - CV Model Training
---------------------------------------------------
Trains an image classifier (transfer learning on MobileNetV2) to
classify water sample images into contamination categories.

EXPECTED DATASET STRUCTURE:
  dataset/
    train/
      clean/          <- put clean water images here
      contaminated/    <- put contaminated water images here
    val/
      clean/
      contaminated/

If your dataset only has ONE folder (not split into train/val), run
split_dataset.py first (included in this folder) to auto-split it
80/20.

If you have MORE than 2 classes (e.g. "clean", "slightly_turbid",
"contaminated"), just make more subfolders with those names - the
script auto-detects classes from folder names.

USAGE:
  pip install tensorflow pillow
  python train_model.py --data_dir ./dataset --epochs 15

OUTPUT:
  water_cv_model.h5       <- trained model
  class_names.json        <- maps model output index -> class name
"""

import argparse
import json
import os

import tensorflow as tf
from tensorflow.keras import layers, models
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.applications.mobilenet_v2 import preprocess_input

IMG_SIZE = (224, 224)
BATCH_SIZE = 16


def build_model(num_classes):
    base_model = MobileNetV2(
        input_shape=IMG_SIZE + (3,),
        include_top=False,
        weights="imagenet",
    )
    base_model.trainable = False  # freeze base for transfer learning

    inputs = tf.keras.Input(shape=IMG_SIZE + (3,))
    x = preprocess_input(inputs)
    x = base_model(x, training=False)
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dropout(0.3)(x)
    x = layers.Dense(64, activation="relu")(x)
    outputs = layers.Dense(num_classes, activation="softmax")(x)

    model = models.Model(inputs, outputs)
    model.compile(
        optimizer="adam",
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_dir", default="./dataset")
    parser.add_argument("--epochs", type=int, default=15)
    args = parser.parse_args()

    train_dir = os.path.join(args.data_dir, "train")
    val_dir = os.path.join(args.data_dir, "val")

    if not os.path.exists(train_dir):
        raise FileNotFoundError(
            f"Couldn't find {train_dir}. See the docstring at the top "
            f"of this file for the expected folder structure, or run "
            f"split_dataset.py first."
        )

    train_ds = tf.keras.utils.image_dataset_from_directory(
        train_dir, image_size=IMG_SIZE, batch_size=BATCH_SIZE
    )
    val_ds = tf.keras.utils.image_dataset_from_directory(
        val_dir, image_size=IMG_SIZE, batch_size=BATCH_SIZE
    )

    class_names = train_ds.class_names
    print(f"Detected classes: {class_names}")

    # Save class name mapping for use during inference
    with open("class_names.json", "w") as f:
        json.dump(class_names, f)

    # Basic data augmentation to help with a small dataset
    augmentation = tf.keras.Sequential([
        layers.RandomFlip("horizontal"),
        layers.RandomRotation(0.1),
        layers.RandomZoom(0.1),
    ])
    train_ds = train_ds.map(lambda x, y: (augmentation(x, training=True), y))

    train_ds = train_ds.prefetch(tf.data.AUTOTUNE)
    val_ds = val_ds.prefetch(tf.data.AUTOTUNE)

    model = build_model(num_classes=len(class_names))
    model.summary()

    early_stop = tf.keras.callbacks.EarlyStopping(
        monitor="val_accuracy", patience=4, restore_best_weights=True
    )

    history = model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=args.epochs,
        callbacks=[early_stop],
    )

    model.save("water_cv_model.h5")
    print("\nSaved model to water_cv_model.h5")
    print("Saved class names to class_names.json")

    final_val_acc = max(history.history["val_accuracy"])
    print(f"Best validation accuracy: {final_val_acc:.2%}")


if __name__ == "__main__":
    main()
