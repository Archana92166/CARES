# CARES Dataset Provenance & Acquisition Guide

## Benchmark Dataset: WESAD (Wearable Stress and Affect Detection)

### Citation & Publication Details
- **Title**: WESAD: Wearable Stress and Affect Detection Data Set
- **Authors**: Philip Schmidt, Attila Reiss, Robert Duerichen, Claus Marberger, and Kristof Van Laerhoven
- **Publication**: Proceedings of the 20th ACM International Conference on Multimodal Interaction (ICMI '18), 2018.
- **DOI**: [10.1145/3242969.3242985](https://doi.org/10.1145/3242969.3242985)
- **UCI Repository DOI**: [10.24432/C57K5T](https://doi.org/10.24432/C57K5T)
- **URL**: https://archive.ics.uci.edu/ml/datasets/WESAD+(Wearable+Stress+and+Affect+Detection)

---

## Dataset Description & Experimental Protocol

WESAD is a publicly available benchmark dataset for wearable physiological threat and stress monitoring containing multimodal sensor recordings from **15 participants** (subjects `S2` through `S17`, excluding `S1` due to sensor calibration failure).

### Experimental Conditions (Ground Truth Labels)
1. **Label 0**: Transient / Unassigned transition state
2. **Label 1 (Neutral)**: Resting baseline state (sitting, reading neutral magazines for ~20 minutes)
3. **Label 2 (Stress)**: Trier Social Stress Test (TSST) protocol consisting of public speaking preparation, public presentation before an evaluation panel, and mental arithmetic stress under time pressure (~10 minutes)
4. **Label 3 (Amusement)**: Viewing humorous video clips (~10 minutes)
5. **Label 4 (Meditation / Recovery)**: De-escalation guided breathing and relaxation session (~7 minutes)

### Sensing Hardware & Signals
- **Chest Device (RespiBAN @ 700 Hz)**:
  - Electrocardiogram (ECG) $\rightarrow$ Derived Heart Rate (HR) and Heart Rate Variability (HRV)
  - Electrodermal Activity (EDA / Galvanic Skin Response)
  - Respiration (RESP)
  - Skin Temperature (TEMP)
  - 3-axis Accelerometer (ACC)
- **Wrist Device (Empatica E4 @ 64 Hz / 4 Hz)**:
  - Blood Volume Pulse (BVP @ 64 Hz) $\rightarrow$ Photoplethysmography (PPG) HR & HRV
  - Electrodermal Activity (EDA @ 4 Hz)
  - Skin Temperature (TEMP @ 4 Hz)
  - 3-axis Accelerometer (ACC @ 32 Hz)

---

## Academic Context & Scoping Disclaimer

> **IMPORTANT SCOPING DISCLAIMER:**  
> WESAD represents an experimental laboratory benchmark for acute psychological stress (TSST protocol).  
> **WESAD does NOT constitute clinical ground truth for cognitive risk or spatial panic in visually impaired individuals.**  
> In the CARES evaluation framework, WESAD labels are utilized specifically as a proxy for physiological arousal, acute cognitive stress, and recovery dynamics under controlled experimental conditions.

---

## Automated Download Diagnostics & Acquisition Protocol

### Diagnostic Status of Automated Download
During Milestone 2 execution, automated direct HTTP download scripts targeting legacy UCI static directory endpoints returned `HTTP 404 Not Found` (due to UCI repository architecture updates and sciebo direct link expiry). To prevent data fabrication, the following documented acquisition procedure is established.

### Manual / Scripted Acquisition Procedure
To populate the raw WESAD files for local evaluation:

1. Download the official WESAD archive (`WESAD.zip`, ~2.8 GB) from the official UCI Repository or Siegen sciebo instance.
2. Unpack the zip file into the project directory:
   ```
   CARES/data/wesad/
   ├── S2/
   │   └── S2.pkl
   ├── S3/
   │   └── S3.pkl
   ├── ...
   └── S17/
       └── S17.pkl
   ```
3. Each subject pickle (`SX.pkl`) contains a Python dictionary with keys:
   - `'signal'`: Nested dict containing `'chest'` and `'wrist'` sensor channels.
   - `'label'`: 1D array of ground truth condition labels at 700 Hz.
   - `'subject'`: Subject ID string.

The CARES preprocessing pipeline ([`evaluation/wesad_parser.py`](file:///home/archana/Documents/CARES/evaluation/wesad_parser.py)) is configured to auto-detect `.pkl` files in `data/wesad/` or operate on WESAD-aligned physiological benchmark streams.
