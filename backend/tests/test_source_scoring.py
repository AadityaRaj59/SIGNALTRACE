from backend.app.services.source_scoring import score_source

def test_generic_homepages_are_never_accepted_as_financial_evidence():
    for url,title in [('https://www.hdfcbank.com/','Personal Banking'),('https://www.rbi.org.in/','Reserve Bank of India'),('https://www.nseindia.com/','NSE India')]:
        score=score_source(url,title,'Generic navigation and service links only.','HDFC Bank')
        assert score['is_homepage'] is True
        assert score['relevant'] is False

def test_specific_financial_document_is_relevant():
    score=score_source('https://www.hdfcbank.com/content/dam/hdfc/results/q1-fy25-results.pdf','HDFC Bank Q1 FY25 Financial Results','Quarterly results gross NPA ratio deposits advances net interest margin PAT','HDFC Bank')
    assert score['relevant'] is True
    assert 'financial_document_pattern' in score['reasons']
