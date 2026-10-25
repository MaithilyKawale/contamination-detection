/*
  AquaScan - ESP32-CAM (AI-Thinker + RHYX M21-45 / GC2145)
  ---------------------------------------------------------
  GC2145 sensor JPEG output support nahi karta, isliye:
    1. Camera se RGB565 frame (QVGA 320x240) liya jata hai
    2. ESP32 khud frame2jpg() se JPEG banata hai
    3. JPEG Flask backend ko POST hota hai

  Backend endpoint:
    POST /api/camera-frame

  Flash karne se pehle WIFI_SSID, WIFI_PASSWORD, SERVER_HOST badlo.

  Sirf upload ke time: GPIO0 -> GND.
  Upload ke baad jumper hatao aur reset dabao.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_camera.h"
#include "img_converters.h"

const char* WIFI_SSID     = "pitaji ko bolo recharge karwaye";
const char* WIFI_PASSWORD = "12345678";
const char* SERVER_HOST   = "10.143.10.33:5000";
const unsigned long SEND_INTERVAL_MS = 10000;


// AI-Thinker ESP32-CAM pin map
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

unsigned long lastSend = 0;

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print('.');
  }
  Serial.println();
  Serial.print("ESP32-CAM IP: ");
  Serial.println(WiFi.localIP());
}

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 10000000;

  // GC2145 (RHYX M21-45): JPEG nahi, sirf RGB565
  config.pixel_format = PIXFORMAT_RGB565;
  config.frame_size = FRAMESIZE_QVGA;   // 320x240
  config.jpeg_quality = 12;
  config.fb_count = 1;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor) {
    Serial.printf("Sensor PID: 0x%x\n", sensor->id.PID);
    sensor->set_brightness(sensor, 0);
    sensor->set_contrast(sensor, 0);
    sensor->set_saturation(sensor, 0);
  }
  return true;
}

void sendJpegToBackend(camera_fb_t* fb) {
  if (!fb || WiFi.status() != WL_CONNECTED) return;

  // RGB565 frame ko JPEG mein convert karo
  uint8_t* jpgBuf = NULL;
  size_t jpgLen = 0;
  if (!frame2jpg(fb, 80, &jpgBuf, &jpgLen)) {
    Serial.println("JPEG conversion failed.");
    return;
  }

  HTTPClient http;
  String url = String("http://") + SERVER_HOST + "/api/camera-frame";
  http.begin(url);
  http.setTimeout(7000);
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("X-Device-ID", "aquascan-cam-01");
  http.addHeader("X-Image-Width", String(fb->width));
  http.addHeader("X-Image-Height", String(fb->height));

  int code = http.POST(jpgBuf, jpgLen);
  Serial.printf("JPEG POST: %d, %u bytes\n", code, (unsigned)jpgLen);
  http.end();

  free(jpgBuf);  // frame2jpg ne malloc kiya tha
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  if (!initCamera()) {
    while (true) delay(1000);
  }

  connectWiFi();
}

void loop() {
  if (millis() - lastSend >= SEND_INTERVAL_MS) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Camera capture failed.");
    } else {
      sendJpegToBackend(fb);
      esp_camera_fb_return(fb);
    }
    lastSend = millis();
  }
  delay(20);
}
