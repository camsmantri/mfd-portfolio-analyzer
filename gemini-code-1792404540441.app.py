import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from pyxirr import xirr
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from datetime import datetime
import pdfplumber
import re

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="MFD Portfolio Analyzer", layout="wide")
st.title("📊 MFD Portfolio vs Benchmark Analyzer")
st.write("Upload your client's mutual fund statement to compare their exact XIRR against the Nifty 50 index.")

# --- HELPER FUNCTIONS ---
@st.cache_data(ttl=3600) 
def fetch_benchmark_data(ticker_symbol, start_date, end_date):
    try:
        df_idx = yf.download(ticker_symbol, start=start_date, end=end_date, progress=False)
        if isinstance(df_idx.columns, pd.MultiIndex):
            return df_idx['Close'].iloc[:, 0]
        return df_idx['Close']
    except Exception as e:
        st.error(f"Failed to fetch benchmark data: {e}")
        return None

def calculate_true_benchmark(benchmark_series, df_trans, valuation_date):
    if benchmark_series is None or benchmark_series.empty:
        return None, 0.0
        
    df_cashflows = df_trans[df_trans['Type'] == 'CASHFLOW'].sort_values('Date').copy()
    
    cumulative_units = 0.0
    bm_dates = []
    bm_amounts = []
    
    for idx, row in df_cashflows.iterrows():
        d = row['Date']
        amt = row['Amount']
        
        try:
            price = benchmark_series.asof(d)
            if pd.isna(price): price = benchmark_series.dropna().iloc[0]
        except:
            price = benchmark_series.dropna().iloc[0]
            
        if amt < 0:
            cumulative_units += abs(amt) / price
        else:
            cumulative_units -= amt / price
            
        bm_dates.append(d)
        bm_amounts.append(amt)
        
    try:
        latest_price = benchmark_series.asof(valuation_date)
        if pd.isna(latest_price): latest_price = benchmark_series.dropna().iloc[-1]
    except:
        latest_price = benchmark_series.dropna().iloc[-1]
        
    simulated_val = cumulative_units * latest_price
    
    bm_dates.append(valuation_date)
    bm_amounts.append(simulated_val) 
    
    try:
        return xirr(bm_dates, bm_amounts), simulated_val
    except:
        return None, simulated_val

# --- FILE UPLOAD & PROCESSING ---
uploaded_file = st.file_uploader("Upload Client Statement (.xlsx or .pdf)", type=["xlsx", "pdf"])
df = None
ready_to_calculate = False
val_date_str = datetime.today().strftime('%Y-%m-%d')

if uploaded_file is not None:
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    # === PDF PROCESSING ===
    if file_extension == 'pdf':
        pdf_password = st.text_input("🔒 This PDF is locked. Enter Password (usually PAN in uppercase):", type="password")
        
        if pdf_password:
            with st.spinner("Unlocking, mapping transactions, and consolidating market values..."):
                try:
                    cashflows = []
                    total_market_value = 0.0
                    
                    with pdfplumber.open(uploaded_file, password=pdf_password) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text:
                                # 1. Find and sum ALL individual folio market values across the document
                                val_matches = re.findall(r'Market Value on (\d{2}-[A-Za-z]{3}-\d{4}).*?((?:\d+,)*\d+\.\d{2})', text, re.IGNORECASE)
                                for match in val_matches:
                                    val_date_str = match[0]
                                    total_market_value += float(match[1].replace(',', ''))
                                
                                # 2. Extract transactions
                                for line in text.split('\n'):
                                    if any(kw in line.upper() for kw in ['PURCHASE', 'SIP', 'REDEEM', 'SWITCH', 'INSTALMENT']):
                                        match = re.search(r'(\d{2}-[A-Za-z]{3}-\d{4})\s+(.*?)\s+((?:\d+,)*\d+\.\d{2}(?!\d))', line)
                                        if match:
                                            date_str = match.group(1)
                                            desc = match.group(2).upper()
                                            amt = float(match.group(3).replace(',', ''))
                                            
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
                        
                        if total_market_value > 0:
                            st.info(f"✅ Auto-detected Consolidated Portfolio Value: ₹{total_market_value:,.2f} (As of {val_date_str})")
                            val_row = pd.DataFrame([{
                                'Scheme': 'PDF Portfolio',
                                'Date': pd.to_datetime(val_date_str),
                                'Amount': total_market_value,
                                'Type': 'VALUATION'
                            }])
                            df = pd.concat([df, val_row], ignore_index=True)
                            ready_to_calculate = True
                        else:
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
                    st.error(f"Incorrect password or unable to read PDF. Error: {e}")

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
                last_positive_idx = df[df['Amount'] > 0].index.max()
                if pd.notna(last_positive_idx):
                    df.loc[last_positive_idx, 'Type'] = 'VALUATION'
                    val_date_str = df.loc[last_positive_idx, 'Date'].strftime('%Y-%m-%d')
            
            ready_to_calculate = True
        except Exception as e:
            st.error(f"Error processing Excel: {e}")

    # === FINAL CALCULATIONS & DASHBOARD ===
    if ready_to_calculate and df is not None:
        port_dates = df['Date'].tolist()
        port_amounts = df['Amount'].tolist()
        val_date = pd.to_datetime(val_date_str)
        
        try:
            port_xirr = xirr(port_dates, port_amounts)
            start_date = df['Date'].min().strftime('%Y-%m-%d')
            end_date = (val_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            
            with st.spinner('Simulating Index Units & Fetching Nifty 50 NSE Benchmark Data...'):
                nifty50_series = fetch_benchmark_data('^NSEI', start_date, end_date)
                nifty50_xirr, nifty_simulated_value = calculate_true_benchmark(nifty50_series, df, val_date)

            # --- METRICS DASHBOARD ---
            st.markdown("---")
            st.subheader("📊 Performance Summary (Annualized CAGR/XIRR)")
            col1, col2, col3 = st.columns(3)
            col1.metric("Client Portfolio Return", f"{port_xirr * 100:.2f}%" if port_xirr else "N/A")
            col2.metric("Nifty 50 Benchmark", f"{nifty50_xirr * 100:.2f}%" if nifty50_xirr else "N/A")
            if port_xirr and nifty50_xirr:
                alpha = (port_xirr - nifty50_xirr) * 100
                col3.metric("Alpha Generated", f"{alpha:.2f}%", delta=f"{alpha:.2f}%")

            actual_value = df[df['Type'] == 'VALUATION']['Amount'].sum()
            net_invested = abs(df[(df['Type'] == 'CASHFLOW') & (df['Amount'] < 0)]['Amount'].sum()) - df[(df['Type'] == 'CASHFLOW') & (df['Amount'] > 0)]['Amount'].sum()
            
            st.subheader(f"💰 Valuation Summary (As of {val_date.strftime('%d-%b-%Y')})")
            c1, c2, c3 = st.columns(3)
            c1.metric("Net Amount Invested", f"₹{net_invested:,.2f}")
            c2.metric("Actual MF Market Value", f"₹{actual_value:,.2f}")
            c3.metric("Nifty 50 Simulated Value", f"₹{nifty_simulated_value:,.2f}", delta=f"₹{actual_value - nifty_simulated_value:,.2f} vs Index")

            # --- COMPARATIVE LINE CHART ---
            st.subheader("📈 Growth Comparison Over Time")
            st.info("Note: Because PDF statements only provide your final valuation and not daily historical NAVs for your specific funds, your 'Actual MF Investment' is represented by your total Net Cash Invested over time, concluding with a definitive marker showing your final Portfolio Value today.")
            
            if nifty50_series is not None and not nifty50_series.empty:
                cashflows = df[df['Type'] == 'CASHFLOW'].sort_values('Date').copy()
                chart_start = cashflows['Date'].min()
                
                # Build Daily Timeseries
                daily_dates = pd.date_range(start=chart_start, end=val_date, freq='D')
                chart_df = pd.DataFrame({'Date': daily_dates})
                nifty_df = nifty50_series.rename('Nifty_Price').to_frame()
                chart_df = chart_df.merge(nifty_df, left_on='Date', right_index=True, how='left')
                chart_df['Nifty_Price'] = chart_df['Nifty_Price'].ffill().bfill()
                
                daily_cf = cashflows.groupby('Date')['Amount'].sum().reset_index()
                chart_df = chart_df.merge(daily_cf, on='Date', how='left').fillna({'Amount': 0})
                
                # Calculate Daily Cash Position
                chart_df['Invested'] = np.where(chart_df['Amount'] < 0, abs(chart_df['Amount']), 0)
                chart_df['Withdrawn'] = np.where(chart_df['Amount'] > 0, chart_df['Amount'], 0)
                chart_df['Cumulative_Invested'] = chart_df['Invested'].cumsum() - chart_df['Withdrawn'].cumsum()
                
                # Calculate Daily Nifty Simulation
                chart_df['Units_Bought'] = np.where(chart_df['Amount'] < 0, abs(chart_df['Amount']) / chart_df['Nifty_Price'], 0)
                chart_df['Units_Sold'] = np.where(chart_df['Amount'] > 0, chart_df['Amount'] / chart_df['Nifty_Price'], 0)
                chart_df['Cumulative_Units'] = chart_df['Units_Bought'].cumsum() - chart_df['Units_Sold'].cumsum()
                chart_df['Simulated_Nifty_Value'] = chart_df['Cumulative_Units'] * chart_df['Nifty_Price']
                
                # Plot
                fig, ax = plt.subplots(figsize=(10, 5))
                ax.plot(chart_df['Date'], chart_df['Simulated_Nifty_Value'], label='Simulated Nifty 50 Value', color='#ff7f0e', linewidth=2)
                ax.plot(chart_df['Date'], chart_df['Cumulative_Invested'], label='Net Cash Invested', color='#1f77b4', linestyle='--')
                
                # Mark actual end value
                ax.scatter(val_date, actual_value, color='green', s=100, zorder=5, label=f"Actual MF Portfolio Value")
                
                ax.set_title("Simulated Nifty 50 Benchmark vs Cash Invested", fontweight='bold')
                ax.set_ylabel("Value (₹)")
                ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: format(int(x), ',')))
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.legend(loc='upper left')
                
                st.pyplot(fig)
                
        except Exception as e:
            st.error(f"Could not calculate returns. Ensure data has investments and a final valuation. ({e})")
