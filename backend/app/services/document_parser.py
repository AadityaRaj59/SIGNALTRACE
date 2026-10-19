"""Bounded HTML/PDF/text parser preserving table relationships and page provenance."""
from __future__ import annotations
from io import BytesIO
from bs4 import BeautifulSoup
from pypdf import PdfReader
import json, re

_DATE_RE = re.compile(r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2})\b", re.I)
_BOILERPLATE = ("nav", "navigation", "footer", "header", "cookie", "consent", "breadcrumb", "menu", "sidebar")

def _tables_as_text(soup: BeautifulSoup) -> list[str]:
    rendered=[]
    for ti, table in enumerate(soup.find_all("table"), 1):
        caption=table.find("caption")
        caption_text=caption.get_text(" ",strip=True) if caption else ""
        rows=[]
        for tr in table.find_all("tr"):
            cells=tr.find_all(["th","td"], recursive=False)
            vals=[" ".join(c.get_text(" ",strip=True).split()) for c in cells]
            if vals and any(vals): rows.append(" | ".join(vals))
        if rows:
            prefix=f"[Table {ti}]" + (f" {caption_text}" if caption_text else "")
            rendered.append(prefix + "\n" + "\n".join(rows))
    return rendered

def _extract_published(soup: BeautifulSoup, text: str) -> str | None:
    for key in ("article:published_time", "datePublished", "date", "pubdate", "publish-date", "DC.date", "dcterms.date", "dateModified"):
        tag=(soup.find("meta",attrs={"property":key}) or soup.find("meta",attrs={"name":key}) or soup.find("meta",attrs={"itemprop":key}))
        if tag and tag.get("content"): return tag.get("content").strip()
    for script in soup.find_all("script",attrs={"type":"application/ld+json"}):
        try:
            data=json.loads(script.string or script.get_text()); stack=data if isinstance(data,list) else [data]
            while stack:
                item=stack.pop()
                if isinstance(item,dict):
                    if item.get("datePublished"): return str(item["datePublished"])
                    stack.extend(v for v in item.values() if isinstance(v,(dict,list)))
                elif isinstance(item,list): stack.extend(item)
        except (ValueError,TypeError): continue
    m=_DATE_RE.search(text)
    return m.group(0) if m else None

def parse_document(content: str | bytes, content_type: str, max_chars: int = 400_000, *, max_pdf_pages: int = 500) -> dict:
    ctype=(content_type or "").split(";")[0].strip().lower()
    if ctype=="application/pdf":
        try:
            raw=content if isinstance(content,bytes) else content.encode()
            reader=PdfReader(BytesIO(raw)); total_pages=len(reader.pages); pages=[]; scored=[]
            for index,page in enumerate(reader.pages[:max(1,max_pdf_pages)]):
                txt=page.extract_text() or ""
                if txt.strip():
                    density=len(re.findall(r"\b(?:advances|deposits|profit|income|NPA|NIM|crore|capital|assets|liabilities|quarter|year ended)\b",txt,re.I))
                    scored.append((density+len(txt)/2000,index,txt))
            is_scanned=not scored or sum(len(x[2].strip()) for x in scored)<100
            # If the document exceeds the character budget, keep financially dense pages, then restore page order.
            chosen=sorted(scored,key=lambda x:x[0],reverse=True)
            kept=[]; used=0
            for score,index,txt in chosen:
                block=f"[Page {index+1}]\n{txt.strip()}"
                if used+len(block)>max_chars: continue
                kept.append((index,block)); used+=len(block)+2
            text="\n\n".join(block for _,block in sorted(kept))
            extracted_tables=[]
            # Optional table recovery for PDFs; pypdf remains the required baseline parser.
            try:
                import pdfplumber
                with pdfplumber.open(BytesIO(raw)) as pdf:
                    for page_no,page in enumerate(pdf.pages[:max(1,max_pdf_pages)],1):
                        for table_no,table in enumerate(page.extract_tables() or [],1):
                            rows=[]
                            for row in table:
                                vals=[" ".join(str(c or "").split()) for c in row]
                                if any(vals): rows.append(" | ".join(vals))
                            if rows: extracted_tables.append(f"[Page {page_no}] [Table {table_no}]\n"+"\n".join(rows))
            except ImportError:
                pass
            if extracted_tables:
                table_text="\n\n".join(extracted_tables)
                if len(text)+len(table_text)+2 <= max_chars: text=(text+"\n\n"+table_text).strip()
            metadata=reader.metadata; published=None
            if metadata:
                published=metadata.get("/CreationDate") or metadata.get("/ModDate")
                if published: published=str(published)
            if not published:
                m=_DATE_RE.search(text); published=m.group(0) if m else None
            err=None if text.strip() and len(text.strip())>=50 else "PDF contained no meaningful extractable text. OCR may be required." if is_scanned else "PDF contained insufficient readable text."
            return {"title":"","description":"","text":text,"publication_date":published,"parse_error":err,
                    "parse_status":"parsed" if not err else "no_readable_text","is_scanned":is_scanned,"ocr_required":is_scanned,
                    "pages_total":total_pages,"pages_parsed":len(kept),"tables":extracted_tables}
        except Exception as exc:
            return {"title":"","description":"","text":"","publication_date":None,"parse_error":f"PDF parsing failed: {type(exc).__name__}: {str(exc)[:180]}","parse_status":"parser_failure","is_scanned":None,"ocr_required":False,"pages_total":0,"pages_parsed":0,"tables":[]}
    if isinstance(content,bytes): content=content.decode("utf-8",errors="replace")
    if ctype in {"text/html","application/xhtml+xml"}:
        soup=BeautifulSoup(content,"html.parser")
        tables=_tables_as_text(soup)
        title=soup.title.get_text(" ",strip=True) if soup.title else ""
        description_tag=soup.find("meta",attrs={"name":"description"}); description=(description_tag.get("content") or "").strip() if description_tag else ""
        published=_extract_published(soup," ".join(soup.get_text(" ",strip=True).split()))
        for node in soup(["script","style","noscript","svg","iframe","form"]): node.decompose()
        for node in list(soup.find_all(True)):
            attrs=" ".join([str(node.get("id") or "")," ".join(node.get("class",[])),str(node.get("role") or "")]).lower()
            if any(x in attrs for x in _BOILERPLATE) and node.name not in {"main","article"}: node.decompose()
        headings=[f"[Heading] {h.get_text(' ',strip=True)}" for h in soup.find_all(["h1","h2","h3"]) if h.get_text(" ",strip=True)]
        paragraphs=[" ".join(x.get_text(" ",strip=True).split()) for x in soup.find_all(["p","li","article","main"]) if x.get_text(" ",strip=True)]
        # Keep normal main text and pipe-delimited table rows. This makes each reconstructed table row a verifiable substring.
        regular="\n".join(dict.fromkeys(headings+paragraphs))
        text=(regular+"\n\n"+"\n\n".join(tables)).strip()
        if not text: text=" ".join(soup.get_text(" ",strip=True).split())
        text=text[:max_chars]
        return {"title":title[:500],"description":description[:1000],"text":text,"publication_date":published,
                "parse_error":None if len(text.strip())>=20 else "No readable HTML text extracted.","parse_status":"parsed" if len(text.strip())>=20 else "no_readable_text","tables":tables,"is_scanned":False,"ocr_required":False}
    if ctype=="text/plain":
        text=content[:max_chars]
        return {"title":"","description":"","text":text,"publication_date":(m.group(0) if (m:=_DATE_RE.search(text)) else None),
                "parse_error":None if text.strip() else "Text document was empty.","parse_status":"parsed" if text.strip() else "no_readable_text","tables":[]}
    return {"title":"","description":"","text":"","publication_date":None,"parse_error":f"Unsupported content type: {ctype or 'unknown'}","parse_status":"unsupported_type","tables":[]}
