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
    
    bm_dates = []
    bm_amounts = []
    cumulative_units = 0.0
    
    for idx, row in df_sorted.iterrows():
        d = row['Date']
        amt = row['Amount']
        row_type = row.get('Type', 'CASHFLOW')
        
        # Skip the client's actual portfolio valuation; we calculate the benchmark's own valuation later
        if row_type == 'VALUATION':
            continue
            
        try:
            price = benchmark_series.asof(d)
            if pd.isna(price):
                price = benchmark_series.dropna().iloc[0]
        except:
            price = benchmark_series.dropna().iloc[0]
            
        if amt < 0:
            # Investment: Buy index units
            units_bought = abs(amt) / price
            cumulative_units += units_bought
            bm_dates.append(d)
            bm_amounts.append(amt)
        elif amt > 0:
            # Redemption: Sell index units
            units_sold = amt / price
            cumulative_units -= units_sold
            bm_dates.append(d)
            bm_amounts.append(amt)

    # Calculate simulated final value of the Nifty 50 units
    latest_date = df_sorted['Date'].max()
    try:
        latest_price = benchmark_series.asof(latest_date)
        if pd.isna(latest_price):
            latest_price = benchmark_series.dropna().iloc[-1]
    except:
        latest_price = benchmark_series.dropna().iloc[-1]
        
    simulated_val = cumulative_units * latest_price
    
    bm_dates.append(latest_date)
    bm_amounts.append(simulated_val) # Positive inflow for final benchmark valuation
    
    try:
        return xirr(bm_dates, bm_amounts)
    except:
        return None

# --- FILE UPLOAD & PROCESSING ---
uploaded_file = st.file_uploader("Upload Client Statement (.xlsx or .pdf)", type=["xlsx", "pdf"])
df = None
ready_to_calculate = False

if uploaded_file is not None:
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    # === PDF PROCESSING ===
    if file_extension == 'pdf':
        pdf_password = st.text_input("🔒 This PDF is locked. Enter Password (usually PAN in uppercase):", type="password")
        
        if pdf_password:
            with st.spinner("Unlocking, mapping transactions, and extracting market value..."):
                try:
                    cashflows = []
                    auto_market_value = None
                    
                    with pdfplumber.open(uploaded_file, password=pdf_password) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text:
                                for line in text.split('\n'):
                                    # 1. Look for the Final Market Value / Portfolio Valuation automatically
                                    if auto_market_value is None:
                                        val_match = re.search(r'(?:TOTAL|MARKET|CURRENT|CLOSING|CONSOLIDATED)\s*(?:VALUE|VALUATION|BALANCE).*?((?:\d+,)*\d+\.\d{2})', line, re.IGNORECASE)
                                        if val_match:
                                            auto_market_value = float(val_match.group(1).replace(',', ''))
                                            
                                    # 2. Extract transaction dates and amounts
                                    if any(kw in line.upper() for kw in ['PURCHASE', 'SIP', 'REDEEM', 'SWITCH', 'INSTALMENT']):
                                        match = re.search(r'(\d{2}-[A-Za-z]{3}-\d{4})\s+(.*?)\s+((?:\d+,)*\d+\.\d{2}(?!\d))', line)
                                        if match:
                                            date_str = match.group(1)
                                            desc = match.group(2).upper()
                                            amt = float(match.group(3).replace(',', ''))
                                            
                                            # Redemptions are positive inflows, Purchases are negative outflows
                                            if any(kw in desc for kw in ['REDEEM', 'PAYOUT', 'WITHDRAW', 'SWITCH OUT']):
                                                amt = abs(amt) 
                                            else:
                                                amt = -abs(amt) 

                                            cashflows.append({
                                                'Scheme': 'PDF Portfolio',
                                                'Date': pd.to_datetime(date_str),
                                                'Amount': amt,
                                                'Type': 'CASHFLOW'
                                            })
                                            
                    if cashflows:
                        df = pd.DataFrame(cashflows)
                        st.success(f"Mapped {len(df)} transactions successfully!")
                        
                        # Apply the automatically found market value
                        if auto_market_value:
                            st.info(f"✅ Auto-detected Current Portfolio Value: ₹{auto_market_value:,.2f}")
                            val_row = pd.DataFrame([{
                                'Scheme': 'PDF Portfolio',
                                'Date': pd.to_datetime(datetime.today().strftime('%Y-%m-%d')),
                                'Amount': auto_market_value,
                                'Type': 'VALUATION'
                            }])
                            df = pd.concat([df, val_row], ignore_index=True)
                            ready_to_calculate = True
                        else:
                            # Fallback just in case CAMS completely changed their phrasing
                            st.warning("Could not auto-detect the final market value.")
                            manual_value = st.number_input("💰 Enter Current Portfolio Value manually:", min_value=0.0, step=1000.0)
                            if manual_value > 0:
                                val_row = pd.DataFrame([{
                                    'Scheme': 'PDF Portfolio',
                                    'Date': pd.to_datetime(datetime.today().strftime('%Y-%m-%d')),
                                    'Amount': manual_value,
                                    'Type': 'VALUATION'
                                }])
                                df = pd.concat([df, val_row], ignore_index=True)
                                ready_to_calculate = True
                    else:
                        st.error("Could not find any standard CAMS/KFintech transactions.")
                except Exception as e:
                    st.error("Incorrect password or unable to read PDF.")

    # === EXCEL PROCESSING ===
    elif file_extension == 'xlsx':
        try:
            df = pd.read_excel(uploaded_file)
            df = df.dropna(how='all', axis=1).dropna(how='all', axis=0)
            df.columns = [str(c).strip() for c in df.columns]
            df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
            df['Date'] = pd.to_datetime(df['Date'])
            df['Amount'] = pd.to_numeric(df['Amount'])
            
            if 'Type' not in df.columns:
                df['Type'] = 'CASHFLOW'
                # Automatically mark the last positive cashflow as the VALUATION
                last_positive_idx = df[df['Amount'] > 0].index.max()
                if pd.notna(last_positive_idx):
                    df.loc[last_positive_idx, 'Type'] = 'VALUATION'
            
            ready_to_calculate = True
        except Exception as e:
            st.error(f"Error processing Excel: {e}")

    # === FINAL CALCULATIONS & DASHBOARD ===
    if ready_to_calculate and df is not None:
        port_dates = df['Date'].tolist()
        port_amounts = df['Amount'].tolist()
        
        try:
            port_xirr = xirr(port_dates, port_amounts)
            
            start_date = df['Date'].min().strftime('%Y-%m-%d')
            end_date = datetime.today().strftime('%Y-%m-%d')
            
            with st.spinner('Simulating Index Units & Fetching Nifty 50 NSE Benchmark Data...'):
                nifty50_series = fetch_benchmark_data('^NSEI', start_date, end_date)
                nifty50_xirr = calculate_true_benchmark_xirr(nifty50_series, df)

            # Dashboard UI
            st.markdown("---")
            st.subheader("📊 Performance Summary (Annualized CAGR/XIRR)")
            col1, col2, col3 = st.columns(3)
            col1.metric("Client Portfolio Return", f"{port_xirr * 100:.2f}%" if port_xirr else "N/A")
            col2.metric("Nifty 50 Benchmark", f"{nifty50_xirr * 100:.2f}%" if nifty50_xirr else "N/A")
            
            if port_xirr and nifty50_xirr:
                alpha = (port_xirr - nifty50_xirr) * 100
                col3.metric("Alpha Generated", f"{alpha:.2f}%", delta=f"{alpha:.2f}%")

            # Growth Chart
            st.subheader("📈 Index Growth Over Investment Period")
            if nifty50_series is not None and not nifty50_series.empty:
                fig, ax = plt.subplots(figsize=(10, 4))
                normalized_index = (nifty50_series / nifty50_series.iloc[0]) * 100
                ax.plot(normalized_index.index, normalized_index, color='#1f77b4', linewidth=2, label="Nifty 50 (Base 100)")
                
                cashflow_df = df[df['Type'] == 'CASHFLOW']
                buy_dates = cashflow_df[cashflow_df['Amount'] < 0]['Date']
                for bd in buy_dates:
                    if bd in normalized_index.index:
                        ax.scatter(bd, normalized_index.loc[bd], color='red', zorder=5)
                
                ax.set_title("Nifty 50 Performance (Red dots = Client SIP/Purchase Dates)", fontweight='bold')
                ax.set_ylabel("Growth (Base 100)")
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.legend()
                st.pyplot(fig)
                
        except Exception as e:
            st.error(f"Could not calculate returns. Ensure data has investments and a final valuation. ({e})")
