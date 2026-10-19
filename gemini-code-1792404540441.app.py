import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from pyxirr import xirr
import matplotlib.pyplot as plt
from datetime import datetime
import pdfplumber
import re

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="MFD Portfolio Analyzer", layout="wide")
st.title("📊 MFD Portfolio vs Benchmark Analyzer")
st.write("Upload your client's mutual fund statement to compare their exact XIRR against the Nifty 50 index.")

# --- HELPER FUNCTIONS ---
@st.cache_data(ttl=3600) 
def fetch_benchmark_data(ticker, start_date, end_date):
    try:
        df_idx = yf.download(ticker, start=start_date, end=end_date, progress=False)
        if isinstance(df_idx.columns, pd.MultiIndex):
            return df_idx['Close'].iloc[:, 0]
        return df_idx['Close']
    except Exception as e:
        st.error(f"Failed to fetch benchmark data: {e}")
        return None

def calculate_true_benchmark_xirr(benchmark_series, df_trans):
    if benchmark_series is None or benchmark_series.empty:
        return None
    df_sorted = df_trans.sort_values('Date').copy()
    latest_date = df_sorted['Date'].max()
    try:
        latest_index_price = benchmark_series.asof(latest_date)
        if pd.isna(latest_index_price):
            latest_index_price = benchmark_series.dropna().iloc[-1]
    except:
        latest_index_price = benchmark_series.dropna().iloc[-1]

    bm_dates = []
    bm_amounts = []

    for d, group in df_sorted.groupby('Date'):
        net_flow_amount = 0
        for idx, row in group.iterrows():
            amt = row['Amount']
            if amt < 0:
                try:
                    buy_price = benchmark_series.asof(d)
                    if pd.isna(buy_price):
                        buy_price = benchmark_series.dropna().iloc[0]
                except:
                    buy_price = benchmark_series.dropna().iloc[0]
                net_flow_amount -= abs(amt) 
            else:
                net_flow_amount += amt 
        bm_dates.append(d)
        bm_amounts.append(net_flow_amount)
    try:
        return xirr(bm_dates, bm_amounts)
    except:
        return None

# --- FILE UPLOAD & CLEANING ---
# UPDATED: Now accepts both Excel and PDF
uploaded_file = st.file_uploader("Upload Client Statement (.xlsx or .pdf)", type=["xlsx", "pdf"])

if uploaded_file is not None:
    file_extension = uploaded_file.name.split('.')[-1].lower()
    df = None
    
    # --- PDF HANDLING ---
    if file_extension == 'pdf':
        pdf_password = st.text_input("🔒 This PDF is locked. Enter Password (usually PAN in uppercase):", type="password")
        
        if pdf_password:
            with st.spinner("Unlocking and reading PDF..."):
                try:
                    extracted_data = []
                    # pdfplumber reads directly from the uploaded file stream
                    with pdfplumber.open(uploaded_file, password=pdf_password) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text:
                                for line in text.split('\n'):
                                    if any(keyword in line.upper() for keyword in ['PURCHASE', 'SIP', 'REDEEM', 'SWITCH']):
                                        extracted_data.append({'Raw_Transaction_Line': line})
                                        
                    if extracted_data:
                        st.success(f"Successfully unlocked! Extracted {len(extracted_data)} transaction lines.")
                        st.dataframe(pd.DataFrame(extracted_data))
                        st.info("💡 Note: To run the full XIRR calculation automatically, an advanced Regex algorithm needs to map these raw text lines into 'Scheme', 'Date', and 'Amount' columns. For now, please use the Excel template for calculations.")
                    else:
                        st.warning("Unlocked successfully, but could not detect standard transaction lines.")
                except Exception as e:
                    st.error("Incorrect password or unable to read PDF. Please try again.")

    # --- EXCEL HANDLING (Full Calculation Engine) ---
    elif file_extension == 'xlsx':
        try:
            df = pd.read_excel(uploaded_file)
            df = df.dropna(how='all', axis=1).dropna(how='all', axis=0)
            if 'Scheme' not in df.columns:
                for r in range(len(df)):
                    if 'Scheme' in df.iloc[r].values:
                        df.columns = df.iloc[r]
                        df = df.iloc[r+1:].reset_index(drop=True)
                        break
                        
            df.columns = [str(c).strip() for c in df.columns]
            df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
            
            required_cols = ['Scheme', 'Date', 'Amount']
            missing = [col for col in required_cols if col not in df.columns]
            if missing:
                st.error(f"Missing required columns: {', '.join(missing)}")
                st.stop()
                
            df['Date'] = pd.to_datetime(df['Date'])
            df['Amount'] = pd.to_numeric(df['Amount'])
            
            with st.expander("🔍 View Cleaned Transaction Data"):
                st.dataframe(df)

            port_dates, port_amounts = df['Date'].tolist(), df['Amount'].tolist()
            port_xirr = xirr(port_dates, port_amounts)

            start_date = df['Date'].min().strftime('%Y-%m-%d')
            end_date = datetime.today().strftime('%Y-%m-%d')
            
            with st.spinner('Fetching Nifty 50 Market Data...'):
                nifty50_series = fetch_benchmark_data('^NSEI', start_date, end_date)
                nifty50_xirr = calculate_true_benchmark_xirr(nifty50_series, df)

            st.subheader("📊 Performance Summary")
            col1, col2, col3 = st.columns(3)
            col1.metric("Portfolio XIRR", f"{port_xirr * 100:.2f}%" if port_xirr else "N/A")
            col2.metric("Nifty 50 XIRR (Benchmark)", f"{nifty50_xirr * 100:.2f}%" if nifty50_xirr else "N/A")
            if port_xirr and nifty50_xirr:
                alpha = (port_xirr - nifty50_xirr) * 100
                col3.metric("Alpha Generated", f"{alpha:.2f}%", delta=f"{alpha:.2f}%")

            st.subheader("📈 Index Growth Over Investment Period")
            if nifty50_series is not None and not nifty50_series.empty:
                fig, ax = plt.subplots(figsize=(10, 4))
                normalized_index = (nifty50_series / nifty50_series.iloc[0]) * 100
                ax.plot(normalized_index.index, normalized_index, color='#1f77b4', linewidth=2, label="Nifty 50 (Base 100)")
                
                buy_dates = df[df['Amount'] < 0]['Date']
                for bd in buy_dates:
                    if bd in normalized_index.index:
                        ax.scatter(bd, normalized_index.loc[bd], color='red', zorder=5)
                
                ax.set_title("Nifty 50 Performance (Red dots = Client SIP/Purchase Dates)", fontweight='bold')
                ax.set_ylabel("Growth (Base 100)")
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.legend()
                st.pyplot(fig)
                
        except Exception as e:
            st.error(f"An error occurred while processing the Excel file: {e}")
