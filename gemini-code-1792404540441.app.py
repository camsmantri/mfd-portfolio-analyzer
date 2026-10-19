# Install required libraries
!pip install -q pandas numpy requests yfinance openpyxl pyxirr pdfplumber matplotlib seaborn

import pandas as pd
import numpy as np
import requests
import yfinance as yf
import pdfplumber
import re
import getpass
from datetime import datetime
from google.colab import files
from pyxirr import xirr
import matplotlib.pyplot as plt

print("Step 1: Upload your CAMS/KFintech PDF statement")
uploaded = files.upload()
filename = list(uploaded.keys())[0]

# Ask for PDF password securely using standard Python getpass
pdf_password = getpass.getpass("Enter your CAMS/KFintech PDF password (usually your PAN in uppercase): ")

if filename.endswith('.pdf'):
    print("\nReading password-protected CAMS/KFintech PDF Statement...")
    extracted_data = []
    
    try:
        with pdfplumber.open(filename, password=pdf_password) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    lines = text.split('\n')
                    for line in lines:
                        # Scan for typical CAMS transaction rows
                        if any(keyword in line.upper() for keyword in ['PURCHASE', 'SIP', 'REDEEM', 'SWITCH', 'REINVESTMENT']):
                            extracted_data.append({'Page': page_num + 1, 'RawLine': line})
                            
        df_pdf = pd.DataFrame(extracted_data)
        print(f"Success! Extracted {len(df_pdf)} transaction-related lines from the PDF.")
        display(df_pdf.head(10))
        
    except Exception as e:
        print(f"Error opening PDF: {e}. Please check if your password is correct.")
else:
    # Excel Fallback
    df = pd.read_excel(filename)
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
    print("\n--- Excel Data Loaded Successfully ---")
    display(df.head())