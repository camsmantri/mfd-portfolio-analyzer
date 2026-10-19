import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from pyxirr import xirr
import matplotlib.pyplot as plt
from datetime import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="MFD Portfolio Analyzer", layout="wide")
st.title("📊 MFD Portfolio vs Benchmark Analyzer")
st.write("Upload your client's mutual fund transaction history to instantly compare their exact XIRR against the Nifty 50 index.")

# --- HELPER FUNCTIONS ---
@st.cache_data(ttl=3600) # Cache the download so it doesn't slow down your app
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
uploaded_file = st.file_uploader("Upload Client Excel File (.xlsx)", type=["xlsx"])

if uploaded_file is not None:
    try:
        df = pd.read_excel(uploaded_file)
        
        # Smart Auto-Cleaner
        df = df.dropna(how='all', axis=1)
        df = df.dropna(how='all', axis=0)
        
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
            st.error(f"Missing required columns: {', '.join(missing)}. Please check your Excel file.")
            st.stop()
            
        df['Date'] = pd.to_datetime(df['Date'])
        df['Amount'] = pd.to_numeric(df['Amount'])
        
        with st.expander("🔍 View Cleaned Transaction Data"):
            st.dataframe(df)

        # --- CALCULATIONS ---
        port_dates = df['Date'].tolist()
        port_amounts = df['Amount'].tolist()
        port_xirr = xirr(port_dates, port_amounts)

        start_date = df['Date'].min().strftime('%Y-%m-%d')
        end_date = datetime.today().strftime('%Y-%m-%d')
        
        with st.spinner('Fetching Nifty 50 Market Data...'):
            nifty50_series = fetch_benchmark_data('^NSEI', start_date, end_date)
            nifty50_xirr = calculate_true_benchmark_xirr(nifty50_series, df)

        # --- DASHBOARD UI ---
        st.subheader("📊 Performance Summary")
        col1, col2, col3 = st.columns(3)
        
        col1.metric("Portfolio XIRR", f"{port_xirr * 100:.2f}%" if port_xirr else "N/A")
        col2.metric("Nifty 50 XIRR (Benchmark)", f"{nifty50_xirr * 100:.2f}%" if nifty50_xirr else "N/A")
        
        if port_xirr and nifty50_xirr:
            alpha = (port_xirr - nifty50_xirr) * 100
            col3.metric("Alpha Generated", f"{alpha:.2f}%", delta=f"{alpha:.2f}%")

        # --- GROWTH CHART ---
        st.subheader("📈 Index Growth Over Investment Period")
        if nifty50_series is not None and not nifty50_series.empty:
            fig, ax = plt.subplots(figsize=(10, 4))
            
            # Normalize index to start at 100 for easy visualization
            normalized_index = (nifty50_series / nifty50_series.iloc[0]) * 100
            ax.plot(normalized_index.index, normalized_index, color='#1f77b4', linewidth=2, label="Nifty 50 (Base 100)")
            
            # Highlight investment dates
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
        st.error(f"An error occurred while processing the file: {e}")