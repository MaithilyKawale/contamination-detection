"""
Loads the trained CV model and predicts contamination class + confidence
for a single water sample image. Used by the dashboard backend.
"""

import json
import os

import numpy as np
import tensorflow as tf
from tensorflow.keras.applications.mobilenet_v2 import preprocess_input

MODEL_PATH = os.path.join(os.path.dirname(__file__), "water_cv_model.h5")
CLASS_NAMES_PATH = os.path.join(os.path.dirname(__file__), "class_names.json")

_model = None
_class_names = None


def _load():
    global _model, _class_names
    if _model is None:
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                "water_cv_model.h5 not found. Train it first: "
                "python train_model.py --data_dir ./dataset"
            )
        _model = tf.keras.models.load_model(MODEL_PATH)
        with open(CLASS_NAMES_PATH) as f:
            _class_names = json.load(f)
    return _model, _class_names


def predict_image(image_path):
    """
    Returns a dict like:
      {"class": "contaminated", "confidence": 0.87,
       "all_scores": {"clean": 0.13, "contaminated": 0.87}}
    """
    model, class_names = _load()

    img = tf.keras.utils.load_img(image_path, target_size=(224, 224))
    img_array = tf.keras.utils.img_to_array(img)
    img_array = np.expand_dims(img_array, axis=0)
    img_array = preprocess_input(img_array)

    predictions = model.predict(img_array, verbose=0)[0]
    predicted_idx = int(np.argmax(predictions))

    return {
        "class": class_names[predicted_idx],
        "confidence": float(predictions[predicted_idx]),
        "all_scores": {name: float(score) for name, score in zip(class_names, predictions)},
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python predict.py <path_to_image>")
    else:
        result = predict_image(sys.argv[1])
        print(json.dumps(result, indent=2))
