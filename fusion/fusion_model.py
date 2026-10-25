"""
Fusion Layer - Water Quality Final Verdict
---------------------------------------------
Combines:
  1. IoT sensor readings (TDS, turbidity, temperature)
  2. CV model output (contamination class + confidence)

...into one final verdict: "Safe", "Needs Treatment", or "Contaminated".

Two modes are provided:

  A) RULE-BASED (rule_based_fusion) - uses WHO/BIS standard thresholds.
     Works immediately, no training needed. Good default + easy to
     explain in your report/viva.

  B) ML-BASED (train_fusion_classifier / ml_fusion) - trains a small
     Random Forest on labeled examples if you want a "real" ML model
     for the AI/DL requirement of the project. Optional upgrade once
     rule-based is working end-to-end.
"""

import json
import os

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier

FUSION_MODEL_PATH = os.path.join(os.path.dirname(__file__), "fusion_rf_model.joblib")

# Standard-ish safe ranges (BIS/WHO drinking water guidelines, simplified
# for a student project - cite BIS 10500:2012 in your report)
TDS_SAFE_MAX = 500       # ppm
TURBIDITY_SAFE_MAX = 5   # NTU
PH_SAFE_MIN = 6.5
PH_SAFE_MAX = 8.5
# NOTE: DO (dissolved oxygen) and ORP sensors are not part of this
# project's hardware (see esp32_arduino/sensor_reader.ino), so they are
# intentionally not evaluated here - only TDS, turbidity, pH and the CV
# model feed the verdict/recommendations.


# Plain-language advice for each parameter, shown alongside the issue
# when it's out of the safe range. Keep these short and actionable -
# good talking points for the report/viva too. Kept for backward
# compatibility (email alerts, any code that just reads "recommendations"
# as a flat list of strings) - the richer, inspector-style plan lives in
# DETAILED_PLANS / build_recommendation_plan() below.
RECOMMENDATIONS = {
    "tds": "TDS is high — use an RO (reverse osmosis) filter, or dilute with cleaner water before use.",
    "turbidity": "Turbidity is high — let the water settle, or run it through sediment/cloth filtration before use.",
    "ph": "pH is out of range — treat with a pH corrector (e.g. lime for acidic, alum for alkaline) or get it professionally corrected before use.",
    "cv": "Visual contamination detected — avoid drinking/bathing use until a lab test confirms safety; investigate the source (algae bloom, dumping, sewage inflow).",
}

VERDICT_ADVICE = {
    "Safe": "All readings are within safe limits. Continue periodic monitoring.",
    "Needs Treatment": "One issue detected — treat the specific parameter below before use.",
    "Contaminated": "Multiple issues detected — do not use this water without proper treatment and a lab test.",
}

# ---------------------------------------------------------------
# Detailed, inspector-style recommendation system
# ---------------------------------------------------------------
# For each parameter this gives a short-form summary (used in the
# flat "recommendations" list for compatibility), plus a full action
# plan an inspector would actually hand to whoever manages the water
# body: what to do right now, how to treat it, what to fix long-term
# so it doesn't recur, and when to retest. Severity (Mild/Moderate/
# Critical) is worked out from how far the reading is past the safe
# limit, and only changes the urgency framing, not the whole plan.

SEVERITY_RANK = {"Critical": 0, "Moderate": 1, "Mild": 2}

SEVERITY_URGENCY = {
    "Critical": "Act now",
    "Moderate": "Act within 48 hours",
    "Mild": "Act within the week",
}


def _severity(value, mild, moderate):
    """Generic severity picker for a value that is bad *above* a threshold.
    mild/moderate are the extra amount past the safe limit at which the
    tier steps up (e.g. mild=250 means 250 units past the limit)."""
    if value >= moderate:
        return "Critical"
    if value >= mild:
        return "Moderate"
    return "Mild"


def _severity_tds(tds):
    over = tds - TDS_SAFE_MAX
    return _severity(over, mild=0, moderate=700)  # >1200 total = Critical, >750 = Moderate


def _severity_turbidity(turbidity):
    over = turbidity - TURBIDITY_SAFE_MAX
    return _severity(over, mild=0, moderate=45)  # >50 NTU = Critical, >15 NTU = Moderate


def _severity_ph(ph):
    dist = max(PH_SAFE_MIN - ph, ph - PH_SAFE_MAX, 0)
    return _severity(dist, mild=0, moderate=2)  # >2 units out = Critical, >1 = Moderate


def _severity_cv(cv_result):
    conf = cv_result.get("top_confidence", 0) or 0
    if conf >= 0.75:
        return "Critical"
    if conf >= 0.5:
        return "Moderate"
    return "Mild"


DETAILED_PLANS = {
    "tds": {
        "parameter": "Total Dissolved Solids (TDS)",
        "immediate": "Flag the water body as unsuitable for drinking/irrigation until levels are rechecked; avoid drawing water from this point in the meantime.",
        "treatment": [
            "For a small/contained body: do a partial water exchange with a cleaner source to dilute dissolved solids.",
            "If a supply point, route through an RO (reverse osmosis) or ion-exchange unit before further use.",
            "Trace and shut off any nearby discharge (industrial effluent, road-salt runoff, agricultural drainage) feeding the source.",
        ],
        "long_term": "Install or upgrade runoff interception upstream (retention basin, constructed wetland) and inspect known discharge points on a monthly schedule.",
        "retest": "Recheck 24–48 hours after any intervention, then weekly until three consecutive readings are back under {limit} ppm.".format(limit=TDS_SAFE_MAX),
    },
    "turbidity": {
        "parameter": "Turbidity",
        "immediate": "Avoid disturbing bed sediment near the sampling point (boating, dredging, wading) and pause any recreational use if this is a public body.",
        "treatment": [
            "Let the water stand and settle before reuse, or run it through sediment/cloth filtration for smaller volumes.",
            "For larger contained bodies, dose with a flocculant (alum / polyaluminium chloride) at the correct rate to speed up clarification.",
            "Install silt fences or sediment traps at the inflow points that are carrying the cloudiness in.",
        ],
        "long_term": "Stabilise eroding banks with vegetation or riprap, maintain a vegetated buffer strip along inflows, and schedule dredging if turbidity stays chronically high.",
        "retest": "Recheck after 24 hours of settling, then every two weeks.",
    },
    "ph": {
        "parameter": "pH",
        "immediate": "Halt any suspected nearby chemical discharge and hold off on stocking aquatic life or drawing drinking water until pH is confirmed back in range.",
        "treatment": [
            "If acidic (low pH): apply agricultural lime (calcium carbonate) at a lab-calculated dose.",
            "If alkaline (high pH): apply alum or gypsum at a lab-calculated dose.",
            "Retest before scaling any correction up to the full water body — small trial doses first.",
        ],
        "long_term": "Identify and regulate industrial or agricultural discharges affecting pH; set up a routine liming/buffering schedule if the source is chronically acidic (e.g. acid rain, acid mine drainage runoff).",
        "retest": "Recheck 48 hours after treatment, then weekly.",
    },
    "cv": {
        "parameter": "Visual / CV Contamination",
        "immediate": "Restrict the water body from all use (drinking, bathing, irrigation) until lab-confirmed safe; photograph/log the extent, and notify the local health or environmental authority if this is a public body.",
        "treatment": [
            "Skim and remove visible contaminant matter (algae mats, floating debris).",
            "If it's a confirmed algal bloom, apply an algaecide only under expert/regulatory guidance — don't self-dose a public water body.",
            "If it looks like sewage or a chemical spill, do not disturb the area — escalate to the relevant authority instead of treating it yourself.",
        ],
        "long_term": "Track down and eliminate the pollution source (sewage inflow, illegal dumping, agricultural runoff), increase inspection frequency at this point, and post advisory signage if the body is used by the public.",
        "retest": "Recheck immediately after remediation, then daily for the following week.",
    },
}


def build_recommendation_plan(tds, turbidity, ph, cv_result,
                               tds_bad, turbidity_bad, ph_bad, cv_bad):
    """Builds the full inspector-style action plan for every parameter
    currently out of range, ordered most urgent (Critical) first."""
    plan = []

    def _add(key, severity, reading_str, safe_range_str):
        base = DETAILED_PLANS[key]
        plan.append({
            "parameter": base["parameter"],
            "severity": severity,
            "urgency": SEVERITY_URGENCY[severity],
            "reading": reading_str,
            "safe_range": safe_range_str,
            "summary": RECOMMENDATIONS[key],
            "immediate_action": base["immediate"],
            "treatment_steps": base["treatment"],
            "long_term": base["long_term"],
            "retest_interval": base["retest"],
        })

    if tds_bad:
        _add("tds", _severity_tds(tds), f"{tds:.0f} ppm", f"≤ {TDS_SAFE_MAX} ppm")
    if turbidity_bad:
        _add("turbidity", _severity_turbidity(turbidity), f"{turbidity:.1f} NTU", f"≤ {TURBIDITY_SAFE_MAX} NTU")
    if ph_bad:
        _add("ph", _severity_ph(ph), f"{ph:.1f}", f"{PH_SAFE_MIN}–{PH_SAFE_MAX}")
    if cv_bad:
        top_conf = cv_result.get("top_confidence", 0) or 0
        _add("cv", _severity_cv(cv_result), f"{top_conf:.0%} confidence", "no visible contamination")

    plan.sort(key=lambda e: SEVERITY_RANK[e["severity"]])
    return plan


def rule_based_fusion(tds, turbidity, temperature, cv_result, ph=None):
    """
    cv_result: dict from cv_model/predict_yolo.py, e.g.
      {"detections": [{"class": "algae", "confidence": 0.82}, ...],
       "is_contaminated": true, "top_confidence": 0.82}
    ph: optional, included if that sensor is connected. (DO/ORP are not
    part of this project's hardware, see the NOTE near the top of this
    file, so they're not accepted or evaluated here.)
    """
    issues = []
    recommendations = []

    tds_bad = tds > TDS_SAFE_MAX
    turbidity_bad = turbidity > TURBIDITY_SAFE_MAX
    ph_bad = ph is not None and (ph < PH_SAFE_MIN or ph > PH_SAFE_MAX)
    cv_bad = bool(cv_result.get("is_contaminated"))

    if tds_bad:
        issues.append(f"TDS too high ({tds:.0f} ppm > {TDS_SAFE_MAX} ppm)")
        recommendations.append(RECOMMENDATIONS["tds"])
    if turbidity_bad:
        issues.append(f"Turbidity too high ({turbidity:.1f} NTU > {TURBIDITY_SAFE_MAX} NTU)")
        recommendations.append(RECOMMENDATIONS["turbidity"])
    if ph_bad:
        issues.append(f"pH out of safe range ({ph:.1f}, safe is {PH_SAFE_MIN}-{PH_SAFE_MAX})")
        recommendations.append(RECOMMENDATIONS["ph"])
    if cv_bad:
        detected_names = ", ".join(
            f"{d['class']} ({d['confidence']:.0%})" for d in cv_result["detections"]
        )
        issues.append(f"CV model detected: {detected_names}")
        recommendations.append(RECOMMENDATIONS["cv"])

    if not issues:
        verdict = "Safe"
    elif len(issues) == 1:
        verdict = "Needs Treatment"
    else:
        verdict = "Contaminated"

    recommendation_plan = build_recommendation_plan(
        tds, turbidity, ph, cv_result,
        tds_bad, turbidity_bad, ph_bad, cv_bad,
    )
    priority_action = recommendation_plan[0]["immediate_action"] if recommendation_plan else None

    return {
        "verdict": verdict,
        "issues": issues,
        "recommendations": recommendations,
        "recommendation_plan": recommendation_plan,
        "priority_action": priority_action,
        "overall_advice": VERDICT_ADVICE[verdict],
        "sensor_readings": {
            "tds": tds, "turbidity": turbidity, "temperature": temperature,
            "ph": ph,
        },
        "cv_result": cv_result,
    }


# ---------------------------------------------------------------
# Optional: ML-based fusion (Random Forest) if you want to show a
# trained AI model combining sensor + CV features, not just if/else
# rules. Needs a small CSV of labeled examples to train on - see
# sample_training_data.csv for the expected format.
# ---------------------------------------------------------------

def train_fusion_classifier(csv_path):
    """
    csv_path should have columns:
      tds, turbidity, temperature, cv_confidence, cv_is_contaminated, label
    where label is 0=Safe, 1=Needs Treatment, 2=Contaminated
    """
    import pandas as pd

    df = pd.read_csv(csv_path)
    X = df[["tds", "turbidity", "temperature", "cv_confidence", "cv_is_contaminated"]]
    y = df["label"]

    clf = RandomForestClassifier(n_estimators=100, random_state=42)
    clf.fit(X, y)

    joblib.dump(clf, FUSION_MODEL_PATH)
    print(f"Fusion model trained and saved to {FUSION_MODEL_PATH}")
    return clf


def ml_fusion(tds, turbidity, temperature, cv_result):
    if not os.path.exists(FUSION_MODEL_PATH):
        raise FileNotFoundError(
            "No trained fusion model found. Run train_fusion_classifier() "
            "first, or just use rule_based_fusion() instead."
        )
    clf = joblib.load(FUSION_MODEL_PATH)

    cv_is_contaminated = 1 if cv_result.get("is_contaminated") else 0
    features = np.array([[tds, turbidity, temperature, cv_result.get("top_confidence", 0.0), cv_is_contaminated]])

    label_map = {0: "Safe", 1: "Needs Treatment", 2: "Contaminated"}
    prediction = clf.predict(features)[0]

    return {
        "verdict": label_map[int(prediction)],
        "sensor_readings": {"tds": tds, "turbidity": turbidity, "temperature": temperature},
        "cv_result": cv_result,
    }


if __name__ == "__main__":
    # Quick manual test
    dummy_cv_result = {
        "detections": [{"class": "algae", "confidence": 0.75}],
        "is_contaminated": True,
        "top_confidence": 0.75,
    }
    result = rule_based_fusion(tds=650, turbidity=8, temperature=26, cv_result=dummy_cv_result)
    print(json.dumps(result, indent=2))
