# ══════════════════════════════════════════════════════════════════════════════
# Novelty Topic: Multi-Task TinyML for Industrial Wearables (ESP32-S3)
# Task 1: Activity & Fall Recognition (Kinematic Branch: Accel X/Y/Z)
# Task 2: Heat Stress & Fatigue Early Warning (Physiological Branch: HR/Temp/SpO2)
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
print("🚀 MULTI-TASK TINYML TRAINING PIPELINE: ACTIVITY/FALL & HEAT STRESS TRIAGE")
print(f"   TensorFlow Version: {tf.__version__}")
print("=" * 80)

# Set random seeds for reproducibility
np.random.seed(42)
tf.random.set_seed(42)

# ══════════════════════════════════════════════════════════════════════════════
# 1. SYNTHETIC MULTI-MODAL DATA GENERATOR (Realistic Fallback)
# ══════════════════════════════════════════════════════════════════════════════

def generate_multitask_synthetic_dataset(num_samples=8000, window_size=60):
    """
    Generates synchronized 6-channel time series:
      Channels 0..2: Kinematics [accel_x, accel_y, accel_z] (g)
      Channels 3..5: Physiological [hr_bpm, body_temp_c, spo2_percent]

    Dual Targets:
      y_activity (4 classes):
        0: Resting / Sedentary
        1: Moderate Work / Walking
        2: Heavy Physical Labor
        3: Fall / Impact Event
      y_health (4 classes):
        0: Normal / Safe
        1: High Exertion / Fatigue (HR > 120 bpm)
        2: Heat Stress / Ambient Heat Strain (Temp > 38.0°C)
        3: Critical Heat Stroke Risk (High HR + High Temp)
    """
    print(f"\n[INFO] Generating {num_samples} multi-modal synthetic windows (6 channels x {window_size} timesteps)...")

    X = np.zeros((num_samples, window_size, 6), dtype=np.float32)
    y_activity = np.zeros((num_samples,), dtype=np.int32)
    y_health = np.zeros((num_samples,), dtype=np.int32)

    for i in range(num_samples):
        # Sample activity and health conditions
        act = np.random.choice([0, 1, 2, 3], p=[0.35, 0.35, 0.20, 0.10])
        hlth = np.random.choice([0, 1, 2, 3], p=[0.45, 0.25, 0.18, 0.12])

        # Base vitals
        if hlth == 0:  # Normal
            hr_mean = np.random.uniform(62, 88)
            temp_mean = np.random.uniform(36.4, 37.1)
            spo2_mean = np.random.uniform(96.0, 99.5)
        elif hlth == 1:  # Exertion / High HR
            hr_mean = np.random.uniform(120, 160)
            temp_mean = np.random.uniform(36.8, 37.4)
            spo2_mean = np.random.uniform(94.0, 98.0)
        elif hlth == 2:  # Heat Stress / High Temp
            hr_mean = np.random.uniform(85, 115)
            temp_mean = np.random.uniform(38.2, 39.4)
            spo2_mean = np.random.uniform(93.0, 97.0)
        else:  # Critical Heat Stroke
            hr_mean = np.random.uniform(130, 175)
            temp_mean = np.random.uniform(38.8, 40.5)
            spo2_mean = np.random.uniform(90.0, 95.0)

        # Base kinematics
        t = np.linspace(0, 2 * np.pi, window_size)
        if act == 0:  # Resting
            acc_x = np.random.normal(0.02, 0.04, window_size)
            acc_y = np.random.normal(0.01, 0.04, window_size)
            acc_z = np.random.normal(0.98, 0.04, window_size)
        elif act == 1:  # Moderate Work / Walking
            freq = np.random.uniform(1.2, 1.8)
            acc_x = 0.3 * np.sin(freq * t) + np.random.normal(0, 0.1, window_size)
            acc_y = 0.2 * np.cos(freq * t) + np.random.normal(0, 0.1, window_size)
            acc_z = 0.98 + 0.4 * np.sin(2 * freq * t) + np.random.normal(0, 0.12, window_size)
        elif act == 2:  # Heavy Labor
            freq = np.random.uniform(2.0, 3.5)
            acc_x = 0.8 * np.sin(freq * t) + np.random.normal(0, 0.25, window_size)
            acc_y = 0.6 * np.cos(freq * t) + np.random.normal(0, 0.25, window_size)
            acc_z = 0.98 + 0.9 * np.sin(freq * t) + np.random.normal(0, 0.3, window_size)
        else:  # Fall / Impact
            acc_x = np.random.normal(0, 0.1, window_size)
            acc_y = np.random.normal(0, 0.1, window_size)
            acc_z = np.random.normal(0.98, 0.1, window_size)
            # Inject impact spike in the middle of window
            impact_idx = np.random.randint(20, 40)
            acc_x[impact_idx : impact_idx + 4] += np.random.uniform(2.5, 4.5)
            acc_y[impact_idx : impact_idx + 4] += np.random.uniform(2.0, 4.0)
            acc_z[impact_idx : impact_idx + 4] = np.random.uniform(-0.8, -2.5)  # freefall + ground hit

        # Combine Channels
        X[i, :, 0] = acc_x
        X[i, :, 1] = acc_y
        X[i, :, 2] = acc_z
        X[i, :, 3] = hr_mean + np.random.normal(0, 1.5, window_size)
        X[i, :, 4] = temp_mean + np.random.normal(0, 0.08, window_size)
        X[i, :, 5] = spo2_mean + np.random.normal(0, 0.3, window_size)

        y_activity[i] = act
        y_health[i] = hlth

    return X, y_activity, y_health


# ══════════════════════════════════════════════════════════════════════════════
# 2. DATA LOADER & PREPROCESSING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def load_multitask_data(csv_path="cleaned_master.csv", window_size=60, stride=15, test_split=0.2):
    """
    Loads unified dataset if available, extracts 6-channel sliding windows,
    or smoothly falls back to the realistic multi-task synthetic generator.
    """
    if not os.path.exists(csv_path):
        print(f"[WARN] '{csv_path}' not found. Using high-fidelity synthetic multi-task generator.")
        X, y_act, y_hlth = generate_multitask_synthetic_dataset(num_samples=7500, window_size=window_size)
    else:
        print(f"\n[INFO] Loading dataset from: {csv_path}")
        df = pd.read_csv(csv_path)

        # Fill missing multi-modal columns if needed
        required_cols = ["accel_x", "accel_y", "accel_z", "hr_bpm", "body_temp_c", "spo2_percent"]
        for col in required_cols:
            if col not in df.columns:
                if "accel" in col:
                    df[col] = 0.98 if col == "accel_z" else 0.0
                elif col == "spo2_percent":
                    df[col] = 98.0
                elif col == "hr_bpm":
                    df[col] = 75.0
                elif col == "body_temp_c":
                    df[col] = 36.8

        df = df[required_cols].interpolate(method="linear").bfill().ffill()

        # Compute Rule-based Dual-Task Targets
        hr = df["hr_bpm"].values
        temp = df["body_temp_c"].values
        ax = df["accel_x"].values
        ay = df["accel_y"].values
        az = df["accel_z"].values
        acc_mag = np.sqrt(ax**2 + ay**2 + az**2)

        # Health labels (0: Normal, 1: High HR, 2: High Temp, 3: Critical)
        hlth_arr = np.zeros(len(df), dtype=np.int32)
        hlth_arr[(hr > 115.0) & (temp <= 37.8)] = 1
        hlth_arr[(temp > 37.8) & (hr <= 115.0)] = 2
        hlth_arr[(hr > 115.0) & (temp > 37.8)] = 3

        # Activity labels (0: Resting, 1: Walking, 2: Heavy Work, 3: Fall)
        act_arr = np.zeros(len(df), dtype=np.int32)
        act_arr[(acc_mag >= 1.15) & (acc_mag < 1.6)] = 1
        act_arr[(acc_mag >= 1.6) & (acc_mag < 2.8)] = 2
        act_arr[acc_mag >= 2.8] = 3

        feat_matrix = df[required_cols].values.astype(np.float32)

        X_list, y_act_list, y_hlth_list = [], [], []
        for start in range(0, len(feat_matrix) - window_size + 1, stride):
            X_list.append(feat_matrix[start : start + window_size])
            y_act_list.append(int(np.max(act_arr[start : start + window_size])))
            y_hlth_list.append(int(np.max(hlth_arr[start : start + window_size])))

        X = np.array(X_list, dtype=np.float32)
        y_act = np.array(y_act_list, dtype=np.int32)
        y_hlth = np.array(y_hlth_list, dtype=np.int32)

    # Feature Normalization (Z-score per channel for stable TinyML integer quantization)
    means = np.mean(X, axis=(0, 1), keepdims=True)
    stds = np.std(X, axis=(0, 1), keepdims=True) + 1e-6
    X_norm = (X - means) / stds

    # Train / Test Split
    indices = np.arange(len(X_norm))
    np.random.shuffle(indices)
    split = int((1.0 - test_split) * len(X_norm))

    train_idx, test_idx = indices[:split], indices[split:]

    X_train, X_test = X_norm[train_idx], X_norm[test_idx]
    y_act_train, y_act_test = y_act[train_idx], y_act[test_idx]
    y_hlth_train, y_hlth_test = y_hlth[train_idx], y_hlth[test_idx]

    print(f"\n📊 Dataset Split:")
    print(f"   • Train Windows: {X_train.shape} | Test Windows: {X_test.shape}")
    print(f"   • Activity Classes (Train): {np.bincount(y_act_train, minlength=4)}")
    print(f"   • Health Classes   (Train): {np.bincount(y_hlth_train, minlength=4)}")

    return (X_train, y_act_train, y_hlth_train), (X_test, y_act_test, y_hlth_test), (means, stds)


# ══════════════════════════════════════════════════════════════════════════════
# 3. NOVEL MULTI-TASK TINYML ARCHITECTURE WITH TEMPORAL ATTENTION
# ══════════════════════════════════════════════════════════════════════════════

def build_multitask_tinyml_model(input_shape=(60, 6), num_activity_classes=4, num_health_classes=4):
    """
    Constructs a Multi-Task 1D-CNN + Channel Attention TinyML Architecture:
      - Shared Feature Backbone: Depthwise Separable 1D Convolutions (ultra-low parameter count)
      - Squeeze-and-Excitation Temporal Attention Block (boosts salient spike detection)
      - Head 1: Activity & Fall Classification (Softmax)
      - Head 2: Heat Stroke & Exertion Triage (Softmax)
    """
    inputs = layers.Input(shape=input_shape, name="sensor_window_input")

    # Shared Temporal Feature Extractor
    x = layers.SeparableConv1D(filters=16, kernel_size=3, padding="same", activation="relu")(inputs)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPooling1D(pool_size=2)(x)

    x = layers.SeparableConv1D(filters=32, kernel_size=3, padding="same", activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPooling1D(pool_size=2)(x)

    # Lightweight Squeeze-and-Excitation (SE) Channel Attention
    se = layers.GlobalAveragePooling1D()(x)
    se = layers.Dense(8, activation="relu")(se)
    se = layers.Dense(32, activation="sigmoid")(se)
    se = layers.Reshape((1, 32))(se)
    x = layers.Multiply()([x, se])

    # Shared Latent Representation
    shared_features = layers.GlobalAveragePooling1D(name="shared_pooling")(x)
    shared_features = layers.Dropout(0.25)(shared_features)

    # ── Task Head 1: Activity & Fall Recognition ──
    act_dense = layers.Dense(16, activation="relu", name="act_dense")(shared_features)
    act_out = layers.Dense(num_activity_classes, activation="softmax", name="activity_output")(act_dense)

    # ── Task Head 2: Heat Stress & Fatigue Triage ──
    hlth_dense = layers.Dense(16, activation="relu", name="hlth_dense")(shared_features)
    hlth_out = layers.Dense(num_health_classes, activation="softmax", name="health_output")(hlth_dense)

    model = keras.Model(inputs=inputs, outputs=[act_out, hlth_out], name="MultiTask_TinyML_WorkerHealth")

    # Multi-Task Joint Loss Optimization
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=0.002),
        loss={
            "activity_output": "sparse_categorical_crossentropy",
            "health_output": "sparse_categorical_crossentropy",
        },
        loss_weights={
            "activity_output": 1.0,
            "health_output": 1.2,  # Prioritize critical health triage safety
        },
        metrics={
            "activity_output": ["accuracy"],
            "health_output": ["accuracy"],
        }
    )

    return model


# ══════════════════════════════════════════════════════════════════════════════
# 4. FULL INT8 QUANTIZATION FOR ESP32-S3 MICROCONTROLLERS
# ══════════════════════════════════════════════════════════════════════════════

def quantize_multitask_model(keras_model, X_train):
    """
    Quantizes model weights and activations to INT8 precision using representative dataset.
    Compatible with ESP32-S3 ESP-NN / TensorFlow Lite Micro runtime.
    """
    print("\n--- Performing Full INT8 Quantization for Edge Deployment ---")
    converter = tf.lite.TFLiteConverter.from_keras_model(keras_model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]

    def representative_dataset_gen():
        for i in range(min(150, len(X_train))):
            sample = np.expand_dims(X_train[i], axis=0).astype(np.float32)
            yield [sample]

    converter.representative_dataset = representative_dataset_gen
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8

    tflite_quant_model = converter.convert()
    print(f"✅ Quantization complete! Model size: {len(tflite_quant_model):,} bytes.")
    return tflite_quant_model


# ══════════════════════════════════════════════════════════════════════════════
# 5. C++ HEADER EXPORT FOR ESP32-S3 FIRMWARE
# ══════════════════════════════════════════════════════════════════════════════

def export_to_cpp_header(tflite_model, filename="model_data.h"):
    """
    Exports TFLite byte array into C++ header file for direct embedding in ESP32 firmware.
    """
    print(f"\n--- Writing C++ Embedded Header: {filename} ---")
    model_len = len(tflite_model)

    with open(filename, "w") as f:
        f.write("// ============================================================================\n")
        f.write("// Multi-Task TinyML Model Header for ESP32-S3\n")
        f.write("// Task 1: Activity & Fall Detection | Task 2: Heat Stroke & Fatigue Triage\n")
        f.write(f"// Size in Microcontroller Flash: {model_len:,} bytes\n")
        f.write("// ============================================================================\n\n")
        f.write("#ifndef MULTITASK_WORKER_MODEL_H_\n")
        f.write("#define MULTITASK_WORKER_MODEL_H_\n\n")
        f.write(f"const unsigned int g_model_len = {model_len};\n\n")
        f.write("alignas(8) const unsigned char g_model[] = {\n    ")

        for idx, val in enumerate(list(tflite_model)):
            f.write(f"0x{val:02x}")
            if idx < model_len - 1:
                f.write(", ")
            if (idx + 1) % 12 == 0 and idx < model_len - 1:
                f.write("\n    ")

        f.write("\n};\n\n")
        f.write("#endif // MULTITASK_WORKER_MODEL_H_\n")

    print(f"✅ Header '{filename}' saved successfully! ({model_len / 1024:.2f} KB)")


# ══════════════════════════════════════════════════════════════════════════════
# 6. MAIN EXECUTION PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 1. Load Data
    (X_train, y_act_train, y_hlth_train), (X_test, y_act_test, y_hlth_test), (norm_mean, norm_std) = load_multitask_data(
        csv_path="cleaned_master.csv",
        window_size=60,
        stride=15
    )

    # 2. Build Multi-Task Model
    model = build_multitask_tinyml_model(
        input_shape=(60, 6),
        num_activity_classes=4,
        num_health_classes=4
    )
    model.summary()

    # 3. Train Multi-Task Model
    print("\n--- Training Multi-Task Model ---")
    lr_scheduler = keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=3, min_lr=1e-5)
    early_stop = keras.callbacks.EarlyStopping(monitor="val_loss", patience=6, restore_best_weights=True)

    history = model.fit(
        X_train,
        {"activity_output": y_act_train, "health_output": y_hlth_train},
        validation_split=0.15,
        epochs=20,
        batch_size=32,
        callbacks=[lr_scheduler, early_stop],
        verbose=1
    )

    # 4. Comprehensive Test Evaluation
    eval_results = model.evaluate(X_test, {"activity_output": y_act_test, "health_output": y_hlth_test}, verbose=0)
    print("\n" + "=" * 60)
    print("🎯 MULTI-TASK EVALUATION METRICS:")
    print(f"   • Total Loss:               {eval_results[0]:.4f}")
    print(f"   • Activity Task Accuracy:   {eval_results[3] * 100:.2f}%")
    print(f"   • Health Triage Accuracy:   {eval_results[4] * 100:.2f}%")
    print("=" * 60)

    # Task Predictions & Classification Reports
    preds = model.predict(X_test)
    y_pred_act = np.argmax(preds[0], axis=1)
    y_pred_hlth = np.argmax(preds[1], axis=1)

    act_names = ["Resting", "Walking", "Heavy Work", "Fall Detected"]
    hlth_names = ["Normal", "High Exertion", "Heat Stress", "Critical Heat Stroke"]

    print("\n📋 Activity & Fall Classification Report:")
    print(classification_report(y_act_test, y_pred_act, target_names=act_names, zero_division=0))

    print("\n📋 Health & Heat Stress Triage Report:")
    print(classification_report(y_hlth_test, y_pred_hlth, target_names=hlth_names, zero_division=0))

    # 5. Quantize to Full INT8
    tflite_quant = quantize_multitask_model(model, X_train)

    # 6. Save TFLite binary & C++ header
    with open("multitask_model_quant.tflite", "wb") as f:
        f.write(tflite_quant)

    export_to_cpp_header(tflite_quant, filename="model_data.h")
    print("\n✨ Multi-Task Model Training & Edge Export Pipeline Completed Successfully!")
