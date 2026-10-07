// ══════════════════════════════════════════════════════════════════════════════
// DISPLAY NODE: Regular ESP32
// Receives Health Packet from S3 Mini via ESP-NOW → Renders GC9A01 Round LCD
// ══════════════════════════════════════════════════════════════════════════════

#include <WiFi.h>
#include <esp_now.h>
#include <SPI.h>
#include <TFT_eSPI.h>

// ── Regular ESP32 Pin Mappings (Hardware VSPI — Stable) ──
// MOSI=23, SCLK=18, CS=5, DC=2, RST=4 — defined in User_Setup.h
#define PIN_LCD_BL  15
#define PIN_BUZZER   7   // Optional local buzzer on display node

TFT_eSPI tft = TFT_eSPI();

// ── Shared Packet (must match Master's struct EXACTLY) ──
typedef struct HealthPacket {
    int   hr_bpm;
    float temp_c;
    int   health_state;
    int   battery_pct;
    char  alert_msg[32];
} HealthPacket;

HealthPacket rxPacket;
volatile bool newDataAvailable = false;

// ── ESP-NOW Receive Callback ──
void onDataReceived(const uint8_t *mac, const uint8_t *data, int len) {
    memcpy(&rxPacket, data, sizeof(rxPacket));
    newDataAvailable = true;
}

// ── GC9A01 Display Initialization (Regular ESP32) ──
void initDisplay() {
    pinMode(PIN_LCD_BL, OUTPUT);
    digitalWrite(PIN_LCD_BL, HIGH);
    tft.init();
    tft.setRotation(0);
    // Regular ESP32 + GC9A01 does NOT need invertDisplay
    tft.fillScreen(TFT_BLACK);
    tft.drawCircle(120, 120, 118, TFT_DARKGREEN);
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(TFT_GREEN, TFT_BLACK);
    tft.drawString("DISPLAY NODE", 120, 110, 4);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString("Waiting...", 120, 145, 2);
    Serial.println("[DISPLAY] GC9A01 OK on Regular ESP32.");
}

// ── Render Health Dashboard on 240x240 Circle ──
void renderDashboard(HealthPacket &p) {
    // Color theme per state
    uint32_t bgColor, ringColor, textColor;
    if (p.health_state == 2) {
        bgColor   = TFT_BLACK;
        ringColor = TFT_RED;
        textColor = TFT_RED;
        digitalWrite(PIN_BUZZER, HIGH); // Local alarm mirror
    } else if (p.health_state == 1) {
        bgColor   = TFT_BLACK;
        ringColor = 0xFD20; // Orange
        textColor = 0xFD20;
        digitalWrite(PIN_BUZZER, LOW);
    } else {
        bgColor   = TFT_BLACK;
        ringColor = TFT_GREEN;
        textColor = TFT_GREEN;
        digitalWrite(PIN_BUZZER, LOW);
    }

    tft.fillScreen(bgColor);

    // Outer status ring (3px thick)
    tft.drawCircle(120, 120, 118, ringColor);
    tft.drawCircle(120, 120, 117, ringColor);
    tft.drawCircle(120, 120, 116, ringColor);

    // ── Heart Rate ──
    tft.setTextColor(TFT_WHITE, bgColor);
    tft.setTextDatum(MC_DATUM);
    tft.drawString("HEART RATE", 120, 55, 2);
    tft.setTextColor(textColor, bgColor);
    tft.drawString(String(p.hr_bpm) + " bpm", 120, 80, 4);

    // ── Divider Line ──
    tft.drawFastHLine(40, 105, 160, TFT_DARKGREY);

    // ── Temperature ──
    tft.setTextColor(TFT_WHITE, bgColor);
    tft.drawString("BODY TEMP", 120, 125, 2);
    tft.setTextColor(textColor, bgColor);
    tft.drawString(String(p.temp_c, 1) + " C", 120, 150, 4);

    // ── Battery Bar (bottom arc) ──
    tft.setTextColor(TFT_DARKGREY, bgColor);
    tft.drawString("BAT: " + String(p.battery_pct) + "%", 120, 188, 2);

    // ── Alert Label (if not normal) ──
    if (p.health_state > 0) {
        tft.setTextColor(TFT_WHITE, ringColor);
        tft.drawString(p.alert_msg, 120, 210, 1);
    }
}

void setup() {
    Serial.begin(115200);
    pinMode(PIN_BUZZER, OUTPUT);
    digitalWrite(PIN_BUZZER, LOW);

    initDisplay();

    // ESP-NOW Receive Setup (no WiFi AP/STA join needed)
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    if (esp_now_init() != ESP_OK) {
        Serial.println("[ESP-NOW] Init FAILED!");
    } else {
        esp_now_register_recv_cb(onDataReceived);
        Serial.println("[ESP-NOW] Display Node Listening...");
    }
}

void loop() {
    if (newDataAvailable) {
        newDataAvailable = false;
        renderDashboard(rxPacket);
        Serial.printf("[RX] HR:%d | Temp:%.1f | State:%d | Bat:%d%% | %s\n",
                      rxPacket.hr_bpm, rxPacket.temp_c,
                      rxPacket.health_state, rxPacket.battery_pct,
                      rxPacket.alert_msg);
    }
}
