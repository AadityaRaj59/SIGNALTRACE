"""Build focused, expandable public-financial-source queries."""
from __future__ import annotations

def plan_queries(company: str, focus: str | None = None, ticker: str | None = None) -> list[dict]:
    identity=f'"{company}"' + (f' "{ticker}"' if ticker else "")
    angles=[
        ("quarterly_results",f'{identity} quarterly results investor relations PDF advances deposits NPA NIM',None),
        ("annual_report",f'{identity} annual report audited financial statements PDF',None),
        ("advances_deposits",f'{identity} total advances total deposits quarterly results FY25 FY26',None),
        ("asset_quality",f'{identity} gross NPA ratio net NPA ratio provisions slippages quarterly results',None),
        ("profit_quality",f'{identity} net interest income NII net interest margin PAT operating profit provisions results',None),
        ("funding_mix",f'{identity} CASA ratio deposits borrowings credit deposit ratio capital adequacy results',None),
        ("exchange_filings",f'{identity} stock exchange filing financial results investor presentation',["nseindia.com","bseindia.com"]),
        ("regulatory",f'{identity} RBI financial stability report bank deposits advances asset quality statistics',["rbi.org.in"]),
        ("contradiction",f'{identity} asset quality deterioration deposit growth margin compression provisions risk',None),
        ("management_narrative",f'{identity} earnings call transcript management commentary deposits loan growth NPA',None),
    ]
    if focus: angles.insert(0,("focus",f'{identity} {focus} quarterly results annual report financial filing data',None))
    return [{"angle":a,"query":q,"include_domains":domains} for a,q,domains in angles]
