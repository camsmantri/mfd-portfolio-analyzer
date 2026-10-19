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
import tempfile
import os

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="MFD Portfolio Analyzer", layout="wide")
st.title("📊 MFD Portfolio vs Benchmark Analyzer")
st.write("Upload your client's mutual fund statement to compare their returns against the Nifty 50 index.")

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
        st.info("If your PDF is password protected (usually your PAN), enter it below. If it is already unlocked, leave this blank.")
        pdf_password = st.text_input("🔒 PDF Password (Optional):", type="password")
        
        if st.button("Analyze Portfolio"):
            with st.spinner("Unlocking, reading formatting, and consolidating market values..."):
                pwd_to_use = pdf_password if pdf_password else ""
                
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                    tmp_file.write(uploaded_file.getvalue())
                    tmp_file_path = tmp_file.name

                try:
                    cashflows = []
                    total_market_value = 0.0
                    total_cost_value = 0.0
                    statement_start_date = None
                    
                    with pdfplumber.open(tmp_file_path, password=pwd_to_use) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text:
                                # 1. Extract Period
                                if not statement_start_date:
                                    period_match = re.search(r'(\d{2}-[A-Za-z]{3}-\d{4})\s+To\s+(\d{2}-[A-Za-z]{3}-\d{4})', text, re.IGNORECASE)
                                    if period_match:
                                        statement_start_date = pd.to_datetime(period_match.group(1))

                                # 2. Aggregate Total Market Value
                                val_matches = re.findall(r'Market Value on (\d{2}-[A-Za-z]{3}-\d{4}).*?((?:\d+,)*\d+\.\d{2})', text, re.IGNORECASE)
                                for match in val_matches:
                                    val_date_str = match[0]
                                    total_market_value += float(match[1].replace(',', ''))
                                    
                                # 3. Aggregate Total Historical Cost Value
                                cost_matches = re.findall(r'Total Cost Value:.*?((?:\d+,)*\d+\.\d{2})', text, re.IGNORECASE)
                                for match_str in cost_matches:
                                    total_cost_value += float(match_str.replace(',', ''))
                                
                                # 4. Extract transaction cashflows
                                for line in text.split('\n'):
                                    if any(kw in line.upper() for kw in ['PURCHASE', 'SIP', 'REDEEM', 'SWITCH', 'INSTALMENT']):
                                        match = re.search(r'(\d{2}-[A-Za-z]{3}-\d{4})[\s|]+(.*?)[\s|]+((?:\d+,)*\d+\.\d{2}(?!\d))', line)
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
                        parsed_invested = sum(abs(cf['Amount']) for cf in cashflows if cf['Amount'] < 0)
                        missing_historical_cost = total_cost_value - parsed_invested
                        
                        if missing_historical_cost > 10: 
                            inject_date = statement_start_date if statement_start_date else min([cf['Date'] for cf in cashflows])
                            cashflows.append({
                                'Scheme': 'Opening Balance (Historical Cost)',
                                'Date': inject_date,
                                'Amount': -missing_historical_cost,
                                'Type': 'CASHFLOW'
                            })

                        df = pd.DataFrame(cashflows)
                        st.success("Mapped transactions & historical opening balances successfully!")
                        
                        if total_market_value > 0:
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
                    else:
                        st.error("Could not find any standard CAMS/KFintech transactions.")
                        
                except Exception as e:
                    st.error(f"Failed to process PDF. Error details: {str(e)}")
                finally:
                    if os.path.exists(tmp_file_path):
                        os.remove(tmp_file_path)

    # === EXCEL PROCESSING ===
    elif file_extension == 'xlsx':
        if st.button("Analyze Portfolio"):
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
                
                try:
                    nifty_index_level = nifty50_series.asof(val_date)
                    if pd.isna(nifty_index_level): nifty_index_level = nifty50_series.dropna().iloc[-1]
                except:
                    nifty_index_level = 0.0

            # --- CALCULATE ABSOLUTE & NET DATA ---
            actual_value = df[df['Type'] == 'VALUATION']['Amount'].sum()
            net_invested = abs(df[(df['Type'] == 'CASHFLOW') & (df['Amount'] < 0)]['Amount'].sum()) - df[(df['Type'] == 'CASHFLOW') & (df['Amount'] > 0)]['Amount'].sum()
            
            port_abs_ret = ((actual_value - net_invested) / net_invested) * 100 if net_invested > 0 else 0
            nifty_abs_ret = ((nifty_simulated_value - net_invested) / net_invested) * 100 if net_invested > 0 else 0

            # --- METRICS DASHBOARD ---
            st.markdown("---")
            
            # 1. Valuation Summary
            st.subheader(f"💰 Valuation & Index Data (As of {val_date.strftime('%d-%b-%Y')})")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Net Invested", f"₹{net_invested:,.2f}")
            c2.metric("Actual MF Market Value", f"₹{actual_value:,.2f}")
            c3.metric(f"Nifty 50 Index Level", f"{nifty_index_level:,.2f}")
            c4.metric("Nifty 50 Simulated Value", f"₹{nifty_simulated_value:,.2f}", delta=f"₹{actual_value - nifty_simulated_value:,.2f} vs Index")
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # 2. Absolute Returns
            st.subheader("📈 Absolute Return (Total Profit %)")
            ca1, ca2, ca3 = st.columns(3)
            ca1.metric("Client Portfolio (Absolute)", f"{port_abs_ret:.2f}%")
            ca2.metric("Nifty 50 Benchmark (Absolute)", f"{nifty_abs_ret:.2f}%")
            ca3.metric("Alpha Generated (Absolute)", f"{(port_abs_ret - nifty_abs_ret):.2f}%", delta=f"{(port_abs_ret - nifty_abs_ret):.2f}%")
            
            st.markdown("<br>", unsafe_allow_html=True)

            # 3. Annualized XIRR
            st.subheader("⏱️ Annualized Return (Time-Weighted XIRR)")
            cx1, cx2, cx3 = st.columns(3)
            cx1.metric("Client Portfolio XIRR", f"{port_xirr * 100:.2f}%" if port_xirr else "N/A")
            cx2.metric("Nifty 50 Benchmark XIRR", f"{nifty50_xirr * 100:.2f}%" if nifty50_xirr else "N/A")
            if port_xirr and nifty50_xirr:
                alpha_xirr = (port_xirr - nifty50_xirr) * 100
                cx3.metric("Alpha Generated (XIRR)", f"{alpha_xirr:.2f}%", delta=f"{alpha_xirr:.2f}%")

            # --- COMPARATIVE LINE CHART ---
            st.markdown("---")
            st.subheader("📊 Growth Comparison Over Time")
            
            if nifty50_series is not None and not nifty50_series.empty:
                cashflows = df[df['Type'] == 'CASHFLOW'].sort_values('Date').copy()
                chart_start = cashflows['Date'].min()
                
                daily_dates = pd.date_range(start=chart_start, end=val_date, freq='D')
                chart_df = pd.DataFrame({'Date': daily_dates})
                nifty_df = nifty50_series.rename('Nifty_Price').to_frame()
                chart_df = chart_df.merge(nifty_df, left_on='Date', right_index=True, how='left')
                chart_df['Nifty_Price'] = chart_df['Nifty_Price'].ffill().bfill()
                
                daily_cf = cashflows.groupby('Date')['Amount'].sum().reset_index()
                chart_df = chart_df.merge(daily_cf, on='Date', how='left').fillna({'Amount': 0})
                
                chart_df['Invested'] = np.where(chart_df['Amount'] < 0, abs(chart_df['Amount']), 0)
                chart_df['Withdrawn'] = np.where(chart_df['Amount'] > 0, chart_df['Amount'], 0)
                chart_df['Cumulative_Invested'] = chart_df['Invested'].cumsum() - chart_df['Withdrawn'].cumsum()
                
                chart_df['Units_Bought'] = np.where(chart_df['Amount'] < 0, abs(chart_df['Amount']) / chart_df['Nifty_Price'], 0)
                chart_df['Units_Sold'] = np.where(chart_df['Amount'] > 0, chart_df['Amount'] / chart_df['Nifty_Price'], 0)
                chart_df['Cumulative_Units'] = chart_df['Units_Bought'].cumsum() - chart_df['Units_Sold'].cumsum()
                chart_df['Simulated_Nifty_Value'] = chart_df['Cumulative_Units'] * chart_df['Nifty_Price']
                
                fig, ax = plt.subplots(figsize=(10, 5))
                ax.plot(chart_df['Date'], chart_df['Simulated_Nifty_Value'], label='Simulated Nifty 50 Value', color='#ff7f0e', linewidth=2)
                ax.plot(chart_df['Date'], chart_df['Cumulative_Invested'], label='Net Cash Invested', color='#1f77b4', linestyle='--')
                ax.scatter(val_date, actual_value, color='green', s=100, zorder=5, label=f"Actual MF Portfolio Value")
                
                ax.set_title("Simulated Nifty 50 Benchmark vs Cash Invested", fontweight='bold')
                ax.set_ylabel("Value (₹)")
                ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: format(int(x), ',')))
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.legend(loc='upper left')
                
                st.pyplot(fig)
                
        except Exception as e:
            st.error(f"Could not calculate returns. Ensure data has investments and a final valuation. Error details: {e}")
