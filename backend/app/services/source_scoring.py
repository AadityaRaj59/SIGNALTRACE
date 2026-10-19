"""Deterministic source relevance scoring; homepages/navigation pages are demoted."""
from __future__ import annotations
from urllib.parse import urlparse
import re

FINANCIAL_TERMS=re.compile(r"\b(quarterly results|financial results|annual report|investor presentation|gross npa|net npa|advances|deposits|net interest margin|nii|profit after tax|capital adequacy|exchange filing|audited financial|balance sheet|cash flow|provisions|credit cost|slippages)\b",re.I)
DOC_TERMS=re.compile(r"(annual[-_ ]?report|financial[-_ ]?results|quarterly|investor[-_ ]?presentation|results[-_ ]?pdf|filing|xbrl|financial[-_ ]?statement|earnings[-_ ]?call)",re.I)
HOMEPAGE_TERMS=re.compile(r"(^|/)(home|index|personal-banking|netbanking|about-us)?/?$",re.I)

def score_source(url: str, title: str = "", snippet: str = "", company: str = "") -> dict:
    parsed=urlparse(url); path=(parsed.path or "/").lower(); text=f"{title} {snippet} {path}"
    score=0.0; reasons=[]
    if company:
        company_name=re.sub(r"\s*\((?:NSE|BSE)\s*:\s*[A-Z0-9]+\)\s*", " ", company, flags=re.I).strip()
        norm=lambda x: re.sub(r"[^a-z0-9]", "", str(x).lower())
        if norm(company_name) in norm(text) or all(tok in norm(text) for tok in re.findall(r"[A-Za-z0-9]+", company_name) if len(tok)>2):
            score+=2; reasons.append("company_identity")
    terms=len(FINANCIAL_TERMS.findall(text)); score+=min(terms*1.2,6); reasons.extend(["financial_terms"] if terms else [])
    if DOC_TERMS.search(text): score+=4; reasons.append("financial_document_pattern")
    if parsed.path.lower().endswith((".pdf",".xlsx",".csv")): score+=2; reasons.append("document_file")
    domain=(parsed.hostname or "").lower()
    if any(x in domain for x in ("nseindia.com","bseindia.com","rbi.org.in")): score+=1.5; reasons.append("regulatory_or_exchange_domain")
    if any(x in domain for x in ("hdfcbank.com","hdfc.bank.in")): score+=1; reasons.append("company_domain")
    is_home=(path in {"/","/index.html","/home","/personal-banking","/netbanking"} or (HOMEPAGE_TERMS.search(path) is not None and not DOC_TERMS.search(text)))
    if is_home: score-=7; reasons.append("generic_homepage_demoted")
    if not title.strip() and not snippet.strip(): score-=2; reasons.append("no_search_metadata")
    return {"score":round(score,2),"reasons":reasons,"is_homepage":is_home,"relevant":score>=3 and not is_home,
            "source_type":classify_source(url,title)}

def classify_source(url: str,title: str="") -> str:
    parsed=urlparse(url); host=(parsed.hostname or "").lower(); text=f"{url} {title}".lower()
    if ".pdf" in parsed.path.lower(): return "pdf_report"
    if "annual report" in text: return "annual_report"
    if "result" in text or "quarterly" in text: return "financial_results"
    if "nseindia.com" in host or "bseindia.com" in host: return "exchange_filing"
    if "rbi.org.in" in host: return "regulatory"
    if "investor" in text or "presentation" in text: return "investor_material"
    return "web"

def independence_group(url: str, title: str = "") -> str:
    host=(urlparse(url).hostname or "unknown").lower()
    if host.startswith("www."): host=host[4:]
    return host
