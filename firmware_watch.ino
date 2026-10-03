// ══════════════════════════════════════════════════════════════════════════════
// ESP32-S3 Mini (Waveshare) - Construction Worker Health Watch Smart Firmware
//
// Hardware Integrated:
//   1. MCU: Waveshare ESP32-S3 Mini (FreeRTOS Multi-tasking)
//   2. Display: 1.28-inch Round LCD (GC9A01 / 240x240 SPI)
//   3. Heart Rate Sensor: BioAmp Candy (Heart Beat & Pulse on ADC)
//   4. Body Temp Sensor: TMP117 7Semi (Clinical Grade I2C)
//   5. Alarm: Active Buzzer (GPIO 7 - Triggers ONLY on CRITICAL state)
//   6. Battery: WLY103443 3.7V 1600mAh 1S LiPo (Monitored on ADC)
//   7. Cloud & Dashboard: Blynk IoT Cloud + Real-time Clock (NTP)
// ══════════════════════════════════════════════════════════════════════════════

#define BLYNK_TEMPLATE_ID   "TMPLxxxxxx"
#define BLYNK_TEMPLATE_NAME "WorkerHealthWatch"
#define BLYNK_AUTH_TOKEN    "YOUR_BLYNK_AUTH_TOKEN"

#include <WiFi.h>
#include <WiFiClient.h>
#include <BlynkSimpleEsp32.h>
#include <Wire.h>
#include <SPI.h>
#include <TFT_eSPI.h>       // Fast graphics library for GC9A01 1.28" Round LCD
#include <time.h>

// ── Hardware Pin Mappings ──
#define PIN_BIOAMP_ADC      1   // BioAmp Candy Analog Output (ADC1_CH0)
#define PIN_BATTERY_ADC     2   // LiPo Battery Voltage Divider (ADC1_CH1)
#define PIN_I2C_SDA         4   // TMP117 SDA
#define PIN_I2C_SCL         5   // TMP117 SCL
#define PIN_BUZZER_ALARM    7   // Active Buzzer / Vibration Motor

// Display Pins (Configured in User_Setup.h for TFT_eSPI or defined here)
// LCD_DC=8, LCD_CS=9, LCD_SCLK=10, LCD_MOSI=11, LCD_RST=14, LCD_BL=15
#define PIN_LCD_BL          15  // Backlight PWM

#define TMP117_I2C_ADDR     0x48
#define TMP117_TEMP_REG     0x00

// ── Wi-Fi & NTP Configuration ──
const char* ssid     = "YOUR_WIFI_SSID";
const char* pass     = "YOUR_WIFI_PASSWORD";
const char* ntpServer = "pool.ntp.org";
const long  gmtOffset_sec     = 19800; // IST (+5:30)
const int   daylightOffset_sec = 0;

// ── Worker Adaptive Health Baseline Parameters ──
float g_worker_resting_hr   = 72.0;  // Baseline Resting HR (BPM)
float g_worker_resting_temp = 36.6;  // Baseline Resting Temp (°C)
String g_worker_id          = "WORKER_042";

// ── 3-Tier Health States ──
enum HealthState {
    HEALTHY = 0,
    RISK    = 1,
    CRITICAL = 2
};

// ── Global Shared Variables (Protected by FreeRTOS Mutex) ──
HealthState g_current_state = HEALTHY;
float g_latest_hr           = 72.0;
float g_latest_temp         = 36.6;
float g_battery_pct         = 100.0;
char  g_time_str[16]        = "12:00:00";
SemaphoreHandle_t g_vitals_mutex;

// ── Display & Graphics Object ──
TFT_eSPI tft = TFT_eSPI();

// ── FreeRTOS Task Handles ──
TaskHandle_t TaskBioAmpHandle;
TaskHandle_t TaskTriageHandle;
TaskHandle_t TaskDisplayHandle;

// ══════════════════════════════════════════════════════════════════════════════
// 1. SENSOR & HARDWARE DRIVERS
// ══════════════════════════════════════════════════════════════════════════════

// Reads TMP117 High Precision Temperature (°C)
float readTMP117() {
    Wire.beginTransmission(TMP117_I2C_ADDR);
    Wire.write(TMP117_TEMP_REG);
    if (Wire.endTransmission() != 0) return -999.0;

    Wire.requestFrom(TMP117_I2C_ADDR, 2);
    if (Wire.available() == 2) {
        int16_t raw = (Wire.read() << 8) | Wire.read();
        return raw * 0.0078125; // 7.8125 m°C per LSB
    }
    return -999.0;
}

// Reads 1S LiPo Battery Percentage from Voltage Divider
float readBatteryPercentage() {
    int raw = analogRead(PIN_BATTERY_ADC);
    float voltage = (raw / 4095.0) * 3.3 * 2.0; // 2:1 voltage divider
    float pct = ((voltage - 3.2) / (4.2 - 3.2)) * 100.0;
    return constrain(pct, 0.0, 100.0);
}

// ══════════════════════════════════════════════════════════════════════════════
// 2. FREERTOS TASK 1: BIOAMP ECG SAMPLING & HR ESTIMATION (Core 0 @ 250 Hz)
// ══════════════════════════════════════════════════════════════════════════════

void TaskBioAmp(void *pvParameters) {
    int threshold = 2200;
    bool peakDetected = false;
    unsigned long lastPeakTime = 0;

    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);

    for (;;) {
        int rawADC = analogRead(PIN_BIOAMP_ADC);

        if (rawADC > threshold && !peakDetected) {
            unsigned long now = millis();
            unsigned long rrInterval = now - lastPeakTime;

            if (rrInterval > 300 && rrInterval < 2000) { // 30-200 BPM
                float calculated_hr = 60000.0 / rrInterval;
                if (xSemaphoreTake(g_vitals_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                    g_latest_hr = (g_latest_hr * 0.7) + (calculated_hr * 0.3); // Exponential smoothing
                    xSemaphoreGive(g_vitals_mutex);
                }
            }
            lastPeakTime = now;
            peakDetected = true;
        } else if (rawADC < threshold - 200) {
            peakDetected = false;
        }

        vTaskDelay(pdMS_TO_TICKS(4)); // 250 Hz
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// 3. FREERTOS TASK 2: 1-SECOND TRIAGE & CLOUD SYNC (Core 1 @ 1 Hz)
// ══════════════════════════════════════════════════════════════════════════════

void TaskTriage(void *pvParameters) {
    for (;;) {
        // Read Temperature & Battery
        float temp = readTMP117();
        float bat = readBatteryPercentage();

        // Update time string from internal RTC
        struct tm timeinfo;
        if (getLocalTime(&timeinfo)) {
            strftime(g_time_str, sizeof(g_time_str), "%H:%M:%S", &timeinfo);
        }

        if (xSemaphoreTake(g_vitals_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
            if (temp > 25.0 && temp < 45.0) g_latest_temp = temp;
            g_battery_pct = bat;

            // Personalized Baseline Deviations (The Novelty Factor)
            float delta_hr = g_latest_hr - g_worker_resting_hr;
            float delta_temp = g_latest_temp - g_worker_resting_temp;

            // 3-Tier State Machine
            if ((delta_hr > 50.0 && delta_temp > 1.4) || (g_latest_hr > 135.0 && g_latest_temp > 38.5) || (g_latest_temp > 39.4)) {
                g_current_state = CRITICAL;
                digitalWrite(PIN_BUZZER_ALARM, HIGH); // 🚨 Wrist Buzzer Sounds
            } else if (delta_hr > 30.0 || delta_temp > 0.9 || g_latest_hr > 115.0 || g_latest_temp > 37.8) {
                g_current_state = RISK;
                digitalWrite(PIN_BUZZER_ALARM, LOW);  // Silent on watch
            } else {
                g_current_state = HEALTHY;
                digitalWrite(PIN_BUZZER_ALARM, LOW);  // Silent on watch
            }
            xSemaphoreGive(g_vitals_mutex);
        }

        // Push 1-Second Telemetry to Blynk IoT
        if (Blynk.connected()) {
            Blynk.virtualWrite(V0, (int)g_latest_hr);
            Blynk.virtualWrite(V1, g_latest_temp);

            if (g_current_state == HEALTHY) {
                Blynk.virtualWrite(V2, "🟢 HEALTHY");
                Blynk.virtualWrite(V3, 0);
            } else if (g_current_state == RISK) {
                Blynk.virtualWrite(V2, "🟡 RISK (WARNING)");
                Blynk.virtualWrite(V3, 0);
            } else {
                Blynk.virtualWrite(V2, "🔴 CRITICAL (EMERGENCY)");
                Blynk.virtualWrite(V3, 255); // Flashing Red Alert on Dashboard
                Blynk.logEvent("critical_health_alert", "CRITICAL: Worker " + g_worker_id + " Heat Stroke Warning!");
            }
        }

        vTaskDelay(pdMS_TO_TICKS(1000)); // Exactly 1-Second RTOS tick
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// 4. FREERTOS TASK 3: 1.28-INCH ROUND LCD UI RENDERER (Core 1 @ 5 Hz)
// ══════════════════════════════════════════════════════════════════════════════

void drawRoundWatchFace() {
    float hr, temp, bat;
    HealthState state;
    char timeBuffer[16];

    if (xSemaphoreTake(g_vitals_mutex, pdMS_TO_TICKS(20)) == pdTRUE) {
        hr = g_latest_hr;
        temp = g_latest_temp;
        bat = g_battery_pct;
        state = g_current_state;
        strcpy(timeBuffer, g_time_str);
        xSemaphoreGive(g_vitals_mutex);
    } else {
        return;
    }

    // 1. Determine Outer Ring Theme Color based on Health State
    uint16_t themeColor;
    const char* statusText;
    if (state == HEALTHY) {
        themeColor = TFT_GREEN;
        statusText = "HEALTHY";
    } else if (state == RISK) {
        themeColor = TFT_YELLOW;
        statusText = "RISK WARN";
    } else {
        themeColor = TFT_RED;
        statusText = "! CRITICAL !";
    }

    // 2. Draw Watch Outer Ring (240x240 Circular Display)
    tft.drawCircle(120, 120, 118, themeColor);
    tft.drawCircle(120, 120, 117, themeColor);

    // 3. Top Section: Real-time Clock & Battery
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextDatum(TC_DATUM);
    tft.drawString(timeBuffer, 120, 25, 4); // Digital Time (Font 4)

    tft.setTextDatum(TR_DATUM);
    tft.drawString(String((int)bat) + "%", 185, 55, 2);

    // 4. Middle Left Card: Heart Rate (BPM)
    tft.setTextColor(TFT_CYAN, TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.drawString("HEART RATE", 75, 95, 2);
    tft.setTextColor(themeColor == TFT_RED ? TFT_RED : TFT_WHITE, TFT_BLACK);
    tft.drawString(String((int)hr) + " bpm", 75, 125, 4);

    // 5. Middle Right Card: Body Temperature (°C)
    tft.setTextColor(TFT_ORANGE, TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.drawString("BODY TEMP", 165, 95, 2);
    tft.setTextColor(themeColor == TFT_RED ? TFT_RED : TFT_WHITE, TFT_BLACK);
    char tempStr[10];
    sprintf(tempStr, "%.1f C", temp);
    tft.drawString(tempStr, 165, 125, 4);

    // 6. Bottom Banner: Health Triage Status Badge
    tft.fillRoundRect(35, 165, 170, 34, 8, themeColor);
    tft.setTextColor(themeColor == TFT_YELLOW ? TFT_BLACK : TFT_WHITE, themeColor);
    tft.setTextDatum(MC_DATUM);
    tft.drawString(statusText, 120, 182, 4);
}

void TaskDisplayUI(void *pvParameters) {
    tft.init();
    tft.setRotation(0);
    tft.fillScreen(TFT_BLACK);

    pinMode(PIN_LCD_BL, OUTPUT);
    analogWrite(PIN_LCD_BL, 255); // Full brightness

    for (;;) {
        drawRoundWatchFace();
        vTaskDelay(pdMS_TO_TICKS(200)); // Smooth 5 FPS LCD Refresh
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// 5. BLYNK INTERACTIVE CONTROLS (Set baseline per worker from App)
// ══════════════════════════════════════════════════════════════════════════════

BLYNK_WRITE(V5) {
    g_worker_resting_hr = param.asFloat();
    Serial.printf("[BLYNK] Updated worker baseline HR: %.1f BPM\n", g_worker_resting_hr);
}

BLYNK_WRITE(V6) {
    g_worker_resting_temp = param.asFloat();
    Serial.printf("[BLYNK] Updated worker baseline Temp: %.2f °C\n", g_worker_resting_temp);
}

// ══════════════════════════════════════════════════════════════════════════════
// 6. SETUP & MAIN LOOP
// ══════════════════════════════════════════════════════════════════════════════

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n🚀 Initializing Construction Worker Health Watch with 1.28\" LCD & LiPo...");

    g_vitals_mutex = xSemaphoreCreateMutex();

    pinMode(PIN_BUZZER_ALARM, OUTPUT);
    digitalWrite(PIN_BUZZER_ALARM, LOW);

    // Initialize I2C for TMP117
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);

    // Connect Wi-Fi & Sync NTP Time
    WiFi.begin(ssid, pass);
    configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);

    // Connect Blynk IoT
    Blynk.config(BLYNK_AUTH_TOKEN);
    Blynk.connect();

    // Create FreeRTOS Tasks
    xTaskCreatePinnedToCore(TaskBioAmp, "TaskBioAmp", 4096, NULL, 3, &TaskBioAmpHandle, 0);
    xTaskCreatePinnedToCore(TaskTriage, "TaskTriage", 4096, NULL, 2, &TaskTriageHandle, 1);
    xTaskCreatePinnedToCore(TaskDisplayUI, "TaskDisplayUI", 6144, NULL, 1, &TaskDisplayHandle, 1);

    Serial.println("✅ All FreeRTOS tasks (Sensors, Triage, 1.28\" Display, Blynk) active.");
}

void loop() {
    Blynk.run();
}
