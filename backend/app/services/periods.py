"""Explicit reporting-period parsing for Indian fiscal-year financial disclosures."""
from __future__ import annotations
import re
from datetime import date
from calendar import monthrange

_MONTHS = {name.lower(): i for i, name in enumerate(("January","February","March","April","May","June","July","August","September","October","November","December"),1)}

def _fy_year(token: str) -> int:
    year = int(token)
    return 2000 + year if year < 100 else year

def _fy_bounds(year_token: str) -> tuple[date,date]:
    end_year = _fy_year(year_token)
    return date(end_year-1,4,1), date(end_year,3,31)

def parse_period(label: str | None, *, caption: str = "") -> dict | None:
    if not label: return None
    raw = " ".join(str(label).strip().split())
    text = raw.lower().replace("–","-").replace("—","-")
    m = re.search(r"\b(?:([1-4])\s*Q|Q\s*([1-4]))\s*(?:[- ]?\s*FY\s*[' ]?\s*(20\d{2}|\d{2})|\s+(20\d{2}))\b", text, re.I)
    if m:
        q = int(m.group(1) or m.group(2)); fy = m.group(3) or m.group(4)
        if fy:
            end_year = _fy_year(fy); start_month = 4 + (q-1)*3
            start_year = end_year-1 if start_month <= 12 else end_year
            start_month = ((start_month-1)%12)+1
            end_month = start_month+2; end_year_q = start_year + (end_month-1)//12; end_month=((end_month-1)%12)+1
            start=date(start_year,start_month,1); end=date(end_year_q,end_month,monthrange(end_year_q,end_month)[1])
            return _period_result(raw,"quarter",start,end,f"FY{end_year%100:02d}Q{q}",end_year,q)
    m = re.search(r"\b([1-4])\s*Q\s*FY\s*[' ]?(20\d{2}|\d{2})\b|\bQ([1-4])\s*[- ]?FY\s*[' ]?(20\d{2}|\d{2})\b", text, re.I)
    if m:
        q=int(m.group(1) or m.group(3)); fy=m.group(2) or m.group(4) or m.group(5)
        end_year=_fy_year(fy); start_month=4+(q-1)*3; sy=end_year-1 if start_month<=12 else end_year; sm=((start_month-1)%12)+1
        em=sm+2; ey=sy+(em-1)//12; em=((em-1)%12)+1
        return _period_result(raw,"quarter",date(sy,sm,1),date(ey,em,monthrange(ey,em)[1]),f"FY{end_year%100:02d}Q{q}",end_year,q)
    m = re.search(r"\bH([12])\s*FY\s*[' ]?(20\d{2}|\d{2})\b", text, re.I)
    if m:
        h=int(m.group(1)); ey=_fy_year(m.group(2)); start=date(ey-1,4,1) if h==1 else date(ey-1,10,1); end=date(ey-1,9,30) if h==1 else date(ey,3,31)
        return _period_result(raw,"half_year",start,end,f"FY{ey%100:02d}H{h}",ey,None)
    m = re.search(r"\b9M\s*FY\s*[' ]?(20\d{2}|\d{2})\b", text, re.I)
    if m:
        ey=_fy_year(m.group(1)); return _period_result(raw,"nine_months",date(ey-1,4,1),date(ey-1,12,31),f"FY{ey%100:02d}9M",ey,None)
    m = re.search(r"\bFY\s*[' ]?(20\d{2})\s*[-/]\s*(20\d{2}|\d{2})\b", text, re.I)
    if m:
        ey=_fy_year(m.group(2)); return _period_result(raw,"annual",date(ey-1,4,1),date(ey,3,31),f"FY{ey%100:02d}",ey,None)
    m = re.search(r"\bFY\s*[' ]?(20\d{2}|\d{2})\b", text, re.I)
    if m:
        ey=_fy_year(m.group(1)); return _period_result(raw,"annual",date(ey-1,4,1),date(ey,3,31),f"FY{ey%100:02d}",ey,None)
    m = re.search(r"(?:quarter ended|quarter ending)\s+(?:on\s+)?([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d{2})", text, re.I)
    if m:
        month=_MONTHS.get(m.group(1).lower()); day=int(m.group(2)); year=int(m.group(3))
        if month:
            end=date(year,month,min(day,monthrange(year,month)[1])); start=date(year,month,1)
            return _period_result(raw,"quarter",start,end,f"{year}Q{(month-1)//3+1}",year,(month-1)//3+1)
    m = re.search(r"(?:year ended|year ending)\s+(?:on\s+)?(?:31\s+March|March\s+31),?\s+(20\d{2})", text, re.I)
    if m:
        ey=int(m.group(1)); return _period_result(raw,"annual",date(ey-1,4,1),date(ey,3,31),f"FY{ey%100:02d}",ey,None)
    m = re.search(r"(?:as at|as of|at)\s+(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", text, re.I)
    if m and m.group(2).lower() in _MONTHS:
        y=int(m.group(3)); mo=_MONTHS[m.group(2).lower()]; d=min(int(m.group(1)),monthrange(y,mo)[1]); end=date(y,mo,d)
        return _period_result(raw,"point_in_time",end,end,end.isoformat(),y,None)
    m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ](20\d{2}|\d{2})\b", text, re.I)
    if m and re.search(r"\bquarter\b", caption, re.I):
        mon=_MONTHS[next(x for x in _MONTHS if x.startswith(m.group(1).lower()))]; y=_fy_year(m.group(2)); end=date(y,mon,monthrange(y,mon)[1]); return _period_result(raw,"quarter",date(y,mon,1),end,f"{y}Q{(mon-1)//3+1}",y,(mon-1)//3+1)
    return None

def _period_result(label, period_type, start, end, period_id, fiscal_year, quarter):
    return {"label":label,"period_type":period_type,"start":start.isoformat(),"end":end.isoformat(),"period_id":period_id,"fiscal_year":fiscal_year,"quarter":quarter}

def prior_year_id(period: dict | None) -> str | None:
    if not period: return None
    if period.get("period_type")=="quarter" and period.get("quarter"):
        return f"FY{(period['fiscal_year']-1)%100:02d}Q{period['quarter']}"
    if period.get("period_type")=="annual": return f"FY{(period['fiscal_year']-1)%100:02d}"
    return None

def prior_quarter_id(period: dict | None) -> str | None:
    if not period or period.get("period_type")!="quarter" or not period.get("quarter"): return None
    q=period["quarter"]; fy=period["fiscal_year"]
    return f"FY{(fy-1)%100:02d}Q4" if q==1 else f"FY{fy%100:02d}Q{q-1}"
