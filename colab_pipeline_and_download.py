# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE COLAB ALL-IN-ONE PIPELINE WITH INTERACTIVE FILE UPLOADER
# ══════════════════════════════════════════════════════════════════════════════

import os, sys, re, json, zipfile
from pathlib import Path
from typing import Dict, List, Optional, Union
import numpy as np
import pandas as pd

# Check if running in Google Colab
try:
    from google.colab import files
    IN_COLAB = True
except ImportError:
    IN_COLAB = False

DATA_DIR = "/content"

# ── 1. CHECK FOR DATA FILES & PROMPT UPLOAD IF EMPTY ──
print("=" * 70)
print("🚀 WEARABLE WORKER HEALTH DATA PIPELINE (GOOGLE COLAB)")
print("=" * 70)

def get_data_files(directory):
    return [
        p for p in Path(directory).rglob("*")
        if p.is_file() and p.suffix.lower() in [".csv", ".zip", ".xlsx", ".xls", ".json", ".parquet", ".txt"]
        and not p.name.startswith((".", "_"))
        and p.name not in ["cleaned_master.csv", "supervisor_alerts.csv"]
    ]

existing_files = get_data_files(DATA_DIR)

if not existing_files and IN_COLAB:
    print("\n📂 No dataset files detected in Google Colab yet.")
    print("👉 Click the 'Choose Files' button below to select and upload your CSV / ZIP files:")
    uploaded = files.upload()
    existing_files = get_data_files(DATA_DIR)

print(f"\n📊 Found {len(existing_files)} data file(s) to process:")
for f in existing_files:
    print(f"   • {f.name}")
print("=" * 70 + "\n")

# ── 2. SCHEMA & CLEANING ENGINE ──
CANONICAL_COLUMNS = [
    "worker_id", "timestamp", "age", "hr_bpm", "body_temp_c", "spo2_percent",
    "accel_x", "accel_y", "accel_z", "is_working", "worker_city",
    "risk_level", "supervisor_alert", "alert_reason", "_source"
]

COLUMN_MAPS = {
    "Heart_Rate__1_.csv": {"Heart Rate": "hr_bpm"},
    "heartv1.csv": {"age": "age", "thalach ": "hr_bpm", "thalach": "hr_bpm"},
    "physiological_data.csv": {"PPG (BPM)": "hr_bpm", "Timestamp": "timestamp", "Participant_ID": "worker_id", "SpO2 (%)": "spo2_percent"},
    "wearable_sports_health_dataset.csv": {"Heart_Rate": "hr_bpm", "Body_Temperature": "body_temp_c", "Timestamp": "timestamp", "Athlete_ID": "worker_id", "SpO2": "spo2_percent"},
    "wearable_sensor_data.csv": {"Heart Rate (bpm)": "hr_bpm", "Body Temperature (°C)": "body_temp_c", "Timestamp": "timestamp", "SpO2": "spo2_percent"},
    "Stress-Lysis.csv": {"Temperature": "body_temp_c", "Humidity": "humidity_ambient", "Step_count": "step_count", "Stress_Level": "stress_level"},
    "secure_edge_healthcare_dataset.csv": {"Patient_ID": "worker_id", "Timestamp": "timestamp", "Age": "age", "Heart_Rate_bpm": "hr_bpm", "Body_Temperature_C": "body_temp_c", "SpO2_%": "spo2_percent"},
    "Heart_risk_dataset.csv": {"patient_id": "worker_id", "age": "age", "heartRate": "hr_bpm", "bodyTemp": "body_temp_c"},
    "remote_health_monitoring_dataset.csv": {"Patient_ID": "worker_id", "Age": "age", "Heart_Rate_bpm": "hr_bpm", "Body_Temperature_C": "body_temp_c", "Oxygen_Saturation_%": "spo2_percent"},
    "Synthetic_patient-HealthCare-Monitoring_dataset.csv": {"Patient Number": "worker_id", "Heart Rate (bpm)": "hr_bpm", "Body Temperature (°C)": "body_temp_c", "Blood Oxygen Level(%)": "spo2_percent"},
    "iot_health_monitoring_dataset.csv": {"timestamp": "timestamp", "patient_id": "worker_id", "heart_rate": "hr_bpm", "body_temperature": "body_temp_c", "pulse_oximetry": "spo2_percent"},
    "Healthcare_iot_dataset.csv": {"heart_rate": "hr_bpm", "body_temperature": "body_temp_c", "spo2": "spo2_percent"},
    "digital_ecosystem_smart_healthcare_dataset.csv": {"Heart_Rate": "hr_bpm", "Body_Temperature": "body_temp_c", "Oxygen_Level": "spo2_percent"},
    "Self_Adaptive_WSN_Patient_Monitoring_Dataset.csv": {"patient_id": "worker_id", "timestamp": "timestamp", "heart_rate_bpm": "hr_bpm", "body_temperature_c": "body_temp_c", "spo2_percent": "spo2_percent"}
}

HEADERLESS_FILES = {f"D{i}.csv": ["accel_x", "body_temp_c", "hr_bpm", "spo2_percent", "mdd_label"] for i in range(1, 9)}
HR_PATTERNS = ["hr", "heart_rate", "heartrate", "pulse", "bpm", "hr_bpm", "heart rate", "ppg (bpm)", "thalach"]
TEMP_PATTERNS = ["temp", "temperature", "body_temp", "skin_temp", "bt", "bodytemp", "body temperature", "temp_c", "skin_temperature"]
SPO2_PATTERNS = ["spo2", "oximeter", "oxygen", "spo2_percent", "oxygen_saturation", "pulse_oximetry", "blood oxygen", "spo2 (%)"]
AGE_PATTERNS = ["age", "years", "age_years", "participant_age", "subject_age"]
ID_PATTERNS = ["worker_id", "subject_id", "participant_id", "id", "worker", "subject", "person_id", "user_id", "patient_id", "athlete_id"]

def load_file_bytes(f, filename: str) -> Optional[pd.DataFrame]:
    lower_name = str(filename).lower()
    try:
        if lower_name.endswith((".csv", ".txt")):
            try: return pd.read_csv(f)
            except Exception: f.seek(0); return pd.read_csv(f, sep=";", encoding="utf-8-sig")
        elif lower_name.endswith(".json"):
            raw = json.load(f)
            return pd.json_normalize(raw) if isinstance(raw, list) else pd.json_normalize([raw])
        elif lower_name.endswith((".xlsx", ".xls")):
            return pd.read_excel(f)
        elif lower_name.endswith(".parquet"):
            return pd.read_parquet(f)
        return None
    except Exception: return None

def find_matched_column(df_columns: List[str], patterns: List[str]) -> Optional[str]:
    cleaned_map = {re.sub(r"[_\s\-\(\)\%°/]", "", str(c)).lower(): c for c in df_columns}
    for pat in patterns:
        clean_pat = re.sub(r"[_\s\-\(\)\%°/]", "", pat).lower()
        if clean_pat in cleaned_map: return cleaned_map[clean_pat]
    return None

def standardise_columns(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    df = df.copy()
    if filename in COLUMN_MAPS:
        df = df.rename(columns=COLUMN_MAPS[filename])
    else:
        for target_col, pats in [("hr_bpm", HR_PATTERNS), ("body_temp_c", TEMP_PATTERNS), ("spo2_percent", SPO2_PATTERNS),
                                 ("age", AGE_PATTERNS), ("worker_id", ID_PATTERNS)]:
            matched = find_matched_column(list(df.columns), pats)
            if matched: df = df.rename(columns={matched: target_col})
    return df

def clean_and_evaluate(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    if df.empty: return pd.DataFrame()
    df = df.copy()
    initial_count = len(df)

    has_hr, has_temp, has_spo2 = "hr_bpm" in df.columns, "body_temp_c" in df.columns, "spo2_percent" in df.columns
    if not has_hr and not has_temp and not has_spo2: return pd.DataFrame()

    for col in ["hr_bpm", "body_temp_c", "spo2_percent", "age"]:
        df[col] = pd.to_numeric(df[col], errors="coerce") if col in df.columns else np.nan

    df["worker_id"] = df["worker_id"].astype(str) if "worker_id" in df.columns else "W_UNK"

    # Unit conversions
    if has_temp and df["body_temp_c"].notna().any() and df["body_temp_c"].median() > 45.0:
        df["body_temp_c"] = (df["body_temp_c"] - 32.0) * (5.0 / 9.0)
    if has_hr and df["hr_bpm"].notna().any() and 0.3 <= df["hr_bpm"].median() <= 2.5:
        df["hr_bpm"] = 60.0 / df["hr_bpm"]

    # Sanitize Disconnect Codes (-4, -3, -2, 0, -20)
    df.loc[df["hr_bpm"].notna() & ((df["hr_bpm"] < 25.0) | (df["hr_bpm"] > 240.0)), "hr_bpm"] = np.nan
    df.loc[df["body_temp_c"].notna() & ((df["body_temp_c"] < 25.0) | (df["body_temp_c"] > 43.5)), "body_temp_c"] = np.nan
    df.loc[df["spo2_percent"].notna() & ((df["spo2_percent"] < 50.0) | (df["spo2_percent"] > 100.0)), "spo2_percent"] = np.nan

    df = df[df["hr_bpm"].notna() | df["body_temp_c"].notna() | df["spo2_percent"].notna()]
    if df.empty: return pd.DataFrame()

    # Imputation
    if has_hr and df["hr_bpm"].isna().any():
        df["hr_bpm"] = df.groupby("worker_id")["hr_bpm"].ffill().bfill().fillna(df["hr_bpm"].median())
    if has_temp and df["body_temp_c"].isna().any():
        df["body_temp_c"] = df.groupby("worker_id")["body_temp_c"].ffill().bfill().fillna(df["body_temp_c"].median())
    if has_spo2 and df["spo2_percent"].isna().any():
        df["spo2_percent"] = df.groupby("worker_id")["spo2_percent"].ffill().bfill().fillna(df["spo2_percent"].median())

    # Risk Triage (0: Normal, 1: High HR, 2: High Temp, 3: Critical Heat Stroke)
    effective_age = df["age"].fillna(35.0)
    max_hr = 220.0 - effective_age
    working_tolerance_hr = 0.90 * max_hr

    high_hr = df["hr_bpm"].notna() & (df["hr_bpm"] > working_tolerance_hr)
    high_temp = df["body_temp_c"].notna() & (df["body_temp_c"] > 37.8)
    low_vitals = (df["hr_bpm"] < 50.0) | (df["body_temp_c"] < 35.5) | (df["spo2_percent"] < 90.0)

    risk_level = pd.Series("NORMAL", index=df.index)
    critical_heat_stroke = high_hr & high_temp
    risk_level[critical_heat_stroke] = "CRITICAL_HEAT_STROKE"
    risk_level[high_hr & ~critical_heat_stroke] = "HIGH_HR_EXERTION"
    risk_level[high_temp & ~critical_heat_stroke] = "HIGH_TEMP_HEAT_STRESS"
    risk_level[low_vitals] = "LOW_VITALS_ALERT"

    df["risk_level"] = risk_level
    df["supervisor_alert"] = risk_level != "NORMAL"
    df["_source"] = source_name

    for col in CANONICAL_COLUMNS:
        if col not in df.columns: df[col] = np.nan

    out_df = df[[c for c in CANONICAL_COLUMNS if c in df.columns]]
    print(f"  ✓ {source_name:32s} | In: {initial_count:6d} -> Out: {len(out_df):6d} | Alerts: {out_df['supervisor_alert'].sum():4d}")
    return out_df

# ── 3. EXECUTE FULL PROCESSING ──
frames = []

for p in existing_files:
    fname = p.name
    if fname in HEADERLESS_FILES:
        raw = pd.read_csv(p, header=None)
        raw.columns = HEADERLESS_FILES[fname]
        cleaned = clean_and_evaluate(raw, fname)
        if not cleaned.empty: frames.append(cleaned)
    elif fname.endswith(".zip"):
        try:
            with zipfile.ZipFile(p) as z:
                for inner in z.namelist():
                    if inner.lower().endswith((".csv", ".txt", ".json", ".parquet")) and not any(k in inner.lower() for k in ["readme", "license", "env"]):
                        with z.open(inner) as f:
                            raw = load_file_bytes(f, inner)
                            if raw is not None and not raw.empty:
                                mapped = standardise_columns(raw, Path(inner).name)
                                cleaned = clean_and_evaluate(mapped, f"{fname}::{Path(inner).name}")
                                if not cleaned.empty: frames.append(cleaned)
        except Exception as e:
            print(f"  ⚠️ Error parsing zip {fname}: {e}")
    elif p.suffix.lower() in [".csv", ".xlsx", ".xls", ".json", ".parquet"]:
        try:
            with open(p, "rb") as f:
                raw = load_file_bytes(f, fname)
                if raw is not None and not raw.empty:
                    mapped = standardise_columns(raw, fname)
                    cleaned = clean_and_evaluate(mapped, fname)
                    if not cleaned.empty: frames.append(cleaned)
        except Exception as e:
            print(f"  ⚠️ Error reading {fname}: {e}")

if not frames:
    print("\n❌ No valid physiological data rows were loaded. Please check that your files are valid CSV/ZIP archives!")
else:
    master_df = pd.concat(frames, ignore_index=True)
    master_df.to_csv("cleaned_master.csv", index=False)
    alerts_df = master_df[master_df["supervisor_alert"]]
    alerts_df.to_csv("supervisor_alerts.csv", index=False)

    print("\n" + "=" * 70)
    print("🎉 CLEANING COMPLETE!")
    print(f"📊 Total Cleaned Rows : {len(master_df):,}")
    print(f"🚨 Total Alerts Flagged: {len(alerts_df):,} ({(len(alerts_df)/len(master_df)*100.0):.2f}%)")
    print("=" * 70)

    if IN_COLAB:
        print("\n⬇️ Downloading 'cleaned_master.csv' and 'supervisor_alerts.csv' to your computer...")
        files.download("cleaned_master.csv")
        files.download("supervisor_alerts.csv")
        print("✅ Download popup opened!")
