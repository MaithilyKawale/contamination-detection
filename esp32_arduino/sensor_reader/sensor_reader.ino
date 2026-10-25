/*
  AquaScan - ESP32 Sensor Reader (MATCHED TO YOUR CURRENT HARDWARE)
  -----------------------------------------------------------------
  Main ESP32 DevKit V1 reads:
    TDS       -> GPIO34 (analog)
    pH        -> GPIO35 (analog, through 10k/20k divider if PO can exceed 3.3V)
    Turbidity -> GPIO32 (analog, through divider if AO can exceed 3.3V)
    DS18B20   -> GPIO4  (digital, 4.7k pull-up to 3.3V)
    Ultrasonic TRIG -> GPIO27
    Ultrasonic ECHO -> GPIO26 (use divider if ECHO is 5V)

  This firmware POSTs the JSON format expected by:
    dashboard/app.py -> POST /api/sensor-data

  IMPORTANT:
    - DO and ORP are not installed in your current hardware, so they are sent as null.
    - Replace WiFi credentials and SERVER_URL before flashing.
    - Calibrate TDS, pH and turbidity with known standards before relying on values.
*/
#include <WiFi.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>

const char* WIFI_SSID     = "pitaji ko bolo recharge karwaye";
const char* WIFI_PASSWORD = "12345678";
// Example: http://192.168.1.5:5000/api/sensor-data
const char* SERVER_URL    = "http://10.143.10.33:5000/api/sensor-data";

/*const char* WIFI_SSID     = "Chanu";
const char* WIFI_PASSWORD = "ilym2914";
// Example: http://192.168.1.5:5000/api/sensor-data
const char* SERVER_URL    = "http://10.109.46.33:5000/api/sensor-data";*/

const unsigned long SEND_INTERVAL_MS = 5000;

#define TDS_PIN       34
#define PH_PIN        35
#define TURBIDITY_PIN 32
#define TEMP_PIN       4
#define TRIG_PIN      27
#define ECHO_PIN      26

// If you use 10k from sensor output to ADC and 20k from ADC to GND,
// original sensor voltage = ADC voltage * 1.5.
const float PH_DIVIDER_RATIO = 1.5f;
const float TURBIDITY_DIVIDER_RATIO = 1.5f;
const float ECHO_DIVIDER_RATIO = 1.5f;

// Placeholder pH two-point calibration values. Recalculate after measuring
// your actual pH interface at pH 4 and pH 7.
const float PH_SLOPE = -5.70f;
const float PH_OFFSET = 21.34f;

OneWire oneWire(TEMP_PIN);
DallasTemperature tempSensor(&oneWire);
unsigned long lastSendTime = 0;

float adcVoltage(int pin) {
  return analogRead(pin) * (3.3f / 4095.0f);
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(500);

  // Show which networks the ESP32 can actually see
  Serial.println("Scanning networks...");
  int n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    Serial.printf("  %d: %s (signal %d dBm)\n", i + 1, WiFi.SSID(i).c_str(), WiFi.RSSI(i));
  }
  Serial.printf("Trying to join: [%s]\n", WIFI_SSID);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 30) {
    delay(500);
    Serial.print('.');
    tries++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("ESP32 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.printf("FAILED, status code: %d\n", WiFi.status());
    delay(3000);
    ESP.restart();
  }
}

float readTDS() {
  float sum = 0;
  for (int i = 0; i < 20; i++) {
    sum += analogReadMilliVolts(TDS_PIN);
    delay(10);
  }
  const float v = (sum / 20.0f) / 1000.0f;   // volts, averaged
  float tds = (133.42f * v * v * v - 255.86f * v * v + 857.39f * v) * 0.5f;
  return max(0.0f, tds);
}

float readTurbidity() {
  const float adcV = adcVoltage(TURBIDITY_PIN);
  const float sensorV = min(5.0f, adcV * TURBIDITY_DIVIDER_RATIO);
  // Starting approximation only; calibrate with known turbidity samples.
  float ntu = (3.3f - sensorV) * 100.0f;
  return max(0.0f, ntu);
}

float readTemperature() {
  tempSensor.requestTemperatures();
  const float t = tempSensor.getTempCByIndex(0);
  return (t == DEVICE_DISCONNECTED_C) ? NAN : t;
}

float readPH() {
  const float adcV = adcVoltage(PH_PIN);
  const float probeV = adcV * PH_DIVIDER_RATIO;
  return PH_SLOPE * probeV + PH_OFFSET;
}

float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // Timeout prevents the loop from blocking if the sensor is disconnected.
  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000UL);
  if (duration == 0) return NAN;
  const float echoV = 0.0f; // Voltage divider is passive; measurement is only for documentation.
  (void)echoV;
  return (duration * 0.0343f) / 2.0f;
}

String jsonNumber(float value) {
  if (isnan(value) || isinf(value)) return "null";
  return String(value, 2);
}

void sendData(float tds, float turbidity, float temperature, float ph, float distanceCm) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected; skipping POST.");
    return;
  }

  HTTPClient http;
  http.begin(SERVER_URL);
  http.setTimeout(4000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-ID", "aquascan-main-01");

  String payload = "{";
  payload += "\"tds\":" + jsonNumber(tds) + ",";
  payload += "\"turbidity\":" + jsonNumber(turbidity) + ",";
  payload += "\"temperature\":" + jsonNumber(temperature) + ",";
  payload += "\"ph\":" + jsonNumber(ph) + ",";
  payload += "\"water_level_distance_cm\":" + jsonNumber(distanceCm);
  payload += "}";

  int code = http.POST(payload);
  Serial.printf("POST %d: %s\n", code, payload.c_str());
  if (code != 200) {
    Serial.println("Server reply: " + http.getString());
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(500);

  analogReadResolution(12);
  analogSetPinAttenuation(TDS_PIN, ADC_11db);
  analogSetPinAttenuation(PH_PIN, ADC_11db);
  analogSetPinAttenuation(TURBIDITY_PIN, ADC_11db);

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);

  tempSensor.begin();
  connectWiFi();
}

void loop() {
  if (millis() - lastSendTime >= SEND_INTERVAL_MS) {
    const float tds = readTDS();
    const float turbidity = readTurbidity();
    const float temperature = readTemperature();
    const float ph = readPH();
    const float distanceCm = readDistanceCm();

    Serial.printf(
      "TDS %.2f ppm | Turbidity %.2f NTU | Temp %.2f C | pH %.2f | Level distance %.2f cm\n",
      tds, turbidity, temperature, ph, distanceCm
    );

    sendData(tds, turbidity, temperature, ph, distanceCm);
    lastSendTime = millis();
  }
}
