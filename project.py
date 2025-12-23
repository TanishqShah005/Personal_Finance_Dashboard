import streamlit as st
import pandas as pd
import mysql.connector
from mysql.connector import Error
import plotly.express as px
from datetime import datetime
import tabula
import re
import io
import os

# Configuration
DB_CONFIG = {
    "host": "localhost",
    "user": "mysql_username",        # <------- change your MySQL username
    "password": "mysql_password", # <------- change your MySQL password
    "database": "Personal_Finance_Dashboard_db"
}

MAX_SPENDING_LIMIT = 20000

def categorize_transaction(remark, rules):
    if not isinstance(remark, str) or not rules:
        return "Uncategorized", "None"
    remark_upper = remark.upper()
    for rule_keyword, category in rules.items():
        cleaned_rule = str(rule_keyword).strip().upper()
        if cleaned_rule and cleaned_rule in remark_upper:
            return category, 'Rule (File)'
    return "Uncategorized", "None"

def get_filtered_query(query_name, start_date=None, end_date=None, max_debit=None, category_filter=None, day_filter=None):
    base_queries = {
        "TOTALS_SUMMARY": "SELECT SUM(credit_amt) AS total_income, SUM(debit_amt) AS total_spending FROM transactions",
        "SPENDING_OVER_TIME": "SELECT transaction_date, debit_amt AS spending FROM transactions WHERE debit_amt > 0",
        "TRANSACTIONS_TABLE": "SELECT transaction_date, remark, debit_amt, credit_amt, category FROM transactions",
    }
    if query_name not in base_queries: return ""

    query = base_queries[query_name]
    where_clauses = []

    if start_date and end_date:
        where_clauses.append(f"transaction_date BETWEEN '{start_date}' AND '{end_date}'")
    if category_filter and category_filter != 'All Categories':
        where_clauses.append(f"category = '{category_filter}'")
    if max_debit is not None and query_name != "TOTALS_SUMMARY":
         where_clauses.append(f"debit_amt <= {max_debit}")
    if day_filter == 'Weekend':
        where_clauses.append("DAYOFWEEK(transaction_date) IN (7, 1)")
    elif day_filter == 'Weekday':
        where_clauses.append("DAYOFWEEK(transaction_date) BETWEEN 2 AND 6")
    elif day_filter:
        day_map = {'Monday': 2, 'Tuesday': 3, 'Wednesday': 4, 'Thursday': 5, 'Friday': 6, 'Saturday': 7, 'Sunday': 1}
        if day_filter in day_map:
            where_clauses.append(f"DAYOFWEEK(transaction_date) = {day_map[day_filter]}")

    if where_clauses:
        connector = " AND " if "WHERE" in query else " WHERE "
        query += connector + " AND ".join(where_clauses)
    
    if query_name == "TRANSACTIONS_TABLE": query += " ORDER BY transaction_date DESC"
    elif query_name == "SPENDING_OVER_TIME": query += " ORDER BY transaction_date ASC"
    
    return query

def get_db_connection():
    try: return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        st.error(f"Database Error: {e}")
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
            categorization_method VARCHAR(50)
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
    init_db()
    try:
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    except Exception as e:
        if "doesn't exist" in str(e):
             init_db()
             try:
                 conn = get_db_connection()
                 df = pd.read_sql(query, conn)
                 conn.close()
                 return df
             except: pass
        st.error(f"SQL Error: {e}")
        if conn.is_connected(): conn.close()
        return pd.DataFrame()

def fetch_rules():
    rules = {}
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
             st.error("'rules.csv' is missing or empty.")
             return

        pdf_bytes = uploaded_file.read()
        df_list = tabula.read_pdf(io.BytesIO(pdf_bytes), pages='all', multiple_tables=True, pandas_options={'header': None, 'dtype': str}, stream=True)
        if not df_list: 
            st.error("No tables found in PDF.")
            return
        
        raw_df = pd.concat(df_list, ignore_index=True)
        date_col = None
        for col in raw_df.columns:
            if raw_df[col].astype(str).str.match(r'\d{2}-\d{2}-\d{4}').sum() > 2:
                date_col = col
                break
        
        if date_col is None:
            st.error("Could not find a 'Date' column (DD-MM-YYYY).")
            return

        df = raw_df.copy()
        df = df.rename(columns={date_col: 'transaction_date'})
        df['transaction_date'] = pd.to_datetime(df['transaction_date'], format="%d-%m-%Y", errors='coerce').dt.date
        df = df.dropna(subset=['transaction_date'])

        other_cols = [c for c in df.columns if c != 'transaction_date']
        df['raw_text'] = df[other_cols].apply(lambda row: ' '.join(row.values.astype(str)), axis=1)

        def extract_amounts(row_str):
            clean_str = str(row_str).replace(',', '').upper()
            match = re.search(r'(\d+\.?\d*)\s*\(?(DR|CR)\)?', clean_str)
            debit, credit = 0.0, 0.0
            if match:
                amt = float(match.group(1))
                type_ = match.group(2)
                if 'DR' in type_: debit = amt
                if 'CR' in type_: credit = amt
            return debit, credit

        df[['debit_amt', 'credit_amt']] = df['raw_text'].apply(lambda x: pd.Series(extract_amounts(x)))
        df = df[(df['debit_amt'] > 0) | (df['credit_amt'] > 0)]

        df[['category', 'categorization_method']] = df['raw_text'].apply(lambda x: pd.Series(categorize_transaction(x, rules)))

        df['remark'] = df['raw_text'].str.replace(r'[0-9,]+\.?[0-9]*\s*\(?(?:DR|CR|Dr|Cr)\)?', '', regex=True)
        df['remark'] = df['remark'].str.replace(r'[0-9,]+\.[0-9]{2}', '', regex=True)
        df['remark'] = df['remark'].str.replace('nan', '', case=False).str.replace(r'\s+', ' ', regex=True).str.strip()

        df['closing_balance'] = 0.0 
        cursor = conn.cursor()
        
        dates = tuple(df['transaction_date'].astype(str).unique())
        if dates:
            fmt = ','.join(['%s'] * len(dates))
            cursor.execute(f"DELETE FROM transactions WHERE transaction_date IN ({fmt})", dates)
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

def main():
    st.set_page_config(layout="wide", page_title="Pocket Money Tracker")
    st.title("Personal Finance Dashboard")

    # --- SESSION STATE TO CONTROL FILE UPLOADER RESET ---
    if "uploader_key" not in st.session_state:
        st.session_state["uploader_key"] = 0
    
    conn = get_db_connection()
    if not conn:
        st.warning("Database not connected. Check config.")
        st.stop()
    
    with st.sidebar:
        st.header("Upload")
        
        # KEY PARAMETER: Changing this key forces the widget to rebuild (clearing the file)
        uploaded_file = st.file_uploader(
            "Bank Statement (PDF)", 
            type="pdf", 
            key=f"uploader_{st.session_state['uploader_key']}"
        )
        
        if uploaded_file:
            # We don't clear cache immediately here to avoid loop, we rely on logic
            process_uploaded_pdf(uploaded_file, conn)
        
        st.header("Actions")
        if st.button("RESET DATABASE"):
            try:
                c = conn.cursor()
                c.execute("TRUNCATE TABLE transactions")
                conn.commit()
                c.close()
                st.success("Database Wiped!")
                
                # CRITICAL: Change key to kill the file uploader so it doesn't re-upload automatically
                st.session_state["uploader_key"] += 1 
                
                st.cache_data.clear()
                st.rerun()
            except Error as e:
                st.error(f"Reset Failed: {e}")

        # Filters
        st.header("Filters")
        try:
            df_d = run_query("SELECT MIN(transaction_date) as mn, MAX(transaction_date) as mx FROM transactions")
            if not df_d.empty and pd.notna(df_d['mn'].iloc[0]):
                mn, mx = df_d['mn'].iloc[0], df_d['mx'].iloc[0]
            else:
                mn, mx = datetime.now().date(), datetime.now().date()
        except: 
            mn, mx = datetime.now().date(), datetime.now().date()
            
        s_date = st.date_input("Start", mn)
        e_date = st.date_input("End", mx)
        
        # Safety for None types
        if s_date is None: s_date = datetime.now().date()
        if e_date is None: e_date = datetime.now().date()
        
        try:
            df_c = run_query("SELECT DISTINCT category FROM transactions")
            cats = ['All Categories'] + (df_c.iloc[:, 0].tolist() if not df_c.empty else [])
        except: cats = ['All Categories']
            
        cat_sel = st.selectbox("Category", cats)
        day_sel = st.selectbox("Day", ['All Days', 'Weekend', 'Weekday', 'Monday', 'Friday'])

    # Dashboard Body
    s_str, e_str = s_date.strftime('%Y-%m-%d'), e_date.strftime('%Y-%m-%d')
    
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
    c3.metric("Net Flow", f"₹{inc-spn:,.0f}")
    
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
