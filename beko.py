# streamlit_app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

st.set_page_config(page_title="Raw File Enricher", layout="wide")
st.title("Raw File Enricher")

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

def derive_tracked_shipments(is_tracked_01, connection_type):
    """
    Rules:
      - Connection Type in {ELD, APP, DIRECT}
          IsTracked=1 -> Tracked
          IsTracked=0 -> Untracked
      - Connection Type == Unknown
          IsTracked=1 -> YMS Milestone
          IsTracked=0 -> Untracked
    """
    tt = to_str(connection_type).upper()
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

    # Fallback
    if it == 1:
        return "Tracked"
    if it == 0:
        return "Untracked"
    return ""

def derive_tracked_flag(tracking_field: str) -> int:
    """
    Column K - Tracked:
      If Tracking field (col N) is one of:
        'APP - Tracked', 'DIRECT - Tracked', 'ELD - Tracked'
      -> 1, else -> 0
    """
    tf = to_str(tracking_field).strip()
    tracked_values = {"APP - Tracked", "DIRECT - Tracked", "ELD - Tracked"}
    return 1 if tf in tracked_values else 0

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
    ISO week numbering (Mon-Sun):
      Returns 'Week XX' zero-padded.
    """
    if pd.isna(dt_val):
        return ""
    d = pd.to_datetime(dt_val, errors="coerce")
    if pd.isna(d):
        return ""
    wk = int(d.isocalendar().week)
    return f"Week {wk:02d}"

def iso_year(dt_val):
    """
    ISO year — matches ISO week (can differ from calendar year at year boundaries).
    e.g. Dec 29 2025 belongs to ISO Week 01 of 2026.
    """
    if pd.isna(dt_val):
        return np.nan
    d = pd.to_datetime(dt_val, errors="coerce")
    if pd.isna(d):
        return np.nan
    return int(d.isocalendar().year)

def to_excel_bytes(df: pd.DataFrame, sheet_name="Raw File") -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl", datetime_format="mm/dd/yyyy") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return bio.getvalue()

# -----------------------
# Column name normalizer
# Handles minor spelling/casing differences in input files
# -----------------------
COLUMN_ALIASES = {
    "Connection Type": [
        "connection type", "connectiontype", "connection_type",
        "tracking type", "trackingtype", "tracking_type",
        "type of tracking", "tracking  type"
    ],
    "Tracked": [
        "tracked", "is tracked", "istracked", "is_tracked"
    ],
    "Order Number": [
        "order number", "ordernumber", "order_number", "order no", "order no."
    ],
    "Bill of Lading": [
        "bill of lading", "billoflading", "bill_of_lading", "bol"
    ],
    "Pickup Appointement Window (UTC)": [
        "pickup appointement window (utc)",
        "pickup appointment window (utc)",
        "pickup appointment window (utc) ",
        "pickup appointement window(utc)",
    ],
}

def normalize_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    col_map = {c.strip().lower(): c for c in df.columns}
    rename_map = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical not in df.columns:
            for alias in aliases:
                if alias.lower() in col_map:
                    rename_map[col_map[alias.lower()]] = canonical
                    break
    if rename_map:
        df = df.rename(columns=rename_map)
    return df, rename_map

# -----------------------
# Main
# -----------------------
if raw_file:
    df = pd.read_excel(raw_file)

    # Normalize column names (handle spelling/casing variants)
    df, renamed = normalize_columns(df)
    if renamed:
        st.info(f"Auto-renamed columns to match expected schema: {renamed}")

    # Show actual columns for debugging (collapsible)
    with st.expander("Columns detected in uploaded file"):
        st.write(df.columns.tolist())

    # Pickup window column (after normalization, should be canonical name)
    pickup_col = "Pickup Appointement Window (UTC)"

    # Required input columns
    required = ["Order Number", "Bill of Lading", "Tracked", "Connection Type", pickup_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Raw file missing required columns: {missing}")
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
        derive_shipment_id(on, bol)
        for on, bol in zip(df["Order Number"], df["Bill of Lading"])
    ]

    df["IsTracked"] = df["Tracked"].apply(normalize_bool_to_01)

    df["Tracked Shipments"] = [
        derive_tracked_shipments(it, tt)
        for it, tt in zip(df["IsTracked"], df["Connection Type"])
    ]

    df["Tracking field"] = (
        df["Connection Type"].apply(to_str) + " - " + df["Tracked Shipments"].apply(to_str)
    )

    # ---- Column K: Tracked (1/0) based on Tracking field ----
    # Logic: if Tracking field is 'APP - Tracked', 'DIRECT - Tracked', or 'ELD - Tracked' -> 1, else -> 0
    df["Tracked"] = df["Tracking field"].apply(derive_tracked_flag)

    # ---- Week and Year from Pickup Appointment Window ----
    pickup_start_dt = df[pickup_col].apply(first_datetime_from_window)
    df["Week"] = pickup_start_dt.apply(iso_week_label)
    df["Year"] = pickup_start_dt.apply(iso_year)

    # ---- Tracking Error update ----
    if "Tracking Error" not in df.columns:
        df["Tracking Error"] = ""
    mask_tracked_like = df["Tracked Shipments"].isin(["Tracked", "YMS Milestone"])
    df.loc[mask_tracked_like, "Tracking Error"] = "Tracked"

    # Map Tenant Name -> Customer Tenant Name for output col B
    df["Customer Tenant Name"] = df["Tenant Name"] if "Tenant Name" in df.columns else ""

    # ---- Final output columns ----
    desired_cols = [
        "Customer Tenant Name", "Carrier Name", "P44 CARRIER ID", "P44 Shipment ID",
        "Bill of Lading", "Order Number", "Shipment ID", "IsTracked", "Tracked", "Tracked Shipments",
        "Connection Type", "Tracking field", "Tracking Method", "Active Equipment ID", "Historical Equipment ID",
        "Pickup Name", "Pickup City State", "Pickup Country", "Pickup Region",
        "Year", "Week",
        "Pickup Appointement Window (UTC)",
        "Final Destination Name", "Final Destination City State", "Final Destination Country",
        "Delivery Appointement Window (UTC)",
        "Shipment Created (UTC)", "Tracking Window Start (UTC)", "Tracking Window End (UTC)",
        "Pickup Arrival Milestone (UTC)", "Pickup Departure Milestone (UTC)",
        "Final Destination Arrival Milestone (UTC)", "Final Destination Departure Milestone (UTC)",
        "# Of Milestones received / # Of Milestones expected",
        "# Updates Received", "# Updates Received < 10 mins",
        "Nb Intervals Expected", "Nb Intervals Observed",
        "Final Status Reason", "Tracking Error",
        "Milestone Error 1", "Milestone Error 2", "Milestone Error 3",
        "Shipment Type",
        "Attr2 Name", "Attr2 Value", "Attr3 Name", "Attr3 Value",
        "Attr4 Name", "Attr4 Value", "Attr5 Name", "Attr5 Value",
        "Average Latency (min)"
    ]

    # Create missing columns as blanks
    for c in desired_cols:
        if c not in df.columns:
            df[c] = ""

    out_df = df[desired_cols].copy()

    st.subheader("Preview (first 50 rows)")
    st.dataframe(out_df.head(50), use_container_width=True)

    st.download_button(
        "⬇️ Download Enriched Raw File",
        data=to_excel_bytes(out_df, sheet_name="Raw File"),
        file_name="Raw_File_Enriched.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.subheader("Quick checks")
    st.write("Total rows:", len(out_df))
    st.write("Rows with Week blank:", int((out_df["Week"].apply(to_str) == "").sum()))
    st.write("Rows where Tracked = 1:", int((out_df["Tracked"] == 1).sum()))
    st.write("Rows where Tracked = 0:", int((out_df["Tracked"] == 0).sum()))
    st.write("Rows where Tracking Error forced to 'Tracked':", int(mask_tracked_like.sum()))

else:
    st.info("Upload the Raw File to continue.")
