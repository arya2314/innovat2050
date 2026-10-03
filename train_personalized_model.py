# ══════════════════════════════════════════════════════════════════════════════
# Personalized 2-Parameter Worker Health ML Pipeline (Cloud & TinyML ESP32-S3)
# Parameters: Heart Rate (BPM) & Body Temperature (°C)
# Novelty: Adaptive Individual Baseline & Body-Type Conditioned 3-Tier Triage:
#   [0: HEALTHY]   -> Safe steady state (Silent on watch)
#   [1: RISK]      -> Exertion / Heat Strain warning (Blynk Yellow alert)
#   [2: CRITICAL]  -> Heat Stroke / Cardiac Overload (Wrist Buzzer + Blynk Red)
# ══════════════════════════════════════════════════════════════════════════════

import os
import sys
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers
from sklearn.metrics import classification_report, confusion_matrix

print("=" * 80)
print("🚀 PERSONALIZED 2-PARAMETER WORKER HEALTH & HEAT STRESS ML PIPELINE")
print(f"   TensorFlow Version: {tf.__version__}")
print("=" * 80)

np.random.seed(42)
tf.random.set_seed(42)

# ══════════════════════════════════════════════════════════════════════════════
# 1. SYNTHETIC REALISTIC DATASET GENERATOR WITH INDIVIDUAL PROFILES
# ══════════════════════════════════════════════════════════════════════════════

def generate_personalized_worker_dataset(num_workers=40, samples_per_worker=250, window_size=30):
    """
    Generates realistic 2-parameter time series (HR, Temp) conditioned on
    individual worker baseline capabilities and body profiles.
    
    Features generated per sample:
      - Raw: hr_bpm, body_temp_c
      - Novelty Context: delta_hr (HR - baseline_hr), delta_temp (Temp - baseline_temp)
    """
    print(f"\n[INFO] Simulating {num_workers} unique construction workers with individual baseline profiles...")
    
    all_X = []
    all_y = []
    
    for w in range(num_workers):
        # Individual baseline health traits
        worker_age = np.random.randint(22, 60)
        worker_resting_hr = np.random.uniform(58.0, 78.0)  # Personalized resting HR
        worker_resting_temp = np.random.uniform(36.3, 36.9) # Personalized resting Temp
        max_safe_hr = 220 - worker_age
        
        # Simulate worker shifts
        for s in range(samples_per_worker):
            # Health condition probability
            state = np.random.choice([0, 1, 2], p=[0.60, 0.28, 0.12])
            
            if state == 0:  # HEALTHY
                hr_seq = worker_resting_hr + np.random.uniform(0, 30) + np.random.normal(0, 1.5, window_size)
                temp_seq = worker_resting_temp + np.random.uniform(0.0, 0.5) + np.random.normal(0, 0.05, window_size)
            elif state == 1:  # RISK (Exertion / Heat Strain)
                # Either elevated HR or elevated Temperature
                if np.random.rand() > 0.5:
                    hr_seq = worker_resting_hr + np.random.uniform(45, 75) + np.random.normal(0, 2.0, window_size)
                    temp_seq = worker_resting_temp + np.random.uniform(0.2, 0.7) + np.random.normal(0, 0.08, window_size)
                else:
                    hr_seq = worker_resting_hr + np.random.uniform(20, 45) + np.random.normal(0, 2.0, window_size)
                    temp_seq = 37.8 + np.random.uniform(0.2, 0.9) + np.random.normal(0, 0.08, window_size)
            else:  # CRITICAL (Cardiac Overload + Dangerous Heat Stroke)
                hr_seq = (0.85 * max_safe_hr) + np.random.uniform(10, 30) + np.random.normal(0, 3.0, window_size)
                temp_seq = 38.6 + np.random.uniform(0.5, 1.8) + np.random.normal(0, 0.1, window_size)
            
            # Compute baseline deltas (Personalized Novelty Features)
            delta_hr_seq = hr_seq - worker_resting_hr
            delta_temp_seq = temp_seq - worker_resting_temp
            
            # Feature matrix: shape (window_size, 4) -> [HR, Temp, Delta_HR, Delta_Temp]
            sample_feats = np.stack([hr_seq, temp_seq, delta_hr_seq, delta_temp_seq], axis=-1).astype(np.float32)
            
            all_X.append(sample_feats)
            all_y.append(state)
            
    X = np.array(all_X, dtype=np.float32)
    y = np.array(all_y, dtype=np.int32)
    
    return X, y


# ══════════════════════════════════════════════════════════════════════════════
# 2. DATA LOADER & CLEANING PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def load_and_preprocess_dataset(csv_path="cleaned_master.csv", window_size=30, stride=10, test_split=0.2):
    """
    Loads dataset from CSV or generates high-fidelity personalized worker data.
    """
    if not os.path.exists(csv_path):
        print(f"[INFO] '{csv_path}' not found. Generating high-fidelity personalized worker health dataset...")
        X, y = generate_personalized_worker_dataset(num_workers=45, samples_per_worker=200, window_size=window_size)
    else:
        print(f"[INFO] Loading data from {csv_path}...")
        df = pd.read_csv(csv_path)
        
        # Ensure HR and Temp exist
        if "hr_bpm" not in df.columns or "body_temp_c" not in df.columns:
            raise ValueError("Dataset missing 'hr_bpm' or 'body_temp_c'.")
            
        clean_df = df.dropna(subset=["hr_bpm", "body_temp_c"]).copy()
        
        # Compute baseline per worker if worker_id exists, else estimate population baseline
        if "worker_id" in clean_df.columns:
            clean_df["base_hr"] = clean_df.groupby("worker_id")["hr_bpm"].transform(lambda s: s.quantile(0.15))
            clean_df["base_temp"] = clean_df.groupby("worker_id")["body_temp_c"].transform(lambda s: s.quantile(0.15))
        else:
            clean_df["base_hr"] = 72.0
            clean_df["base_temp"] = 36.6
            
        clean_df["delta_hr"] = clean_df["hr_bpm"] - clean_df["base_hr"]
        clean_df["delta_temp"] = clean_df["body_temp_c"] - clean_df["base_temp"]
        
        # Map or compute 3-tier triage labels: 0: HEALTHY, 1: RISK, 2: CRITICAL
        hr = clean_df["hr_bpm"].values
        temp = clean_df["body_temp_c"].values
        d_hr = clean_df["delta_hr"].values
        d_temp = clean_df["delta_temp"].values
        
        labels = np.zeros(len(clean_df), dtype=np.int32)
        
        # Risk: either high delta HR or high delta Temp
        risk_mask = ((d_hr > 35.0) | (d_temp > 1.0) | (hr > 115.0) | (temp > 37.8))
        # Critical: severe combined cardiac and thermal distress
        crit_mask = ((d_hr > 50.0) & (d_temp > 1.4)) | ((hr > 135.0) & (temp > 38.5)) | (temp > 39.5)
        
        labels[risk_mask & ~crit_mask] = 1
        labels[crit_mask] = 2
        
        feats = clean_df[["hr_bpm", "body_temp_c", "delta_hr", "delta_temp"]].values.astype(np.float32)
        
        X_list, y_list = [], []
        for i in range(0, len(feats) - window_size + 1, stride):
            X_list.append(feats[i : i + window_size])
            y_list.append(int(np.max(labels[i : i + window_size])))
            
        X = np.array(X_list, dtype=np.float32)
        y = np.array(y_list, dtype=np.int32)

    # Train / Test split
    indices = np.arange(len(X))
    np.random.shuffle(indices)
    X, y = X[indices], y[indices]
    
    split = int((1.0 - test_split) * len(X))
    X_train, y_train = X[:split], y[:split]
    X_test, y_test = X[split:], y[split:]
    
    print(f"📊 Processed Dataset:")
    print(f"   • Train shape: {X_train.shape} | Test shape: {X_test.shape}")
    print(f"   • Class Distribution (Train): {np.bincount(y_train, minlength=3)} [0:HEALTHY, 1:RISK, 2:CRITICAL]")
    
    return X_train, y_train, X_test, y_test


# ══════════════════════════════════════════════════════════════════════════════
# 3. PERSONALIZED 1D-CNN TINYML MODEL FOR ESP32-S3 & CLOUD INFERENCE
# ══════════════════════════════════════════════════════════════════════════════

def build_personalized_health_model(input_shape=(30, 4), num_classes=3):
    """
    Constructs an ultra-lightweight 1D-CNN model designed for:
      1. Cloud-based high-throughput inference
      2. Direct ESP32-S3 on-device inference via TFLite Micro (<12 KB RAM)
    """
    model = keras.Sequential([
        layers.Input(shape=input_shape, name="vitals_window_input"),
        
        layers.Conv1D(filters=12, kernel_size=3, padding="same", activation="relu"),
        layers.BatchNormalization(),
        layers.MaxPooling1D(pool_size=2),
        
        layers.Conv1D(filters=24, kernel_size=3, padding="same", activation="relu"),
        layers.BatchNormalization(),
        layers.GlobalAveragePooling1D(),
        
        layers.Dropout(0.2),
        layers.Dense(16, activation="relu"),
        layers.Dense(num_classes, activation="softmax", name="triage_output")
    ], name="Personalized_Worker_Health_Model")

    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=0.002),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"]
    )
    return model


# ══════════════════════════════════════════════════════════════════════════════
# 4. FULL INT8 QUANTIZATION & C++ EMBEDDED HEADER EXPORT
# ══════════════════════════════════════════════════════════════════════════════

def quantize_and_export_tflite(model, X_train, header_path="model_data.h", tflite_path="personalized_model_int8.tflite"):
    """
    Converts model to full INT8 precision for ESP32-S3 and exports C++ byte array header.
    """
    print("\n--- Converting & Quantizing Model to INT8 for ESP32-S3 ---")
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]

    def rep_dataset():
        for i in range(min(150, len(X_train))):
            sample = np.expand_dims(X_train[i], axis=0).astype(np.float32)
            yield [sample]

    converter.representative_dataset = rep_dataset
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8

    tflite_quant = converter.convert()
    model_len = len(tflite_quant)
    
    with open(tflite_path, "wb") as f:
        f.write(tflite_quant)
        
    print(f"✅ Quantized TFLite model saved: {tflite_path} ({model_len:,} bytes)")

    # Export C++ Header
    with open(header_path, "w") as f:
        f.write("// ============================================================================\n")
        f.write("// Personalized 2-Parameter Worker Health Model for ESP32-S3 (Waveshare Mini)\n")
        f.write("// Classes: 0: HEALTHY | 1: RISK | 2: CRITICAL (Triggers Buzzer & Blynk Alarm)\n")
        f.write(f"// Size: {model_len:,} bytes\n")
        f.write("// ============================================================================\n\n")
        f.write("#ifndef PERSONALIZED_HEALTH_MODEL_H_\n")
        f.write("#define PERSONALIZED_HEALTH_MODEL_H_\n\n")
        f.write(f"const unsigned int g_model_len = {model_len};\n\n")
        f.write("alignas(8) const unsigned char g_model[] = {\n    ")

        for idx, val in enumerate(list(tflite_quant)):
            f.write(f"0x{val:02x}")
            if idx < model_len - 1:
                f.write(", ")
            if (idx + 1) % 12 == 0 and idx < model_len - 1:
                f.write("\n    ")

        f.write("\n};\n\n")
        f.write("#endif // PERSONALIZED_HEALTH_MODEL_H_\n")

    print(f"✅ C++ Header '{header_path}' exported successfully for Arduino / ESP-IDF!")


# ══════════════════════════════════════════════════════════════════════════════
# 5. MAIN TRAINING & EVALUATION
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 1. Load Data
    X_train, y_train, X_test, y_test = load_and_preprocess_dataset(
        csv_path="cleaned_master.csv",
        window_size=30,
        stride=10
    )

    # 2. Build Model
    model = build_personalized_health_model(input_shape=(30, 4), num_classes=3)
    model.summary()

    # 3. Train Model
    print("\n--- Training Personalized Worker Health Model ---")
    history = model.fit(
        X_train, y_train,
        validation_split=0.15,
        epochs=20,
        batch_size=32,
        callbacks=[
            keras.callbacks.EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True),
            keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=2)
        ],
        verbose=1
    )

    # 4. Evaluation
    loss, acc = model.evaluate(X_test, y_test, verbose=0)
    print("\n" + "=" * 60)
    print(f"🎯 Test Evaluation -> Loss: {loss:.4f} | Accuracy: {acc * 100:.2f}%")
    print("=" * 60)

    y_pred = np.argmax(model.predict(X_test), axis=1)
    class_names = ["0: HEALTHY", "1: RISK (Warning)", "2: CRITICAL (Alarm)"]
    print("\n📋 Classification Report:")
    print(classification_report(y_test, y_pred, target_names=class_names, zero_division=0))

    # 5. INT8 Quantization and C++ Export
    quantize_and_export_tflite(model, X_train, header_path="model_data.h")
    print("\n✨ All operations completed successfully!")
