# streamlit_app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

st.set_page_config(page_title="Raw File Enricher", layout="wide")
st.title("Raw File Enricher (No Shipment Type)")

raw_file = st.file_uploader("Upload Raw File (Data Quality by Carrier)", type=["xlsx"])

# -----------------------
# Helpers
# -----------------------
def to_str(x) -> str:
    if pd.isna(x):
        return ""
    if isinstance(x, (int, np.integer)):
        return str(int(x)).strip()
    if isinstance(x, float) and x.is_integer():
        return str(int(x)).strip()
    return str(x).strip()

def to_numeric(val):
    """Force numeric (int) where possible, else NaN."""
    if pd.isna(val):
        return np.nan
    try:
        return int(float(val))
    except Exception:
        return np.nan

def to_mmddyyyy(val):
    """Convert to datetime and return normalized date (date-only)."""
    if pd.isna(val):
        return pd.NaT
    d = pd.to_datetime(val, errors="coerce")
    return d.normalize() if not pd.isna(d) else pd.NaT

def normalize_bool_to_01(val):
    """Convert True/False-ish values to 1/0, else NaN."""
    if pd.isna(val):
        return np.nan
    if isinstance(val, bool):
        return 1 if val else 0
    s = to_str(val).upper()
    if s in ["TRUE", "T", "YES", "Y", "1"]:
        return 1
    if s in ["FALSE", "F", "NO", "N", "0"]:
        return 0
    return np.nan

def derive_shipment_id(order_number, bill_of_lading):
    """Shipment ID = Order Number if not blank else Bill of Lading."""
    on = to_str(order_number)
    bol = to_str(bill_of_lading)
    return on if on != "" else bol

def derive_tracked_shipments(is_tracked_01, tracking_type):
    """
    Rules:
      - Tracking Type in {ELD, APP, DIRECT}
          IsTracked=1 -> Tracked
          IsTracked=0 -> Untracked
      - Tracking Type == Unknown
          IsTracked=1 -> YMS Milestone
          IsTracked=0 -> Untracked
    """
    tt = to_str(tracking_type).upper()
    it = None if pd.isna(is_tracked_01) else int(is_tracked_01)

    if tt in ["ELD", "APP", "DIRECT"]:
        if it == 1:
            return "Tracked"
        if it == 0:
            return "Untracked"
        return ""
    if tt == "UNKNOWN":
        if it == 1:
            return "YMS Milestone"
        if it == 0:
            return "Untracked"
        return ""

    # Fallback (if Tracking Type is something else)
    if it == 1:
        return "Tracked"
    if it == 0:
        return "Untracked"
    return ""

def first_datetime_from_window(window_val):
    """
    Input examples:
      '2025-11-05 10:00:00.000...2025-11-05 11:30:00.000'
    We take the left side before '...'
    """
    s = to_str(window_val)
    if s == "":
        return pd.NaT
    left = s.split("...")[0].strip()
    return pd.to_datetime(left, errors="coerce")

def iso_week_label(dt_val):
    """
    ISO week numbering (Mon–Sun) matching your screenshots:
      Week 40 = Sep 29 2025 to Oct 5 2025
      Week 01 (2026) = Dec 29 2025 to Jan 4 2026
    """
    if pd.isna(dt_val):
        return ""
    d = pd.to_datetime(dt_val, errors="coerce")
    if pd.isna(d):
        return ""
    wk = int(d.isocalendar().week)
    return f"Week {wk:02d}"

def to_excel_bytes(df: pd.DataFrame, sheet_name="Raw File") -> bytes:
    bio = BytesIO()
    # Ensure Excel date formatting mm/dd/yyyy
    with pd.ExcelWriter(bio, engine="openpyxl", datetime_format="mm/dd/yyyy") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return bio.getvalue()

# -----------------------
# Main
# -----------------------
if raw_file:
    df = pd.read_excel(raw_file)

    # Pickup window column name can vary; support common variants
    pickup_window_candidates = [
        "Pickup Appointement Window (UTC)",   # (as in your output list)
        "Pickup Appointment Window (UTC)",    # common spelling
        "Pickup Appointment Window (UTC) ",   # trailing space
    ]
    pickup_col = next((c for c in pickup_window_candidates if c in df.columns), None)

    # Required input columns
    required = ["Order Number", "Bill of Lading", "Tracked", "Tracking Type"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Raw file missing required columns: {missing}")
        st.stop()
    if pickup_col is None:
        st.error("Could not find Pickup Appointment Window column. Expected one of: "
                 + ", ".join(pickup_window_candidates))
        st.stop()

    # ---- Force numeric format for ID columns ----
    numeric_cols = ["Tenant ID", "P44 CARRIER ID", "P44 Shipment ID"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(to_numeric)

    # ---- Force date columns to date-only (mm/dd/yyyy on export) ----
    date_cols = [
        "Shipment Created (UTC)",
        "Tracking Window Start (UTC)",
        "Tracking Window End (UTC)"
    ]
    for col in date_cols:
        if col in df.columns:
            df[col] = df[col].apply(to_mmddyyyy)

    # ---- Create derived columns ----
    df["Shipment ID"] = [
        derive_shipment_id(on, bol) for on, bol in zip(df["Order Number"], df["Bill of Lading"])
    ]
    df["IsTracked"] = df["Tracked"].apply(normalize_bool_to_01)
    df["Tracked Shipments"] = [
        derive_tracked_shipments(it, tt) for it, tt in zip(df["IsTracked"], df["Tracking Type"])
    ]
    df["Tracking field"] = df["Tracking Type"].apply(to_str) + " - " + df["Tracked Shipments"].apply(to_str)

    # Week from the first timestamp in Pickup Appointment Window
    pickup_start_dt = df[pickup_col].apply(first_datetime_from_window)
    df["Week"] = pickup_start_dt.apply(iso_week_label)

    # Tracking Error update
    if "Tracking Error" not in df.columns:
        df["Tracking Error"] = ""
    mask_tracked_like = df["Tracked Shipments"].isin(["Tracked", "YMS Milestone"])
    df.loc[mask_tracked_like, "Tracking Error"] = "Tracked"

    # Ensure output uses the exact requested header spelling
    if "Pickup Appointement Window (UTC)" not in df.columns:
        df["Pickup Appointement Window (UTC)"] = df[pickup_col]

    # ---- Final output columns (Shipment Type REMOVED) ----
    desired_cols = [
        "Sl. No","Tenant Name","Tenant ID","Carrier Name","P44 CARRIER ID","P44 Shipment ID",
        "Bill of Lading","Order Number","Shipment ID","IsTracked","Tracked Shipments",
        "Tracking Type","Tracking field","Tracking Method","Active Equipment ID","Historical Equipment ID",
        "Pickup Name","Pickup City State","Pickup Country","Pickup Region","Week",
        "Pickup Appointement Window (UTC)",
        "Final Destination Name","Final Destination City State","Final Destination Country",
        "Delivery Appointement Window (UTC)",
        "Shipment Created (UTC)","Tracking Window Start (UTC)","Tracking Window End (UTC)",
        "Pickup Arrival Milestone (UTC)","Pickup Departure Milestone (UTC)",
        "Final Destination Arrival Milestone (UTC)","Final Destination Departure Milestone (UTC)",
        "# Of Milestones received / # Of Milestones expected",
        "# Updates Received","# Updates Received < 10 mins",
        "Nb Intervals Expected","Nb Intervals Observed",
        "Final Status Reason","Tracking Error",
        "Milestone Error 1","Milestone Error 2","Milestone Error 3",
        "Attr1 Value","Attr2 Name","Attr2 Value","Attr3 Name","Attr3 Value",
        "Attr4 Name","Attr4 Value","Attr5 Name","Attr5 Value",
        "Average Latency (min)"
    ]

    # Create missing columns as blanks (so output always matches the requested schema)
    for c in desired_cols:
        if c not in df.columns:
            df[c] = ""

    out_df = df[desired_cols].copy()

    st.subheader("Preview (first 50 rows)")
    st.dataframe(out_df.head(50), use_container_width=True)

    st.download_button(
        "Download Enriched Raw File (Reordered, No Shipment Type)",
        data=to_excel_bytes(out_df, sheet_name="Raw File"),
        file_name="Raw_File_Enriched_Reordered_No_Shipment_Type.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.subheader("Quick checks")
    st.write("Rows with Week blank:", int((out_df["Week"].apply(to_str) == "").sum()))
    st.write("Rows where Tracking Error forced to 'Tracked':", int(mask_tracked_like.sum()))
else:
    st.info("Upload the Raw File to continue.")
