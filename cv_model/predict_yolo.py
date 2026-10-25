"""
Runs the trained YOLOv8 model on a water sample image and returns
detected contamination indicators (e.g. algae, debris, discoloration
patches) with confidence scores. Used by the dashboard backend.
"""

import os

MODEL_PATH = os.path.join(os.path.dirname(__file__), "water_yolo_model.pt")

_model = None


def _load():
    global _model
    if _model is None:
        from ultralytics import YOLO
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                "water_yolo_model.pt not found. Train it first: "
                "python train_yolo.py --data /path/to/data.yaml "
                "then copy runs/detect/train/weights/best.pt here as water_yolo_model.pt"
            )
        _model = YOLO(MODEL_PATH)
    return _model


def predict_image(image_path, confidence_threshold=0.4):
    """
    Returns a dict like:
      {
        "detections": [
          {"class": "algae", "confidence": 0.82, "box": [x1, y1, x2, y2]},
          {"class": "debris", "confidence": 0.55, "box": [x1, y1, x2, y2]}
        ],
        "image_width": 640,
        "image_height": 480,
        "is_contaminated": true,
        "top_confidence": 0.82
      }
    Box coordinates are in original image pixels (not normalized) so
    the frontend can draw them directly over the uploaded image.
    If nothing is detected above the confidence threshold, "detections"
    will be an empty list and "is_contaminated" will be false, which
    the fusion layer treats as "no visual contamination flagged".
    """
    model = _load()
    results = model.predict(source=image_path, conf=confidence_threshold, verbose=False)

    detections = []
    img_width, img_height = 0, 0
    for result in results:
        img_height, img_width = result.orig_shape
        for box in result.boxes:
            class_id = int(box.cls[0])
            class_name = model.names[class_id]
            confidence = float(box.conf[0])
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
            detections.append({
                "class": class_name,
                "confidence": confidence,
                "box": [x1, y1, x2, y2],
            })

    detections.sort(key=lambda d: d["confidence"], reverse=True)

    return {
        "detections": detections,
        "image_width": img_width,
        "image_height": img_height,
        "is_contaminated": len(detections) > 0,
        "top_confidence": detections[0]["confidence"] if detections else 0.0,
    }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) != 2:
        print("Usage: python predict_yolo.py <path_to_image>")
    else:
        result = predict_image(sys.argv[1])
        print(json.dumps(result, indent=2))
