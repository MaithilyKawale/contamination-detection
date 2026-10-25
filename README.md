# Intelligent Water Quality Monitoring & Contamination Detection

Final year project — combines IoT sensors (TDS, turbidity, pH,
temperature, dissolved oxygen, ORP) with a Computer Vision model to
detect water contamination, fused into one final safety verdict
shown on a live web dashboard.

## Hardware bill of materials (~₹17,600)

| Category | Items | Approx. cost |
|---|---|---|
| Sensors | ESP32 DevKit, DFRobot TDS, DFRobot Turbidity, DS18B20 temp, **Atlas Scientific pH probe (upgraded)** + buffers, DFRobot DO sensor, DFRobot ORP sensor | ₹9350 |
| CV camera | ESP32-CAM + FTDI programmer + lens mount | ₹1050 |
| Automation & alerts | Relay module, solenoid valve, mini pump, buzzer+RGB LED, I2C LCD, SD card module+card | ₹1860 |
| Power & build | 12V adapter, buck converter, 18650 battery+charger, **IP67 polycarbonate enclosure (upgraded)**, PCB, wiring/connectors | ₹2550 |
| Extra sensor | Ultrasonic water level sensor | ₹450 |
| Redundancy/buffer | Spare ESP32, DO/ORP calibration solutions, contingency | ₹2150 |

Upgraded from the base ₹15k build: the pH probe (Atlas Scientific,
better long-term stability/less drift) and the enclosure (IP67
polycarbonate, better weatherproofing) — both worth the extra spend
since they directly affect reading accuracy and durability.

See chat history / project notes for the full itemized list if needed
when actually ordering.

## Folder structure

```
water_quality_project/
├── esp32_arduino/
│   └── sensor_reader.ino      # ESP32 code: reads sensors, sends to dashboard
├── cv_model/
│   ├── train_yolo.py          # trains YOLOv8 on your images+labels dataset  <- USE THIS
│   ├── predict_yolo.py        # runs YOLO inference on one image             <- USE THIS
│   ├── evaluate_model.py      # precision/recall/mAP + confusion matrix for your report <- USE THIS
│   ├── merge_datasets.py      # merges multiple Roboflow exports into 4 clean classes
│   ├── split_dataset.py       # (unused, for classification-style datasets only)
│   ├── train_model.py         # (unused, for classification-style datasets only)
│   └── predict.py             # (unused, for classification-style datasets only)
├── fusion/
│   └── fusion_model.py        # combines sensor + CV output into final verdict + recommendations
├── dashboard/
│   ├── app.py                 # Flask backend (dummy data + real ESP32 + CV + fusion + alerts)
│   ├── db.py                  # SQLite persistence (readings + verdict history survive restarts)
│   ├── data/aquascan.db       # auto-created on first run
│   ├── templates/index.html   # dashboard page
│   └── static/                # CSS + JS
├── requirements.txt
└── README.md
```

**Note:** since your dataset is labeled in YOLO format (images/ + labels/ +
a data.yaml/classes file), use `train_yolo.py` and `predict_yolo.py`.
The `train_model.py`/`predict.py`/`split_dataset.py` files are a
classification-style alternative kept in case you ever get a
folder-per-class dataset instead — you can ignore them.

## Setup

```bash
cd water_quality_project
pip install -r requirements.txt
```

## Step-by-step order to build this out

### 1. Run the dashboard (waits for real ESP32 data by default)
```bash
cd dashboard
python app.py
```
Open `http://localhost:5000`. The sensor cards will show `—` until
your real ESP32 starts sending data — this is expected since dummy
data is now switched off by default.

If you want to test the UI without hardware connected, open
`dashboard/app.py` and set `USE_DUMMY_DATA = True` near the top,
then restart. Set it back to `False` once your ESP32 is ready.

### 2. Train the YOLO model on your dataset
Your dataset is already in YOLO format (images/ + labels/ + a
data.yaml or classes.txt). Just point the training script at your
data.yaml:
```bash
cd cv_model
pip install ultralytics
python train_yolo.py --data /path/to/your/data.yaml --epochs 50
```
This trains a YOLOv8-nano model (fast, works fine on a laptop CPU,
good for small datasets). When it finishes, it auto-copies the best
weights to `cv_model/water_yolo_model.pt`. The dashboard picks this
up automatically — restart `app.py` after training.

If your data.yaml doesn't already have `train:`/`val:` paths split
out, Ultralytics also accepts just pointing at one `images:` folder
and will handle it — check your data.yaml's existing format first
since your dataset apparently already came with one.

### 3. Test image upload + verdict on the dashboard
With the model trained and the dashboard running, upload a water
sample photo on the page. It'll run the CV model, combine the result
with the latest sensor reading (dummy or real), and show a verdict:
**Safe / Needs Treatment / Contaminated**.

### 4. Connect your current real ESP32 hardware
1. Open `esp32_arduino/sensor_reader.ino` in Arduino IDE.
2. Fill in WiFi SSID/password and the PC running Flask in `SERVER_URL`.
3. Current wiring: TDS GPIO34, pH GPIO35, turbidity GPIO32, DS18B20 GPIO4,
   ultrasonic TRIG GPIO27 and ECHO GPIO26. DO and ORP are not installed,
   so the firmware sends them as `null` and the dashboard shows `—`.
4. If a 5V sensor output can exceed 3.3V, use the voltage divider described
   in the firmware before connecting it to an ESP32 ADC/GPIO.
5. Calibrate TDS, pH and turbidity with known reference samples before
   treating the displayed values as measurement-grade.
6. Flash it. POSTs to `/api/sensor-data` immediately update the dashboard.

### 5. Connect the ESP32-CAM
1. Open `esp32_arduino/esp32_cam.ino`.
2. Fill in the same WiFi credentials and set `SERVER_HOST` to the PC running
   Flask, for example `192.168.1.5:5000`.
3. The firmware uses `PIXFORMAT_JPEG`, so the OV2640 produces JPEG directly.
   It POSTs the JPEG bytes to `/api/camera-frame` every 10 seconds.
4. During flashing, connect GPIO0 to GND; remove that connection after upload.
5. The dashboard displays the latest JPEG in the **ESP32-CAM** panel and
   **Capture & Analyze** sends that frame through the existing YOLO + fusion pipeline.

## Recent additions (dashboard v2)

- **Recommendations** — every verdict now includes plain-language advice
  per parameter (e.g. "TDS high — use an RO filter"), shown in the
  "What to do" section of the verdict panel.
- **Previous tests log** — the dashboard shows your last 20 analyzed
  samples with thumbnail, verdict badge, and issues — data persists
  in SQLite (`dashboard/data/aquascan.db`), survives server restarts.
- **Sensor readings table** — toggle between the trend chart and a
  raw table view of the last 50 readings; download either as CSV
  (buttons next to each panel) for your report.
- **Confidence slider** — adjust the CV model's detection threshold
  live from the dashboard before running an analysis (useful for
  demoing sensitivity vs. false positives in the viva).
- **Contamination alert** — an on-screen banner + browser notification
  fires when a test comes back "Contaminated". Optional email alert
  via SMTP — see the docstring at the top of `dashboard/app.py` for
  the environment variables to set (disabled by default, since it
  needs your own SMTP credentials which can't be tested from here).
- **Model evaluation** — `cv_model/evaluate_model.py` computes
  precision/recall/mAP50/mAP50-95 + a confusion matrix against a
  labeled validation set, for the accuracy numbers your report needs:
  ```bash
  cd cv_model
  python evaluate_model.py --data /path/to/data.yaml
  ```

## How the fusion logic works

`fusion/fusion_model.py` has two modes:
- **Rule-based** (used by default): compares TDS/turbidity against
  standard safe thresholds (BIS 10500:2012, simplified) and checks if
  the CV model flagged contamination. Easy to explain in your report
  and viva.
- **ML-based** (optional upgrade): trains a small Random Forest on
  labeled examples if you want to show a "real" trained model doing
  the fusion instead of if/else rules. Only bother with this if you
  have time left after the rule-based version works end-to-end —
  it needs a labeled CSV of past readings + verdicts to train on.

## Dashboard features

- Live sensor cards that turn amber/red when a reading crosses the
  safe threshold (TDS > 500 ppm, Turbidity > 5 NTU)
- Live history chart of the last 50 readings
- Image upload with a preview showing detected contamination objects
  as bounding boxes drawn directly on the image
- Verdict panel with icon + color (green = Safe, amber = Needs
  Treatment, red = Contaminated)

## Suggested report/viva talking points

- Why fusion matters: sensors alone can miss visual contamination
  (algae, discoloration) that doesn't affect TDS; CV alone can't
  measure dissolved solids. Combining both gives a more complete
  picture.
- 6-parameter monitoring (TDS, turbidity, pH, temperature, dissolved
  oxygen, ORP) gives a research-grade picture of water health — low
  DO and abnormal ORP are strong pollution/sewage indicators that
  TDS/turbidity alone would miss.
- TDS/turbidity/pH thresholds are based on BIS 10500:2012 drinking
  water standards (cite this in your report).
- Transfer learning (YOLOv8, pretrained on COCO) was used for the CV
  model since it's lightweight and works well with a small dataset —
  appropriate given time/data constraints of a student project.


## ESP32-to-dashboard data flow

```
Main ESP32 -> POST /api/sensor-data -> Flask -> SQLite -> frontend cards/history
ESP32-CAM -> POST /api/camera-frame -> Flask -> latest_camera.jpg -> frontend
                                      -> /api/analyze-camera -> YOLO + fusion -> verdict
```

The current hardware is configured for the sensors actually available: TDS, pH,
turbidity, DS18B20 and ultrasonic level. DO/ORP remain optional.
