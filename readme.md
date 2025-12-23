# Personal Finance Dashboard

# Description:
A comprehensive personal finance dashboard built with Python and Streamlit. This application solves the tedious problem of manual expense tracking by automating the extraction, cleaning, and categorization of transaction data directly from bank statement PDFs.
Instead of manually entering data into Excel, users can simply upload their PDF statements. The app processes the file, identifies merchants (like Swiggy, Uber, or Netflix), and visualizes spending habits instantly.

# Key Features:

* PDF Parsing: Uses `tabula-py` and a custom "Brute Force" search algorithm to extract transaction tables from PDF bank statements, independent of specific column layouts.
* Smart Categorization: Automatically tags transactions (e.g., "Food", "Transport") based on keywords defined in a `rules.csv` file. It uses robust string matching to find keywords anywhere in the transaction description.
* Data Cleaning: Uses Regular Expressions (Regex) to strip noise like "500.00 (Dr)" from remarks, ensuring clean and readable text.
* Database Integration: Stores data in a local MySQL database to prevent duplicates and allow for long-term historical analysis.
* Interactive Dashboard:
  * KPI Metrics: Real-time tracking of Income, Spending, and Net Balance.
  * Visualizations: Interactive Bar charts and Trend lines using Plotly.
  * Filters: Slice data by Date, Category, or Day of the Week.
* Database Management: Includes a sidebar utility to "Reset" the database for fresh starts or debugging.

# Technologies Used:
* Python 3
* Streamlit (Web Interface)
* Pandas (Data Manipulation)
* MySQL Connector (Database Backend)
* Tabula-py (PDF Extraction)
* Plotly (Data Visualization)
  
# Installation:

1. Clone the repository:
bash
git clone https://github.com/TanishqShah005/Personal_Finance_Dashboard.git

2. Install dependencies:
bash
pip install -r requirements.txt

3. Configure MySQL credentials in `project.py`.

4. Run the application:
bash
streamlit run project.py

# Project Structure:

* `project.py`: Main application code containing UI and logic.
* `test_project.py`: Unit tests for core logic (categorization, query generation).
* `rules.csv`: Configurable rules for mapping keywords to categories.
* `requirements.txt`: Project dependencies.

# Credits:
Created by Tanishq for the *CS50P* Final Project.
