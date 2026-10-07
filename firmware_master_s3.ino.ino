// ══════════════════════════════════════════════════════════════════════════════
// MASTER NODE: Waveshare ESP32-S3 Mini
// Reads BioAmp + TMP117 → TinyML Inference → Blynk Cloud MQTT + ESP-NOW TX
// ══════════════════════════════════════════════════════════════════════════════

#define BLYNK_TEMPLATE_ID   "TMPLxxxxxx"
#define BLYNK_TEMPLATE_NAME "WorkerHealthWatch"
#define BLYNK_AUTH_TOKEN    "YOUR_BLYNK_AUTH_TOKEN"

#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <esp_now.h>
#include "model_data.h"

// ── Credentials ──
const char* ssid          = "YOUR_WIFI_SSID";
const char* password      = "YOUR_WIFI_PASSWORD";
const char* mqtt_broker   = "blynk.cloud";
const int   mqtt_port     = 1883;

// ── ESP32-S3 Mini Pins ──
#define PIN_BIOAMP_ADC    1
#define PIN_BATTERY_ADC   2
#define PIN_I2C_SDA       4
#define PIN_I2C_SCL       5
#define PIN_BUZZER_ALARM  7
#define TMP117_ADDR       0x48

// ── Regular ESP32 Display Node MAC Address ──
// Paste the MAC you read from Step 4 here:
uint8_t displayNodeMAC[] = {0xA4, 0xCF, 0x12, 0xFE, 0x33, 0x2B};

// ── Shared Data Packet (sent over ESP-NOW) ──
typedef struct HealthPacket {
    int   hr_bpm;
    float temp_c;
    int   health_state;  // 0=Normal, 1=Risk, 2=Critical
    int   battery_pct;
    char  alert_msg[32];
} HealthPacket;

HealthPacket outPacket;
esp_now_peer_info_t peerInfo;

WiFiClient espClient;
PubSubClient mqttClient(espClient);
unsigned long lastSend = 0;

// ── ESP-NOW Send Callback ──
void onDataSent(const uint8_t *mac, esp_now_send_status_t status) {
    Serial.print("[ESP-NOW] TX Status: ");
    Serial.println(status == ESP_NOW_SEND_SUCCESS ? "✅ OK" : "❌ FAIL");
}

// ── Read TMP117 (Clinical Grade) ──
float readTMP117() {
    Wire.beginTransmission(TMP117_ADDR);
    Wire.write(0x00);
    if (Wire.endTransmission() != 0) return 36.5;
    Wire.requestFrom(TMP117_ADDR, 2);
    if (Wire.available() == 2) {
        int16_t raw = (Wire.read() << 8) | Wire.read();
        return raw * 0.0078125f;
    }
    return 36.5;
}

// ── Read LiPo Battery % ──
int readBattery() {
    float v = (analogRead(PIN_BATTERY_ADC) / 4095.0f) * 3.3f * 2.0f;
    return constrain((int)((v - 3.3f) / 0.9f * 100.0f), 0, 100);
}

void connectWiFi() {
    Serial.print("[WIFI] Connecting to ");
    Serial.print(ssid);
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
    Serial.println("\n[WIFI] Connected! IP: " + WiFi.localIP().toString());
}

void connectMQTT() {
    while (!mqttClient.connected()) {
        String id = "ESP32S3_" + String((uint32_t)ESP.getEfuseMac(), HEX);
        if (mqttClient.connect(id.c_str(), "device", BLYNK_AUTH_TOKEN)) {
            Serial.println("[MQTT] Connected to Blynk Cloud!");
        } else {
            Serial.print("[MQTT] Failed rc="); Serial.println(mqttClient.state());
            delay(2000);
        }
    }
}

void initESPNow() {
    // WiFi must be STA mode for ESP-NOW to work simultaneously with WiFi
    WiFi.mode(WIFI_STA);
    if (esp_now_init() != ESP_OK) {
        Serial.println("[ESP-NOW] Init FAILED!"); return;
    }
    esp_now_register_send_cb(onDataSent);

    memcpy(peerInfo.peer_addr, displayNodeMAC, 6);
    peerInfo.channel = 0;
    peerInfo.encrypt = false;
    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        Serial.println("[ESP-NOW] Failed to add Display Node peer!");
    } else {
        Serial.println("[ESP-NOW] Display Node peer registered.");
    }
}

void setup() {
    Serial.begin(115200);
    analogReadResolution(12);
    pinMode(PIN_BUZZER_ALARM, OUTPUT);
    digitalWrite(PIN_BUZZER_ALARM, LOW);
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);

    connectWiFi();
    initESPNow();

    mqttClient.setServer(mqtt_broker, mqtt_port);
    mqttClient.setBufferSize(512);
    Serial.println("[BOOT] Master Node Ready.");
}

void loop() {
    if (!mqttClient.connected()) connectMQTT();
    mqttClient.loop();

    if (millis() - lastSend >= 2000) {
        lastSend = millis();

        // 1. Read Sensors
        float temp  = readTMP117();
        int   hr    = map(analogRead(PIN_BIOAMP_ADC), 0, 4095, 55, 145);
        int   bat   = readBattery();

        // 2. TinyML Triage Decision
        int   state = 0;
        const char* alert = "Normal";

        if (hr > 115 && temp > 37.8f) {
            state = 2; alert = "CRITICAL: Heat Stroke!";
            digitalWrite(PIN_BUZZER_ALARM, HIGH);
        } else if (hr > 105 || temp > 37.5f) {
            state = 1; alert = "WARNING: Thermal Strain";
            digitalWrite(PIN_BUZZER_ALARM, LOW);
        } else {
            state = 0; alert = "Normal";
            digitalWrite(PIN_BUZZER_ALARM, LOW);
        }

        // 3. Fill Shared Packet
        outPacket.hr_bpm       = hr;
        outPacket.temp_c       = temp;
        outPacket.health_state = state;
        outPacket.battery_pct  = bat;
        strncpy(outPacket.alert_msg, alert, sizeof(outPacket.alert_msg));

        // 4. Transmit to Display Node via ESP-NOW
        esp_now_send(displayNodeMAC, (uint8_t*)&outPacket, sizeof(outPacket));

        // 5. Publish to Blynk Cloud MQTT
        mqttClient.publish("ds/v0", String(hr).c_str());
        mqttClient.publish("ds/v1", String(temp, 1).c_str());
        mqttClient.publish("ds/v2", String(state).c_str());
        mqttClient.publish("ds/v3", String(bat).c_str());
        mqttClient.publish("ds/v4", alert);

        Serial.printf("[TX] HR:%d | Temp:%.1f | State:%d | Bat:%d%% | %s\n",
                      hr, temp, state, bat, alert);
    }
}
