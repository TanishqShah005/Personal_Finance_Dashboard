from project import extract_merchant_name, categorize_transaction, get_filtered_query

def test_extract_merchant_name():
    # Test 1: Standard UPI format
    assert extract_merchant_name("UPI/123456/ZOMATO/OKICICI") == "OKICICI"
    # Test 2: Basic stripping
    assert extract_merchant_name("  NETFLIX  ") == "NETFLIX"
    assert extract_merchant_name("") == ""

def test_categorize_transaction():
    # This Dictionary must contain every keyword you will use
    rules = {
        "SWIGGY": "Food",
        "NETFLIX": "Entertainment",
        "SALARY": "Income",
        "UBER": "Transport"
    }
    
    # Test 1: Merchant is in the LAST part
    assert categorize_transaction("POS/WDL/SWIGGY", rules) == ("Food", "Rule (File)")
    
    # Test 2: Merchant is in the SECOND TO LAST part (The fix you requested)
    assert categorize_transaction("UPI/12345/NETFLIX/SUB", rules) == ("Entertainment", "Rule (File)")
    
    # Test 3: No slashes, just a direct keyword
    assert categorize_transaction("SALARY CREDIT", rules) == ("Income", "Rule (File)")
    
    # Test 4: Keyword is too far back
    assert categorize_transaction("SWIGGY/REFUND/BANK/NOW", rules) == ("Uncategorized", "None")


def test_get_filtered_query():
    # Test 1: Basic String Generation
    q = get_filtered_query("TOTALS_SUMMARY")
    assert "SELECT SUM(credit_amt)" in q
    
    # Test 2: Filter Application
    q2 = get_filtered_query("SPENDING_BY_CATEGORY", category_filter="Food")
    assert "category = 'Food'" in q2
    
    # Test 3: Invalid Query Name
    assert get_filtered_query("INVALID_NAME") == ""