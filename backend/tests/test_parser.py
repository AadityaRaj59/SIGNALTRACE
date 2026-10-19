from backend.app.services.document_parser import parse_document

def test_html_parser_removes_scripts():
    x=parse_document("<html><head><title>Results</title></head><body><script>secret()</script><p>Quarterly results available</p></body></html>","text/html")
    assert x["title"]=="Results"
    assert "Quarterly results available" in x["text"]
    assert "secret()" not in x["text"]

def test_pdf_parser_reports_non_pdf_as_parse_error():
    from backend.app.services.document_parser import parse_document
    result = parse_document(b"not a real pdf", "application/pdf")
    assert result["text"] == ""
    assert result["parse_error"]

def test_plain_text_parser_preserves_financial_text():
    result = parse_document("Net interest income was 123.4 crore in FY 2025.", "text/plain")
    assert "123.4 crore" in result["text"]
    assert result["parse_error"] is None


def test_extracts_published_date_from_html_meta():
    from backend.app.services.document_parser import parse_document
    html = '<html><head><meta property="article:published_time" content="2025-04-30T10:00:00Z"><title>Results</title></head><body>Financial results</body></html>'
    parsed = parse_document(html, "text/html")
    assert parsed["publication_date"] == "2025-04-30T10:00:00Z"

def test_html_table_preserves_period_headers_and_rows():
    from backend.app.services.document_parser import parse_document
    parsed=parse_document('''<html><body><nav>menu</nav><h1>Quarterly results</h1><table><caption>Consolidated amounts in INR crore</caption><tr><th>Metric</th><th>Q1 FY25</th><th>Q1 FY24</th></tr><tr><td>Total Advances</td><td>1,200</td><td>1,000</td></tr></table></body></html>''','text/html')
    assert 'Metric | Q1 FY25 | Q1 FY24' in parsed['text']
    assert 'Total Advances | 1,200 | 1,000' in parsed['text']
    assert 'menu' not in parsed['text']
    from backend.app.services.financial_extraction import _table_candidates
    observations=_table_candidates({'id':1,'url':'https://example.org/results','content_text':parsed['text']})
    assert len(observations)==2
    assert observations[0]['metric_id']=='total_advances'
    assert observations[0]['normalized_value']=='12000000000'

def test_date_regex_extracts_text_dates():
    parsed=parse_document('<html><body><main>Results released October 19, 2026. Financial results for Q2 FY27.</main></body></html>','text/html')
    assert parsed['publication_date']=='October 19, 2026'
