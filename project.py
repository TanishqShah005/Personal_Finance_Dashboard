import streamlit as st
import pandas as pd
import mysql.connector
from mysql.connector import Error
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import tabula
import re
import io
import os

# Configuration
DB_CONFIG = {
    "host": "localhost",
    "user": "root", # <------- use your MySQL username
    "password": "mysql@123", # <------- use your MySQL password
    "database": "Personal_Finance_Dashboard_db"
}

MAX_SPENDING_LIMIT = 20000

def extract_merchant_name(remark):
    """
    Extracts the likely merchant name from a bank remark string.
    Example: 'UPI/12345/SWIGGY/BAN' -> 'SWIGGY'
    """
    if not isinstance(remark, str):
        return ""
    if '/' in remark:
        parts = remark.split('/')
        return parts[-1].strip()
    return remark.strip()


def categorize_transaction(remark, rules):
    """
    Checks if a rule keyword exists anywhere in the remark string.
    """
    if not isinstance(remark, str):
        return "Uncategorized", "None"
    
    if not rules:
        return "Uncategorized", "None"

    # Normalize the remark (uppercase)
    remark_upper = remark.upper()

    for rule_keyword, category in rules.items():
        # Clean the rule keyword (e.g., "SWIGGY")
        cleaned_rule = str(rule_keyword).strip().upper()
        
        if not cleaned_rule:
            continue

        # Example: Is "SWIGGY" inside "UPI/SWIGGY/CHRISTMAS"? -> YES
        if cleaned_rule in remark_upper:
            return category, 'Rule (File)'

    return "Uncategorized", "None"

def process_uploaded_pdf(uploaded_file, conn):
    init_db()
    try:
        # 1. DEBUG: Check if rules are loading
        rules = fetch_rules()
        
        st.write("--- DEBUG INFO ---")
        if not rules:
            st.error("CRITICAL ERROR: 'rules.csv' was NOT found or is empty.")
            st.info(f"Looking in folder: {os.getcwd()}")
            st.info("Please ensure 'rules.csv' is in this folder.")
            return # Stop processing if no rules
        else:
            st.success(f"Rules Loaded Successfully: {len(rules)} rules found.")
            # Show the first 5 rules to verify they look correct
            st.json(list(rules.items())[:5]) 
        st.write("--------------------")

        pdf_bytes = uploaded_file.read()
        
        # Read PDF
        df_list = tabula.read_pdf(io.BytesIO(pdf_bytes), pages='all', multiple_tables=True, pandas_options={'header': None, 'dtype': str}, stream=True)
        if not df_list: 
            st.error("No tables found.")
            return
        
        df = pd.concat(df_list, ignore_index=True).iloc[:, :5]
        df.columns = ['transaction_date', 'remark', 'chq', 'amt', 'bal']
        df = df.dropna(subset=['transaction_date'])
        
        # Currency Cleaning
        temp = df['amt'].astype(str).str.split(r'\(', expand=True)
        cleaned_nums = temp[0].str.replace(r'[^\d.]', '', regex=True)
        df['debit_amt'] = pd.to_numeric(cleaned_nums.where(temp[1].str.contains('DR', case=False, na=False), 0), errors='coerce').fillna(0)
        df['credit_amt'] = pd.to_numeric(cleaned_nums.where(temp[1].str.contains('CR', case=False, na=False), 0), errors='coerce').fillna(0)
        df['closing_balance'] = pd.to_numeric(df['bal'].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce')
        df['transaction_date'] = pd.to_datetime(df['transaction_date'], format="%d-%m-%Y", errors='coerce').dt.date
        df = df.dropna(subset=['transaction_date'])
        df = df[(df['debit_amt'] > 0) | (df['credit_amt'] > 0)]

        # Apply Categorization
        df[['category', 'categorization_method']] = df['remark'].apply(lambda x: pd.Series(categorize_transaction(x, rules)))
        
        # Insert into Database
        cursor = conn.cursor()
        
        # HARD RESET FOR TESTING: Delete old data for these dates to force a fresh insert
        dates = tuple(df['transaction_date'].astype(str).unique())
        if dates:
            format_strings = ','.join(['%s'] * len(dates))
            cursor.execute(f"DELETE FROM transactions WHERE transaction_date IN ({format_strings})", dates)
            conn.commit()

        data = [tuple(x) for x in df[['transaction_date', 'remark', 'debit_amt', 'credit_amt', 'closing_balance', 'category', 'categorization_method']].values]
        q = """INSERT INTO transactions (transaction_date, remark, debit_amt, credit_amt, closing_balance, category, categorization_method) 
               VALUES (%s, %s, %s, %s, %s, %s, %s)"""
        
        if data:
            cursor.executemany(q, data)
            conn.commit()
            st.success(f"Processed {len(data)} transactions.")
        cursor.close()
        
    except Exception as e:
        st.error(f"Error: {e}")


def get_filtered_query(query_name, start_date=None, end_date=None, max_debit=None, category_filter=None, day_filter=None):
    """
    Generates a SQL query string based on filters. But does NOT execute the query.
    """
    base_queries = {
        "TOTALS_SUMMARY": "SELECT SUM(credit_amt) AS total_income, SUM(debit_amt) AS total_spending FROM transactions",
        "SPENDING_BY_CATEGORY": "SELECT category, SUM(debit_amt) AS total_spent FROM transactions WHERE debit_amt > 0 AND category != 'Uncategorized'",
        "SPENDING_OVER_TIME": "SELECT transaction_date, debit_amt AS spending FROM transactions WHERE debit_amt > 0",
        "TRANSACTIONS_TABLE": "SELECT transaction_date, remark, debit_amt, credit_amt, category, categorization_method FROM transactions",
        "MONTHLY_CATEGORY_TREND": "SELECT DATE_FORMAT(transaction_date, '%Y-%m') AS month_year, SUM(debit_amt) AS total_spent FROM transactions WHERE debit_amt > 0"
    }

    if query_name not in base_queries:
        return ""

    query = base_queries[query_name]
    where_clauses = []

    # Safe quoting for dates
    if start_date and end_date:
        where_clauses.append(f"transaction_date BETWEEN '{start_date}' AND '{end_date}'")

    if category_filter and category_filter != 'All Categories':
        where_clauses.append(f"category = '{category_filter}'")

    if max_debit is not None and query_name != "TOTALS_SUMMARY":
         where_clauses.append(f"debit_amt <= {max_debit}")

    # Day Logic
    day_map = {'Monday': 2, 'Tuesday': 3, 'Wednesday': 4, 'Thursday': 5, 'Friday': 6, 'Saturday': 7, 'Sunday': 1}
    
    if day_filter == 'Weekend':
        where_clauses.append(f"DAYOFWEEK(transaction_date) IN (7, 1)")
    elif day_filter == 'Weekday':
        where_clauses.append(f"DAYOFWEEK(transaction_date) BETWEEN 2 AND 6")
    elif day_filter in day_map:
        where_clauses.append(f"DAYOFWEEK(transaction_date) = {day_map[day_filter]}")

    # Combine
    if where_clauses:
        connector = " AND " if "WHERE" in query else " WHERE "
        query += connector + " AND ".join(where_clauses)

    # Ordering
    if query_name == "SPENDING_BY_CATEGORY":
        query += " GROUP BY category ORDER BY total_spent DESC"
    elif query_name == "TRANSACTIONS_TABLE":
        query += " ORDER BY transaction_date DESC"
    elif query_name == "SPENDING_OVER_TIME":
        query += " ORDER BY transaction_date ASC"
    elif query_name == "MONTHLY_CATEGORY_TREND":
        query += " GROUP BY month_year ORDER BY month_year ASC"

    return query


def get_db_connection():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        st.error(f"Database Connection Error: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    try:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            transaction_date DATE,
            remark TEXT,
            debit_amt DECIMAL(10,2) DEFAULT 0.00,
            credit_amt DECIMAL(10,2) DEFAULT 0.00,
            closing_balance DECIMAL(15,2),
            category VARCHAR(255) DEFAULT 'Uncategorized',
            categorization_method VARCHAR(50),
            is_processed BOOLEAN DEFAULT 0,
            UNIQUE KEY unique_txn (transaction_date, remark(100), closing_balance) 
        );
        """)
        conn.commit()
    except Error: pass
    finally:
        cursor.close()
        conn.close()

def run_query(query):
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    try:
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    except Exception as e:
        st.error(f"SQL Error: {e}")
        if conn.is_connected(): conn.close()
        return pd.DataFrame()

def fetch_rules():
    rules = {}
    # Prioritize CSV
    if os.path.exists("rules.csv"):
        try:
            df = pd.read_csv("rules.csv")
            if 'keyword_match' in df.columns and 'category_name' in df.columns:
                rules = dict(zip(df['keyword_match'].astype(str).str.upper(), df['category_name']))
        except: pass
    return rules

def process_uploaded_pdf(uploaded_file, conn):
    init_db()
    try:
        rules = fetch_rules()
        if not rules:
             st.error("Critical: 'rules.csv' is missing or empty.")
             return

        pdf_bytes = uploaded_file.read()
        
        # 1. Read PDF
        df_list = tabula.read_pdf(io.BytesIO(pdf_bytes), pages='all', multiple_tables=True, pandas_options={'header': None, 'dtype': str}, stream=True)
        if not df_list: 
            st.error("No tables found in PDF.")
            return
        
        # Combine all tables
        raw_df = pd.concat(df_list, ignore_index=True)

        # 2. Column Mapping
        date_col = None
        for col in raw_df.columns:
            if raw_df[col].astype(str).str.match(r'\d{2}-\d{2}-\d{4}').sum() > 2:
                date_col = col
                break
        
        if date_col is None:
            st.error("Could not find a 'Date' column (DD-MM-YYYY).")
            return

        # 3. Rename & Clean
        df = raw_df.copy()
        df = df.rename(columns={date_col: 'transaction_date'})
        df['transaction_date'] = pd.to_datetime(df['transaction_date'], format="%d-%m-%Y", errors='coerce').dt.date
        df = df.dropna(subset=['transaction_date'])

        # 4. Construct "FULL REMARK"
        other_cols = [c for c in df.columns if c != 'transaction_date']
        df['raw_text'] = df[other_cols].apply(lambda row: ' '.join(row.values.astype(str)), axis=1)

        # 5. Extract Amounts
        def extract_amounts(row_str):
            clean_str = str(row_str).replace(',', '').upper()
            match = re.search(r'(\d+\.?\d*)\s*\(?(DR|CR)\)?', clean_str)
            debit, credit = 0.0, 0.0
            if match:
                amount = float(match.group(1))
                type_ = match.group(2)
                if 'DR' in type_: debit = amount
                if 'CR' in type_: credit = amount
            return debit, credit

        df[['debit_amt', 'credit_amt']] = df['raw_text'].apply(lambda x: pd.Series(extract_amounts(x)))
        df = df[(df['debit_amt'] > 0) | (df['credit_amt'] > 0)]

        # 6. Clean the remark
        
        # Step A: Remove "Amount + Dr/Cr"
        df['remark'] = df['raw_text'].str.replace(r'[0-9,]+\.?[0-9]*\s*\(?(?:DR|CR|Dr|Cr)\)?', '', regex=True)
        
        # Step B: Remove Standalone Currency/Balance Numbers
        # This matches any number with a decimal point and 2 digits at the end
        df['remark'] = df['remark'].str.replace(r'[0-9,]+\.[0-9]{2}', '', regex=True)

        # Step C: Cleanup noise
        df['remark'] = df['remark'].str.replace('nan', '', case=False)
        df['remark'] = df['remark'].str.replace(r'\s+', ' ', regex=True).str.strip()

        # 7. Apply Categorization
        df[['category', 'categorization_method']] = df['remark'].apply(lambda x: pd.Series(categorize_transaction(x, rules)))

        # 8. Database Insert
        df['closing_balance'] = 0.0 
        cursor = conn.cursor()
        
        # Delete overlaps to handle updates
        dates = tuple(df['transaction_date'].astype(str).unique())
        if dates:
            format_strings = ','.join(['%s'] * len(dates))
            cursor.execute(f"DELETE FROM transactions WHERE transaction_date IN ({format_strings})", dates)
            conn.commit()

        data = [tuple(x) for x in df[['transaction_date', 'remark', 'debit_amt', 'credit_amt', 'closing_balance', 'category', 'categorization_method']].values]
        q = """INSERT INTO transactions (transaction_date, remark, debit_amt, credit_amt, closing_balance, category, categorization_method) 
               VALUES (%s, %s, %s, %s, %s, %s, %s)"""
        
        if data:
            cursor.executemany(q, data)
            conn.commit()
            st.success(f"Success! Processed {len(data)} transactions.")
        
        cursor.close()

    except Exception as e:
        st.error(f"Error: {e}")

# ============
# Dashboard UI
# ============

def main():
    st.set_page_config(layout="wide", page_title="Pocket Money Tracker")
    st.title("Personal Finance Dashboard")
    
    # 1. DEBUG: Show us exactly what files Python sees
    import os
    st.sidebar.header("Debug Info")
    if os.path.exists("rules.csv"):
        st.sidebar.success(f"Found 'rules.csv' ({os.path.getsize('rules.csv')} bytes)")
    else:
        st.sidebar.error("'rules.csv' NOT FOUND in this folder!")
        st.sidebar.text(f"Looking in: {os.getcwd()}")

    conn = get_db_connection()
    if not conn:
        st.warning("Database not connected.")
        st.stop()
    
    # Sidebar Actions
    with st.sidebar:
        st.markdown("---")
        st.header("1. Upload")
        uploaded_file = st.file_uploader("Upload Bank Statement (PDF)", type="pdf")
        
        if uploaded_file:
            # Forces Reload everytime
            st.cache_data.clear()
            process_uploaded_pdf(uploaded_file, conn)
        
        st.markdown("---")
        st.header("2. Emergency Zone")
        if st.button("NUKE DATABASE (Delete All)"):
            c = conn.cursor()
            c.execute("DROP TABLE IF EXISTS transactions")
            conn.commit()
            c.close()
            st.error("Database Wiped! Please refresh page and re-upload.")
            st.cache_data.clear()

        # Date & Category Filters
        st.markdown("---")
        st.header("3. Filters")
        
        # Safe Date Loading
        try:
            df_d = run_query("SELECT MIN(transaction_date) as mn, MAX(transaction_date) as mx FROM transactions")
            if not df_d.empty and df_d['mn'].iloc[0]:
                mn, mx = df_d['mn'].iloc[0], df_d['mx'].iloc[0]
            else:
                mn, mx = datetime.now().date(), datetime.now().date()
        except:
            mn, mx = datetime.now().date(), datetime.now().date()
            
        s_date = st.date_input("Start", mn)
        e_date = st.date_input("End", mx)
        
        # Safe Category Loading
        try:
            df_c = run_query("SELECT DISTINCT category FROM transactions")
            cats = ['All Categories'] + (df_c.iloc[:, 0].tolist() if not df_c.empty else [])
        except:
            cats = ['All Categories']
            
        cat_sel = st.selectbox("Category", cats)
        day_sel = st.selectbox("Day", ['All Days', 'Weekend', 'Weekday', 'Monday', 'Friday'])

    # --- Visuals Section ---
    s_str, e_str = s_date.strftime('%Y-%m-%d'), e_date.strftime('%Y-%m-%d')
    
    # Totals
    q_totals = get_filtered_query("TOTALS_SUMMARY", s_str, e_str, MAX_SPENDING_LIMIT, cat_sel, day_sel)
    df_t = run_query(q_totals)
    
    inc = 0.0
    spn = 0.0
    if not df_t.empty:
        if 'total_income' in df_t.columns and pd.notna(df_t['total_income'].iloc[0]): inc = df_t['total_income'].iloc[0]
        if 'total_spending' in df_t.columns and pd.notna(df_t['total_spending'].iloc[0]): spn = df_t['total_spending'].iloc[0]

    c1, c2, c3 = st.columns(3)
    c1.metric("Income", f"₹{inc:,.0f}")
    c2.metric("Spending", f"₹{spn:,.0f}")
    c3.metric("Net", f"₹{inc-spn:,.0f}")
    
    col_gr, col_tb = st.columns([1, 1])
    
    with col_gr:
        q_trend = get_filtered_query("SPENDING_OVER_TIME", s_str, e_str, MAX_SPENDING_LIMIT, cat_sel, day_sel)
        df_trend = run_query(q_trend)
        if not df_trend.empty:
            st.plotly_chart(px.bar(df_trend, x='transaction_date', y='spending', title="Daily Spending"), use_container_width=True)
            
    with col_tb:
        st.subheader("Transaction List")
        q_tab = get_filtered_query("TRANSACTIONS_TABLE", s_str, e_str, MAX_SPENDING_LIMIT, cat_sel, day_sel)
        st.dataframe(run_query(q_tab), use_container_width=True, height=400)
        
    if conn.is_connected(): conn.close()

if __name__ == "__main__":
    main()