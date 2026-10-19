"""Conservative Decimal-based numeric and financial-unit parsing."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import re

SCALE = {
    "thousand":Decimal("1e3"), "thousands":Decimal("1e3"),
    "lakh":Decimal("1e5"), "lakhs":Decimal("1e5"),
    "crore":Decimal("1e7"), "crores":Decimal("1e7"), "cr":Decimal("1e7"),
    "lakh crore":Decimal("1e12"), "lakhs crore":Decimal("1e12"),
    "million":Decimal("1e6"), "mn":Decimal("1e6"), "millions":Decimal("1e6"),
    "billion":Decimal("1e9"), "bn":Decimal("1e9"), "billions":Decimal("1e9"),
    "trillion":Decimal("1e12"), "tn":Decimal("1e12"), "trillions":Decimal("1e12"),
}

def parse_number(value: str | int | float | Decimal) -> Decimal | None:
    if isinstance(value, Decimal): return value
    text=str(value).strip().replace("−","-").replace("–","-").replace(" ", " ").replace("&minus;","-")
    negative=text.startswith("(") and text.endswith(")")
    if negative: text=text[1:-1].strip()
    text=text.replace(",","").replace(" ","")
    text=re.sub(r"^(?:₹|Rs\.?|INR|\$|USD|EUR|€|£)","",text,flags=re.I)
    text=re.sub(r"(?:₹|Rs\.?|INR|\$|USD|EUR|€|£)$","",text,flags=re.I)
    if not re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)",text): return None
    try:
        n=Decimal(text)
        return -abs(n) if negative else n
    except InvalidOperation: return None

def detect_currency(text: str) -> str | None:
    s=str(text or "")
    if re.search(r"₹|\bINR\b|\bRs\.?\b|rupees?",s,re.I): return "INR"
    if re.search(r"\bUSD\b|\$",s,re.I): return "USD"
    if re.search(r"\bEUR\b|€",s,re.I): return "EUR"
    if re.search(r"\bGBP\b|£",s,re.I): return "GBP"
    return None

def normalize_amount(value: str, unit: str = "", context: str = "") -> dict | None:
    text=f"{value} {unit}".strip().lower()
    number=parse_number(value)
    if number is None: return None
    if re.search(r"\bbps?\b",text): return {"value":format(number,"f"),"unit":"bps","currency":None,"conversion_rule":"basis_points_no_scale"}
    if "%" in text or re.search(r"\bpercent(?:age)?\b|\bper cent\b",text): return {"value":format(number,"f"),"unit":"%","currency":None,"conversion_rule":"percentage_no_scale"}
    currency=detect_currency(text) or detect_currency(context)
    scale_name=next((k for k in sorted(SCALE,key=len,reverse=True) if re.search(rf"\b{re.escape(k)}\b",text)),None)
    if not scale_name: return {"value":None,"unit":unit or "unknown","currency":currency,"conversion_rule":"no_explicit_scale_not_normalized"}
    multiplier=SCALE[scale_name]
    # Indian lakh/crore terminology implies INR; western scale words do not imply currency.
    if scale_name in {"lakh","lakhs","crore","crores","cr","lakh crore","lakhs crore"} and currency is None: currency="INR"
    if currency is None and scale_name in {"million","mn","millions","billion","bn","billions","trillion","tn","trillions"}:
        return {"value":None,"unit":scale_name,"currency":None,"conversion_rule":"western_scale_without_currency_not_normalized"}
    return {"value":format(number*multiplier,"f"),"unit":f"{currency} base units" if currency else "scaled units","currency":currency,"conversion_rule":f"{scale_name} x {multiplier}"}
