"""
Phase-2 CIS classifier — training harness.

Phase 1 is the rule classifier that ships now. Phase 2 trains a calibrated
multi-class model on RAW features, with SHAP explanations, once real outcome
labels exist. Those labels = actual measured delay (Mappls historical travel
time) binned into Low/Medium/High/Critical — we don't have them yet, so the
pipeline seeds `outcome_class` with the Phase-1 class as a PLACEHOLDER.

This script runs end-to-end on that placeholder, proving the full mechanism
(features -> LightGBM -> isotonic calibration -> SHAP -> class + confidence).
When real labels arrive, point `outcome_class` at them and rerun — nothing else
changes. The trained model loads in gridlock_pipeline.py's Phase2Classifier.

Run:  python cis_phase2_train.py            # reads outputs/cis_features.csv
Output: cis_phase2_model.txt (+ printed CV accuracy and top SHAP features)
"""
import json, os, sys
import numpy as np
import pandas as pd

FEATURES = ["observed_count", "latent_rate", "n_lanes", "road_throughput", "road_weight",
            "VLS", "COS", "ECS", "RPS", "poi_commercial", "poi_transit", "poi_institutional",
            "metro_500m", "recurrence", "concurrent", "avg_vwidth", "mean_vio_w"]
CLASSES = ["Low", "Medium", "High", "Critical"]


def find_features():
    for p in ["outputs/cis_features.csv", "../model/outputs/cis_features.csv", "cis_features.csv"]:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("cis_features.csv not found — run gridlock_pipeline.py first.")


def train(features_csv=None, model_out="cis_phase2_model.txt"):
    import lightgbm as lgb
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.isotonic import IsotonicRegression

    df = pd.read_csv(features_csv or find_features())
    label_col = "outcome_class" if "outcome_class" in df.columns else "cis_class"
    if label_col == "cis_class" or (df[label_col] == df.get("cis_class")).all():
        print("⚠ Training on PLACEHOLDER labels (Phase-1 class). Replace `outcome_class` "
              "with measured-delay bins for a real Phase-2 model.")
    cls_index = {c: i for i, c in enumerate(CLASSES)}
    df = df[df[label_col].isin(CLASSES)].copy()
    X = df[FEATURES].fillna(0.0)
    y = df[label_col].map(cls_index).values
    present = sorted(set(y))
    if len(present) < 2:
        print(f"Only one class present ({[CLASSES[i] for i in present]}); need ≥2 to train. "
              "Harness verified; awaiting label variety.")
        return None

    params = dict(objective="multiclass", num_class=len(CLASSES), learning_rate=0.05,
                  num_leaves=31, min_child_samples=20, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=5, verbose=-1, seed=42)

    # stratified CV for an honest accuracy/F1 estimate
    accs, f1s = [], []
    skf = StratifiedKFold(n_splits=min(5, min(np.bincount(y)[present])), shuffle=True, random_state=42)
    for tr, va in skf.split(X, y):
        m = lgb.train(params, lgb.Dataset(X.iloc[tr], y[tr]), num_boost_round=200)
        pred = m.predict(X.iloc[va]).argmax(1)
        accs.append(accuracy_score(y[va], pred))
        f1s.append(f1_score(y[va], pred, average="macro"))
    print(f"CV accuracy {np.mean(accs):.3f} ± {np.std(accs):.3f} | macro-F1 {np.mean(f1s):.3f}")

    # final model on all data
    model = lgb.train(params, lgb.Dataset(X, y), num_boost_round=300)
    model.save_model(model_out)

    # isotonic calibration of the max-class probability (Platt/isotonic per spec)
    proba = model.predict(X)
    conf = proba.max(1)
    correct = (proba.argmax(1) == y).astype(float)
    try:
        iso = IsotonicRegression(out_of_bounds="clip").fit(conf, correct)
        json.dump({"x": list(map(float, iso.X_thresholds_)), "y": list(map(float, iso.y_thresholds_))},
                  open(model_out.replace(".txt", "_calib.json"), "w"))
        print("Saved isotonic calibration.")
    except Exception as e:
        print("calibration skipped:", e)

    # SHAP — top features driving the class
    try:
        import shap
        expl = shap.TreeExplainer(model)
        sv = expl.shap_values(X.sample(min(1000, len(X)), random_state=1))
        imp = np.abs(np.array(sv)).mean(axis=(0, 1)) if isinstance(sv, list) else np.abs(sv).mean(0)
        top = pd.Series(imp, index=FEATURES).sort_values(ascending=False).head(5)
        print("Top SHAP features:\n" + top.to_string())
    except Exception as e:
        imp = pd.Series(model.feature_importance(), index=FEATURES).sort_values(ascending=False)
        print("Top features (gain):\n" + imp.head(5).to_string())
    print(f"Saved Phase-2 model -> {model_out}")
    return model


if __name__ == "__main__":
    train(sys.argv[1] if len(sys.argv) > 1 else None)
