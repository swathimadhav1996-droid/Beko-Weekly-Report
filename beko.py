# streamlit_app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

st.set_page_config(page_title="Carrier Data Quality Builder", layout="wide")

st.title("Carrier Data Quality Report Builder")
st.write("Upload the base **Data Quality by Carrier** file and the **RCA mapping** file to generate the final report.")

base_file = st.file_uploader("Upload: Data Quality by Carrier (base file)", type=["xlsx"])
rca_file = st.file_uploader("Upload: RCA mapping file (truckload_shipments_*.xlsx)", type=["xlsx"])

# --------------------------
# Helpers
# --------------------------
def to_str(x) -> str:
    """Normalize IDs to comparable strings (handles numbers stored as text and vice versa)."""
    if pd.isna(x):
        return ""
    if isinstance(x, (int, np.integer)):
        return str(int(x)).strip()
    if isinstance(x, float) and x.is_integer():
        return str(int(x)).strip()
    return str(x).strip()

def derive_shipment_id(order_number, bill_of_lading):
    """Shipment ID = Order Number if not blank else Bill of Lading."""
    on = to_str(order_number)
    bol = to_str(bill_of_lading)
    return on if on != "" else bol

def normalize_tracking_type(tracking_type):
    return to_str(tracking_type).upper()

def normalize_bool(val):
    """Return True/False/None from various excel representations."""
    if pd.isna(val):
        return None
    if isinstance(val, bool):
        return val
    s = to_str(val).upper()
    if s in ["TRUE", "T", "YES", "Y", "1"]:
        return True
    if s in ["FALSE", "F", "NO", "N", "0"]:
        return False
    return None

def derive_tracked_shipments(tracked_val, tracking_type):
    """
    Rules:
      - If Tracking Type in {ELD, APP, DIRECT}: True->Tracked, False->Untracked
      - If Tracking Type == UNKNOWN: True->YMS Milestone, False->Untracked
      - Otherwise: fallback True->Tracked, False->Untracked
    """
    tt = normalize_tracking_type(tracking_type)
    trb = normalize_bool(tracked_val)

    if tt in ["ELD", "APP", "DIRECT"]:
        return "Tracked" if trb is True else ("Untracked" if trb is False else "")
    if tt == "UNKNOWN":
        return "YMS Milestone" if trb is True else ("Untracked" if trb is False else "")
    if trb is True:
        return "Tracked"
    if trb is False:
        return "Untracked"
    return ""

def excel_weeknum_sunday(date_val):
    """
    Excel WEEKNUM(date,1) equivalent:
    - Weeks start on Sunday
    - Week 1 is the week containing Jan 1
    Python: %U is week number (Sunday start), week 0 before first Sunday -> add 1.
    """
    if pd.isna(date_val):
        return ""
    d = pd.to_datetime(date_val, errors="coerce")
    if pd.isna(d):
        return ""
    return f"Week {int(d.strftime('%U')) + 1}"

def build_rca_lookup(map_df: pd.DataFrame) -> dict:
    """
    Mapping key = Bill Of Lading if present else Order Number (as strings).
    Value = Root Cause Error (string).
    """
    required = ["Bill Of Lading", "Order Number", "Root Cause Error"]
    missing = [c for c in required if c not in map_df.columns]
    if missing:
        raise ValueError(f"Mapping file missing columns: {missing}")

    bol = map_df["Bill Of Lading"].apply(to_str)
    on = map_df["Order Number"].apply(to_str)
    key = np.where(bol != "", bol, on)

    values = map_df["Root Cause Error"].apply(to_str).values
    return dict(zip(key, values))

def reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Reorder to your final layout; create missing columns as blank."""
    desired_cols = [
        "Sl. No","Tenant Name","Carrier Name","Carrier Identifier Selection","SCAC",
        "Bill of Lading","Order Number","Shipment ID","RCA Reason","Tracked Shipments",
        "Tracking Type","Tracking field","Tracking Method","IsTracked",
        "Active Equipment ID","Historical Equipment ID",
        "Pickup Name","Pickup City State","Pickup Country","Pickup Region",
        "Dropoff Name","Dropoff City State","Dropoff Country","Dropoff Country Region",
        "Final Status Reason","Week","Created Timestamp Date",
        "Pickup Arrival Utc Timestamp Raw","Pickup Departure Utc Timestamp Raw",
        "Dropoff Arrival Utc Timestamp Raw","Dropoff Departure Utc Timestamp Raw",
        "Nb Milestones Expected","Nb Milestones Received","Milestones Achieved Percentage",
        "Latency Updates Received","Latency Updates Passed","Shipment Latency Percentage",
        "Average Latency (min)","Period Date","Ping Interval (min)","Shipment Type",
        "Attr1 Value","Attr2 Name","Attr2 Value","Attr3 Name","Attr3 Value",
        "Attr4 Name","Attr4 Value","Attr5 Name","Attr5 Value"
    ]

    for c in desired_cols:
        if c not in df.columns:
            df[c] = ""

    return df[desired_cols].copy()

def to_excel_bytes(df: pd.DataFrame, sheet_name="Report") -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()

# --------------------------
# Main flow
# --------------------------
if base_file and rca_file:
    try:
        base_df = pd.read_excel(base_file)
        map_df = pd.read_excel(rca_file)

        # Normalize Sl. No column (sometimes comes as Unnamed: 0)
        if "Unnamed: 0" in base_df.columns and "Sl. No" not in base_df.columns:
            base_df.rename(columns={"Unnamed: 0": "Sl. No"}, inplace=True)

        required_base = ["Order Number", "Bill of Lading", "Tracked", "Tracking Type", "Created Timestamp Date"]
        missing_base = [c for c in required_base if c not in base_df.columns]
        if missing_base:
            st.error(f"Base file missing required columns: {missing_base}")
            st.stop()

        # Shipment ID
        base_df["Shipment ID"] = [
            derive_shipment_id(on, bol) for on, bol in zip(base_df["Order Number"], base_df["Bill of Lading"])
        ]

        # Tracked Shipments
        base_df["Tracked Shipments"] = [
            derive_tracked_shipments(tr, tt) for tr, tt in zip(base_df["Tracked"], base_df["Tracking Type"])
        ]

        # Tracking field
        base_df["Tracking field"] = base_df["Tracking Type"].apply(to_str) + " - " + base_df["Tracked Shipments"].apply(to_str)

        # IsTracked
        base_df["IsTracked"] = np.where(base_df["Tracked Shipments"] == "Tracked", 1, 0)

        # Week derived from Created Timestamp Date
        base_df["Week"] = base_df["Created Timestamp Date"].apply(excel_weeknum_sunday)

        # Ensure RCA Reason exists
        if "RCA Reason" not in base_df.columns:
            base_df["RCA Reason"] = ""

        # Build RCA lookup and fill ONLY blanks in RCA Reason
        lookup = build_rca_lookup(map_df)

        def fill_rca_reason(row):
            current = to_str(row.get("RCA Reason"))
            if current != "":
                return row["RCA Reason"]
            sid = to_str(row.get("Shipment ID"))
            return lookup.get(sid, "")

        base_df["RCA Reason"] = base_df.apply(fill_rca_reason, axis=1)

        # Override rule: Tracked or YMS Milestone => RCA Reason = "Tracked"
        base_df.loc[base_df["Tracked Shipments"].isin(["Tracked", "YMS Milestone"]), "RCA Reason"] = "Tracked"

        # Reorder columns to final layout
        final_df = reorder_columns(base_df)

        # Preview
        st.subheader("Preview (first 50 rows)")
        st.dataframe(final_df.head(50), use_container_width=True)

        # Download
        excel_bytes = to_excel_bytes(final_df, sheet_name="Data Quality")
        st.download_button(
            label="Download Final Excel",
            data=excel_bytes,
            file_name="Data_Quality_Final.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # Quick checks
        st.subheader("Quick checks")
        st.write("Blank RCA Reason count:", int((final_df["RCA Reason"].apply(to_str) == "").sum()))
        st.write("Tracked/YMS count:", int(final_df["Tracked Shipments"].isin(["Tracked", "YMS Milestone"]).sum()))

    except Exception as e:
        st.error(f"Error processing files: {e}")
else:
    st.info("Upload both files to generate the final report.")
