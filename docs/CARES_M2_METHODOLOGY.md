# CARES Milestone 2 Methodology Specification

## 1. Experimental Integrity & Subject Separation

To prevent data leakage and ensure realistic scientific evaluation:

- **Strict Subject Separation**: Time series samples or sliding windows from the same participant are **NEVER** randomly split into both training and test sets. Human physiological baselines, autonomic reactivity, and heart rate dynamics exhibit high intra-subject autocorrelation. Random window splitting results in severe data leakage and artificially inflated performance metrics.
- **Leave-One-Subject-Out (LOSO) Cross-Validation**: Evaluation is performed using Leave-One-Subject-Out cross-validation across all 15 subjects in the benchmark dataset. In each fold $k \in \{2, 3, \dots, 17\} \setminus \{12\}$, subject $k$ is held out exclusively as the test set, while the remaining 14 subjects are used for preprocessing parameter estimation and threshold derivation.

---

## 2. Baseline Normalization Analysis

We evaluate four physiological input representations to test whether personalized baseline normalization improves stress/cognitive-risk discrimination compared to static global values:

1. **Raw Heart Rate ($HR_{raw}$ in BPM)**: Un-normalized absolute heart rate values.
2. **Absolute Baseline Deviation ($\Delta HR = HR - HR_{baseline}$ in BPM)**: Delta relative to individual resting calibration window mean.
3. **Percentage Baseline Deviation ($\% \Delta HR = \frac{HR - HR_{baseline}}{HR_{baseline}} \times 100\%$)**: Proportional shift relative to baseline.
4. **Standardized Deviation ($Z_{HR} = \frac{HR - HR_{baseline}}{\sigma_{baseline}}$)**: Standardized $z$-score normalized by baseline standard deviation.

### Discrimination Evaluation Metric
Receiver Operating Characteristic Area Under the Curve (ROC-AUC) comparing Neutral (Label 1) vs Stress (Label 2) experimental conditions.

---

## 3. Parameter Derivation Methodology (Training Subjects Only)

For parameters lacking static literature constants (e.g. decision thresholds for Medium and High risk entry), values are derived **strictly from training subjects** in each LOSO fold:

1. **Medium Risk Boundary Derivation**:
   - Optimal operating point calculated on training fold data using the **Youden $J$-Statistic**:
     $$J = \text{Sensitivity} + \text{Specificity} - 1$$
   - Maximizes true positive rate while minimizing false positive rate on training stress vs neutral distributions.

2. **High Risk Boundary Derivation**:
   - Calculated as the 90th percentile threshold of the training stress condition distribution ($\Delta HR_{stress, 90\%}$), identifying severe physiological distress.

3. **Locking Parameters for Testing**:
   - Thresholds derived from the training fold are locked and applied without modification to the held-out test subject.

---

## 4. Temporal Feature Ablation Study

To evaluate the contribution of individual temporal components, five nested decision engine variants are evaluated:

- **Model A (Baseline + HR Deviation)**: Evaluates single-sample baseline deviation ($\Delta HR$) without temporal trend, persistence, or state machine history.
- **Model B (Model A + Trend)**: Incorporates short-term rate of change ($\frac{dHR}{dt}$ over 5-sample window).
- **Model C (Model B + Persistence)**: Incorporates multi-sample abnormality persistence ($3\text{ samples}$ for Medium, $5\text{ samples}$ for High).
- **Model D (Model C + Recovery)**: Incorporates recovery behavior classification ($\frac{dHR}{dt} \le -0.3 \text{ BPM/s}$) and structured de-escalation hysteresis.
- **Model E (Model D + HRV)**: Incorporates Heart Rate Variability (RMSSD calculated over 30-second sliding windows).

---

## 5. Comparative Baselines & CARES Engine Evaluation

### Comparative Baseline Model
- **Naive Static Threshold Engine**: Fixed, population-wide threshold rule (`if HR >= 100: HIGH; elif HR >= 85: MEDIUM; else: LOW`) without individual baseline calibration, temporal trend, persistence filtering, or state machine de-escalation.

### Evaluation Metrics
1. **Accuracy**: Overall classification correctness.
2. **Precision & Recall (Sensitivity)**: Identification of true stress/distress states.
3. **Specificity**: True negative rate on neutral resting states.
4. **$F_1$ Score**: Harmonic mean of Precision and Recall.
5. **False Positive Rate (FPR)**: Rate of false alarms during neutral resting or transient artifact periods.
6. **False Negative Rate (FNR)**: Rate of missed distress events.
7. **Detection Latency ($\Delta t_{detect}$ in seconds)**: Time from stress onset to first state escalation.
8. **State Oscillation / Churn Index ($C_{osc}$)**: Total count of rapid state changes per minute.

---

## 6. Risk Score & Operational State Definitions

### Risk Score Calculation
The continuous CARES risk score ($0.0 - 100.0$) is calculated as:
$$\text{Score} = \min\left(100.0, \, 50.0 \cdot \frac{\Delta HR}{\text{Thresh}_{high}} + 30.0 \cdot \frac{\% \Delta HR}{\text{PctThresh}_{high}} + 20.0 \cdot \max\left(0, \frac{\frac{dHR}{dt}}{\text{Slope}_{rapid}}\right)\right)$$

> **DOCUMENTED DISCLAIMER:**  
> The CARES risk score is an internally defined decision-engine score and is NOT a clinical probability or validated medical risk score.

### Operational State Mapping
- **LOW**: Physiological state within normal baseline bounds ($HR \in \text{baseline} \pm \text{Thresh}_{med}$). Action: `CONTINUE_MONITORING`.
- **MEDIUM**: Moderate physiological arousal exceeding baseline bounds ($HR \ge \text{baseline} + \text{Thresh}_{med}$ for $\ge 3\text{ samples}$). Action: `USER_WARNING`, `CONTINUE_MONITORING`.
- **HIGH**: Severe physiological arousal ($HR \ge \text{baseline} + \text{Thresh}_{high}$ for $\ge 5\text{ samples}$ with confidence $\ge 0.6$). Action: `EMERGENCY_ALERT`, `GUARDIAN_NOTIFICATION`, `LOCATION_SHARE`, `GUARDIAN_COMMUNICATION`, `INCIDENT_LOG`.
