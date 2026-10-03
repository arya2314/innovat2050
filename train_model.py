# Google Colab / Local - TinyML Worker Health Monitoring Training Pipeline
# ══════════════════════════════════════════════════════════════════════════════
# Trains a 2-Parameter (Heart Rate & Body Temperature) 1D-CNN TinyML Model
# on the real 'cleaned_master.csv' dataset and exports a C-binary header (model_data.h)
# for direct deployment on ESP32-S3 microcontroller flash.
# ══════════════════════════════════════════════════════════════════════════════

import os
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers

print("TensorFlow Version:", tf.__version__)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Load Real Cleaned Dataset or Fallback Generator
# ══════════════════════════════════════════════════════════════════════════════

def load_real_worker_health_data(
    csv_path="cleaned_master.csv",
    window_size=60,
    stride=15,
    test_split=0.2
):
    """
    Loads 'cleaned_master.csv', extracts 60-sample sliding windows of
    (Heart Rate, Body Temperature), and maps risk conditions to 4 target classes:
      0: Normal (Healthy Worker)
      1: High HR (Physical Exertion / Fatigue)
      2: High Temp (Fever / Ambient Heat Stress)
      3: CRITICAL (High HR + High Temp -> Heat Stroke Risk)
    """
    if not os.path.exists(csv_path):
        print(f"[INFO] '{csv_path}' not found in current directory. Falling back to synthetic generator.")
        return generate_synthetic_worker_data(num_samples=6000, window_size=window_size)

    print(f"\n--- Loading Real Dataset from: {csv_path} ---")
    df = pd.read_csv(csv_path)
    print(f"Total rows in dataset: {len(df):,}")

    # Ensure required columns exist
    if "hr_bpm" not in df.columns or "body_temp_c" not in df.columns:
        raise ValueError("Dataset must contain 'hr_bpm' and 'body_temp_c' columns.")

    # Drop missing values
    clean_df = df.dropna(subset=["hr_bpm", "body_temp_c"]).copy()
    print(f"Valid vitals rows: {len(clean_df):,}")

    # Map risk levels to integer classes (0..3)
    label_map = {
        "NORMAL": 0,
        "HIGH_HR_EXERTION": 1,
        "HIGH_TEMP_HEAT_STRESS": 2,
        "CRITICAL_HEAT_STROKE": 3,
        "LOW_VITALS_ALERT": 0,  # Bradycardia/Hypothermia baseline mapped to 0 or 1
    }
    
    if "risk_level" in clean_df.columns:
        risk_series = clean_df["risk_level"].map(label_map).fillna(0).values.astype(np.int32)
    else:
        # Auto-compute rule-based labels if risk_level column is missing
        hr = clean_df["hr_bpm"].values
        temp = clean_df["body_temp_c"].values
        high_hr = hr > 115.0
        high_temp = temp > 37.8
        critical = high_hr & high_temp
        
        risk_series = np.zeros(len(clean_df), dtype=np.int32)
        risk_series[high_hr & ~critical] = 1
        risk_series[high_temp & ~critical] = 2
        risk_series[critical] = 3

    features = clean_df[["hr_bpm", "body_temp_c"]].values.astype(np.float32)

    # Extract 60-sample sliding windows (shape: Samples x 60 x 2)
    X_windows = []
    y_windows = []

    for start in range(0, len(features) - window_size + 1, stride):
        win_x = features[start : start + window_size]
        # Label is the maximum severity condition occurring within the window
        win_y = int(np.max(risk_series[start : start + window_size]))
        X_windows.append(win_x)
        y_windows.append(win_y)

    X = np.array(X_windows, dtype=np.float32)
    y = np.array(y_windows, dtype=np.int32)

    # Shuffle dataset
    indices = np.arange(len(X))
    np.random.seed(42)
    np.random.shuffle(indices)
    X = X[indices]
    y = y[indices]

    # Train / Test split
    split_idx = int((1.0 - test_split) * len(X))
    X_train, y_train = X[:split_idx], y[:split_idx]
    X_test, y_test = X[split_idx:], y[split_idx:]

    print(f"Extracted Windows -> Train: {X_train.shape}, Test: {X_test.shape}")
    print(f"Class distribution in Training: {np.bincount(y_train, minlength=4)}")

    return X_train, y_train, X_test, y_test


def generate_synthetic_worker_data(num_samples=6000, window_size=60):
    """Synthetic generator for testing or augmenting scarce classes."""
    print("\n--- Generating Synthetic Worker Health Data ---")
    X = np.zeros((num_samples, window_size, 2), dtype=np.float32)
    y = np.zeros((num_samples,), dtype=np.int32)

    for i in range(num_samples):
        condition = np.random.choice([0, 1, 2, 3], p=[0.4, 0.25, 0.2, 0.15])
        if condition == 0:
            hr_base = np.random.uniform(60, 95)
            temp_base = np.random.uniform(36.2, 37.2)
        elif condition == 1:
            hr_base = np.random.uniform(115, 155)
            temp_base = np.random.uniform(36.5, 37.4)
        elif condition == 2:
            hr_base = np.random.uniform(75, 105)
            temp_base = np.random.uniform(38.0, 39.3)
        else:
            hr_base = np.random.uniform(125, 165)
            temp_base = np.random.uniform(38.5, 40.2)

        X[i, :, 0] = hr_base + np.random.normal(0, 1.5, size=(window_size,))
        X[i, :, 1] = temp_base + np.random.normal(0, 0.1, size=(window_size,))
        y[i] = condition

    split = int(0.8 * num_samples)
    return X[:split], y[:split], X[split:], y[split:]


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Define Lightweight 1D-CNN Model for ESP32
# ══════════════════════════════════════════════════════════════════════════════

def build_worker_health_model(input_shape=(60, 2), num_classes=4):
    """
    Constructs an ultra-lightweight 1D-CNN model designed for real-time
    inference on ESP32-S3 microcontrollers (<15 KB RAM footprint).
    """
    print("\n--- Building 1D-CNN TinyML Model ---")
    model = keras.Sequential([
        layers.Input(shape=input_shape),

        layers.Conv1D(filters=8, kernel_size=3, activation="relu", padding="same"),
        layers.MaxPooling1D(pool_size=2),

        layers.Conv1D(filters=16, kernel_size=3, activation="relu", padding="same"),
        layers.MaxPooling1D(pool_size=2),

        layers.Flatten(),
        layers.Dropout(0.25),
        layers.Dense(16, activation="relu"),
        layers.Dense(num_classes, activation="softmax")
    ])

    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=0.001),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"]
    )
    model.summary()
    return model


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Full INT8 Quantization for Microcontrollers
# ══════════════════════════════════════════════════════════════════════════════

def quantize_tflite_model(keras_model, X_train):
    """Quantizes model weights and activations to 8-bit integer (INT8)."""
    print("\n--- Quantizing Model to 8-bit Integer (INT8) ---")
    converter = tf.lite.TFLiteConverter.from_keras_model(keras_model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]

    def representative_dataset_gen():
        for i in range(min(100, len(X_train))):
            sample = np.expand_dims(X_train[i], axis=0).astype(np.float32)
            yield [sample]

    converter.representative_dataset = representative_dataset_gen
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8

    tflite_quant = converter.convert()
    return tflite_quant


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: Export to C++ Header File for ESP32 Firmware
# ══════════════════════════════════════════════════════════════════════════════

def write_tflite_header(tflite_model, filename="model_data.h"):
    """Generates the C byte array header file for Arduino / ESP-IDF."""
    print(f"\n--- Exporting Quantized Model to C++ Header: {filename} ---")
    bytes_list = list(tflite_model)
    model_len = len(bytes_list)

    with open(filename, "w") as f:
        f.write("// Worker Health TinyML Quantized Model (2 Parameters: Heart Rate & Temperature)\n")
        f.write(f"// Model Size in Flash: {model_len} bytes\n\n")
        f.write("#ifndef WORKER_MODEL_DATA_H_\n")
        f.write("#define WORKER_MODEL_DATA_H_\n\n")
        f.write(f"const unsigned int g_model_len = {model_len};\n\n")
        f.write("alignas(8) const unsigned char g_model[] = {\n    ")

        for idx, val in enumerate(bytes_list):
            f.write(f"0x{val:02x}")
            if idx < model_len - 1:
                f.write(", ")
            if (idx + 1) % 12 == 0 and idx < model_len - 1:
                f.write("\n    ")

        f.write("\n};\n\n")
        f.write("#endif // WORKER_MODEL_DATA_H_\n")

    print(f"✅ Header file '{filename}' generated successfully! Flash footprint: {model_len:,} bytes.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN TRAINING PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 1. Load real cleaned dataset
    X_train, y_train, X_test, y_test = load_real_worker_health_data(
        csv_path="cleaned_master.csv",
        window_size=60,
        stride=15
    )

    # 2. Build 1D-CNN Model
    model = build_worker_health_model(input_shape=(60, 2), num_classes=4)

    # 3. Train Model
    print("\n--- Training Worker Health Classification Model ---")
    history = model.fit(
        X_train, y_train,
        epochs=12,
        batch_size=32,
        validation_split=0.15,
        verbose=1
    )

    # 4. Evaluate on Test Set
    loss, acc = model.evaluate(X_test, y_test, verbose=0)
    print(f"\n🎯 Test Set Accuracy: {acc * 100:.2f}% | Loss: {loss:.4f}")

    # 5. Quantize to INT8 TFLite
    tflite_quant = quantize_tflite_model(model, X_train)

    # 6. Export to C++ header for ESP32
    write_tflite_header(tflite_quant, filename="model_data.h")
