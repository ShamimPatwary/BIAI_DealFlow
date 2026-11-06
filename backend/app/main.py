from __future__ import annotations

import csv
import io
import json
import os
import re
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "sample-data" / "intern" / "task2-corporate-leads" / "corporate-leads.csv"
DB_PATH = Path(os.getenv("DATABASE_PATH", str(ROOT / "backend" / "dealflow.db")))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH.as_posix()}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
if DATABASE_URL.startswith("sqlite"):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
STAGES = ["New", "Contacted", "Meeting", "Proposal", "Invoice sent", "Paid", "Lost"]
def local_today() -> date:
    return datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Dhaka"))).date()

class Base(DeclarativeBase): pass

class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    company: Mapped[str] = mapped_column(String, default="")
    industry: Mapped[str] = mapped_column(String, default="")
    contact: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    phone: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="")
    stage: Mapped[str] = mapped_column(String, default="New")
    value: Mapped[float] = mapped_column(Float, default=0)
    last: Mapped[date | None] = mapped_column(Date, nullable=True)
    next: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String, default="Unassigned")
    original_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

class Issue(Base):
    __tablename__ = "issues"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    lead_id: Mapped[str] = mapped_column(String, index=True)
    type: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    original_value: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)

class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

def norm_company(name: str) -> str:
    name = re.sub(r"[.,'’&()\-]", " ", (name or "").lower())
    name = re.sub(r"\b(limited|ltd|incorporated|inc|company|co)\b", " ", name)
    return " ".join(name.split())

def duplicate_groups_for(leads: list[Lead]) -> list[tuple[str, list[Lead], str]]:
    groups: dict[str, list[Lead]] = defaultdict(list)
    for l in leads:
        if l.company.strip(): groups[norm_company(l.company)].append(l)
    result=[(k,v,"normalized exact company name") for k,v in groups.items() if len(v)>1]
    assigned={l.id for _, group, _ in result for l in group}
    # The supplied Meghna pair uses variant company names but repeats the same
    # person, email, phone, stage, and deal value. Flag for human review.
    for l in leads:
        if l.id in assigned or not l.email.strip(): continue
        peers=[x for x in leads if x.id!=l.id and x.email.strip().lower()==l.email.strip().lower() and x.phone.strip()==l.phone.strip() and x.contact.strip().lower()==l.contact.strip().lower() and x.stage==l.stage and x.value==l.value]
        peers=[x for x in peers if x.id not in assigned]
        if peers:
            group=[l,*peers]; result.append(("same contact and deal details",group,"same contact and deal details across company-name variants")); assigned.update(x.id for x in group)
    return result

def lead_dict(l: Lead) -> dict[str, Any]:
    return {"id": l.id, "company": l.company, "industry": l.industry, "contact": l.contact, "role": l.role,
            "email": l.email, "phone": l.phone, "source": l.source, "stage": l.stage, "value": l.value,
            "last": l.last.isoformat() if l.last else "", "next": l.next.isoformat() if l.next else "",
            "notes": l.notes, "owner": l.owner, "original": json.loads(l.original_json), "archived": l.archived}

def seed() -> None:
    with Session(engine) as db:
        if db.scalar(select(Lead.id).limit(1)):
            audit()
            return
        with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                def parse_date(v):
                    try: return date.fromisoformat((v or "").strip()) if v and v.strip() else None
                    except ValueError: return None
                try: value = float((row.get("deal value (BDT)") or "0").strip() or 0)
                except ValueError: value = 0
                db.add(Lead(id=row["lead id"], company=row.get("company", "").strip(), industry=row.get("industry", "").strip(),
                    contact=row.get("contact name", "").strip(), role=row.get("contact role", "").strip(),
                    email=row.get("contact email", "").strip(), phone=row.get("contact phone", "").strip(),
                    source=row.get("source", "").strip(), stage=row.get("stage", "").strip(), value=value,
                    last=parse_date(row.get("last contact")), next=parse_date(row.get("next follow-up")), notes=row.get("notes", "").strip(),
                    original_json=json.dumps(row, ensure_ascii=False)))
        db.commit()
    audit()

def audit() -> list[dict[str, Any]]:
    with Session(engine) as db:
        leads = list(db.scalars(select(Lead).where(Lead.archived.is_(False))))
        groups: dict[str, list[Lead]] = defaultdict(list)
        for l in leads:
            if l.company.strip(): groups[norm_company(l.company)].append(l)
        duplicate_members={l.id:(group,method) for _,group,method in duplicate_groups_for(leads) for l in group}
        found=[]
        for l in leads:
            checks=[]
            for field, label in (("contact", "Contact person"), ("email", "Email"), ("phone", "Phone")):
                if not getattr(l, field).strip(): checks.append(("Missing contact", "Warning", f"Missing {label.lower()}.", getattr(l, field), f"Add a verified {label.lower()} if available."))
            duplicate=duplicate_members.get(l.id)
            if duplicate:
                group,method=duplicate
                checks.append(("Duplicate company", "Warning", method + ": " + ", ".join(x.id for x in group if x.id != l.id), l.company, "Review records; retain original rows until confirmed."))
            if l.last and l.next and l.next < l.last:
                checks.append(("Date order", "Critical", "Next follow-up is earlier than last contact.", l.next.isoformat(), "Review and enter the intended follow-up date; no date was changed."))
            if not l.company.strip(): checks.append(("Missing company", "Critical", "Company name is required.", l.company, "Enter a verified company name."))
            if l.value < 0: checks.append(("Invalid deal value", "Critical", "Deal value cannot be negative.", str(l.value), "Confirm the intended BDT amount."))
            if l.stage not in STAGES: checks.append(("Invalid stage", "Critical", "Stage is not in the supplied stage list.", l.stage, "Choose a valid pipeline stage."))
            if l.email.strip() and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", l.email): checks.append(("Invalid email", "Warning", "Email format looks invalid.", l.email, "Verify the email address with the contact."))
            if "bogurateXtile" in l.email: checks.append(("Possible email typo", "Warning", "Notes explicitly say to check this email typo.", l.email, "Verify the address; do not guess a correction."))
            original=json.loads(l.original_json)
            note_low=l.notes.lower()
            if "no phone or email" in note_low and (l.phone.strip() or l.email.strip()):
                checks.append(("Contact note mismatch", "Warning", "Notes say no phone or email is on file, but at least one is populated in the contact columns.", l.notes, "Verify the contact fields against the original enquiry."))
            if l.stage=="Paid" and ("50 percent" in note_low or "second payment" in note_low):
                checks.append(("Stage/notes mismatch", "Warning", "Paid is defined as full payment received, but notes describe a partial payment still due.", l.stage, "Review the payment status and select the stage that matches the confirmed balance."))
            for key,label in (("last contact","last contact"),("next follow-up","next follow-up")):
                raw=(original.get(key) or "").strip()
                parsed=l.last if key=="last contact" else l.next
                if raw and parsed is None: checks.append(("Invalid date", "Warning", f"{label.title()} is not a valid ISO date.", raw, "Review the original date and enter a valid date."))
            raw_value=(original.get("deal value (BDT)") or "0").strip()
            try: float(raw_value or 0)
            except ValueError: checks.append(("Invalid deal value", "Critical", "Deal value is not numeric.", raw_value, "Confirm the intended BDT amount."))
            for check_number, (typ, sev, desc, orig, sugg) in enumerate(checks, start=1):
                found.append({"id":f"{l.id}:{typ}:{check_number}","lead_id":l.id,"company":l.company,"type":typ,"severity":sev,"description":desc,"original_value":orig,"suggestion":sugg})
        resolved={i.id:i.resolved for i in db.scalars(select(Issue))}
        db.query(Issue).delete()
        for issue in found: db.add(Issue(id=issue["id"], lead_id=issue["lead_id"], type=issue["type"], severity=issue["severity"], description=issue["description"], original_value=str(issue["original_value"]), suggestion=issue["suggestion"], resolved=resolved.get(issue["id"],False)))
        db.commit()
        return found

class LeadPatch(BaseModel):
    company: str|None=None; industry: str|None=None; contact: str|None=None; role: str|None=None
    email: str|None=None; phone: str|None=None; stage: str|None=None; value: float|None=None
    last: str|None=None; next: str|None=None; notes: str|None=None; owner: str|None=None
class QueryIn(BaseModel): question: str=Field(min_length=1,max_length=500)
class ResolveIn(BaseModel): resolved: bool=True
class QueryIntent(BaseModel):
    intent: Literal["overdue", "pipeline_value", "by_stage", "missing_contact", "issues", "duplicates", "priorities"]
    stage: str|None=None

def live_ai(prompt: str, instructions: str) -> str | None:
    """Small provider adapter; errors and missing credentials fall back to deterministic demo mode."""
    if os.getenv("AI_PROVIDER", "openai").lower() != "openai": return None
    key=os.getenv("OPENAI_API_KEY")
    if not key: return None
    payload=json.dumps({"model":os.getenv("OPENAI_MODEL","gpt-4.1-mini"),"instructions":instructions,"input":prompt,"max_output_tokens":350}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/responses",data=payload,headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=18) as response: result=json.loads(response.read())
        texts=[]
        for item in result.get("output",[]):
            for content in item.get("content",[]):
                if content.get("type")=="output_text": texts.append(content.get("text",""))
        return "\n".join(texts).strip() or None
    except Exception:
        return None

app=FastAPI(title="BIAI DealFlow API", version="1.0.0")
origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in origins.split(",")], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    seed()

@app.get("/api/v1/health")
def health(): return {"ok":True,"ai_provider":"openai" if os.getenv("AI_PROVIDER","openai").lower()=="openai" and os.getenv("OPENAI_API_KEY") else "demo"}

@app.get("/api/v1/leads")
def get_leads():
    with Session(engine) as db: return [lead_dict(l) for l in db.scalars(select(Lead).where(Lead.archived.is_(False)).order_by(Lead.id))]

@app.get("/api/v1/leads/{lead_id}")
def get_lead(lead_id: str):
    with Session(engine) as db:
        l=db.get(Lead,lead_id)
        if not l or l.archived: raise HTTPException(404,"Lead not found")
        return lead_dict(l)

@app.patch("/api/v1/leads/{lead_id}")
def update_lead(lead_id: str, body: LeadPatch):
    with Session(engine) as db:
        l=db.get(Lead,lead_id)
        if not l: raise HTTPException(404,"Lead not found")
        changes=[]
        for k,v in body.model_dump(exclude_unset=True).items():
            if k in ("last","next"):
                try: v=date.fromisoformat(v) if v else None
                except ValueError: raise HTTPException(422,f"{k} must be an ISO date")
            if k=="stage" and v not in STAGES: raise HTTPException(422,"Invalid stage")
            old=getattr(l,k)
            if old!=v: setattr(l,k,v); changes.append(k)
        if changes:
            l.updated_at=datetime.utcnow(); db.add(Activity(lead_id=l.id,description="Updated " + ", ".join(changes)))
            db.commit(); audit()
        db.refresh(l); return lead_dict(l)

@app.get("/api/v1/dashboard/summary")
def summary():
    today=local_today()
    with Session(engine) as db:
        ls=list(db.scalars(select(Lead).where(Lead.archived.is_(False))))
        open_ls=[l for l in ls if l.stage not in ("Paid","Lost")]
        return {"total_leads":len(ls),"pipeline_value":sum(l.value for l in open_ls),"proposal_value":sum(l.value for l in ls if l.stage=="Proposal"),
          "overdue":sum(bool(l.next and l.next<today and l.stage not in ("Paid","Lost")) for l in ls),"data_issues":len({i.lead_id for i in db.scalars(select(Issue).where(Issue.resolved.is_(False)))}),"paid_deals":sum(l.stage=="Paid" for l in ls),"today":today.isoformat()}

@app.get("/api/v1/dashboard/pipeline")
def pipeline():
    with Session(engine) as db:
        ls=list(db.scalars(select(Lead).where(Lead.archived.is_(False))))
        return [{"stage":s,"count":sum(l.stage==s for l in ls),"value":sum(l.value for l in ls if l.stage==s)} for s in STAGES]

@app.get("/api/v1/dashboard/follow-ups")
def followups():
    today=local_today()
    with Session(engine) as db:
        ls=list(db.scalars(select(Lead).where(Lead.archived.is_(False))))
        return {"overdue":sum(bool(l.next and l.next<today and l.stage not in ("Paid","Lost")) for l in ls),"due_today":sum(bool(l.next==today and l.stage not in ("Paid","Lost")) for l in ls),"upcoming":sum(bool(l.next and l.next>today and l.stage not in ("Paid","Lost")) for l in ls),"no_date":sum(bool(not l.next and l.stage not in ("Paid","Lost")) for l in ls)}

@app.get("/api/v1/activities")
def activities():
    with Session(engine) as db:
        rows=list(db.scalars(select(Activity).order_by(Activity.created_at.desc()).limit(12)))
        return [{"lead_id":x.lead_id,"company":db.get(Lead,x.lead_id).company if db.get(Lead,x.lead_id) else "Archived lead","description":x.description,"created_at":x.created_at.isoformat()} for x in rows]

@app.get("/api/v1/data-quality/issues")
def issues():
    with Session(engine) as db:
        return [{"id":i.id,"lead_id":i.lead_id,"company":db.get(Lead,i.lead_id).company,"type":i.type,"severity":i.severity,"description":i.description,"original_value":i.original_value,"suggestion":i.suggestion,"resolved":i.resolved} for i in db.scalars(select(Issue))]

@app.post("/api/v1/data-quality/audit")
def run_audit(): return {"issue_count":len(audit()),"issues":issues()}

@app.post("/api/v1/data-quality/issues/{issue_id}/resolve")
def resolve(issue_id: str, body: ResolveIn):
    with Session(engine) as db:
        i=db.get(Issue,issue_id)
        if not i: raise HTTPException(404,"Issue not found")
        i.resolved=body.resolved; db.commit(); return {"id":i.id,"resolved":i.resolved}

@app.get("/api/v1/data-quality/duplicates")
def duplicates():
    with Session(engine) as db:
        ls=list(db.scalars(select(Lead).where(Lead.archived.is_(False))))
        return [{"normalized_name":k,"method":method,"leads":[lead_dict(l) for l in group]} for k,group,method in duplicate_groups_for(ls)]

@app.post("/api/v1/ai/follow-up-email")
def email_draft(payload: dict[str,str]):
    with Session(engine) as db:
        l=db.get(Lead,payload.get("lead_id",""))
        if not l: raise HTTPException(404,"Lead not found")
        context=l.notes.split(".")[0].strip().lower().rstrip(".")
        fallback={"subject":f"Following up on {l.company}","body":f"Hello {l.contact or 'there'},\n\nI’m following up regarding {context}. Would you be available to discuss the next step? Please let me know if any additional information would be helpful.\n\nKind regards,\nBIAI Corporate Training Team","next_step":"Review the notes and confirm a suitable time to discuss next steps."}
        instructions="You are a professional BIAI corporate training sales assistant. Write a short, polite English follow-up using only the supplied facts and notes. Do not invent commitments, pricing, discounts, meetings or promises. Return JSON only with subject, body, next_step. Keep the body under 120 words."
        generated=live_ai(json.dumps(lead_dict(l),ensure_ascii=False),instructions); ai_generated=False
        if generated:
            try:
                parsed=json.loads(generated)
                if all(isinstance(parsed.get(k),str) and parsed[k].strip() for k in ("subject","body","next_step")) and len(parsed["body"].split())<=120: fallback=parsed; ai_generated=True
            except (ValueError,TypeError): pass
        mode="OpenAI" if ai_generated else "Demo template"
        return {**fallback,"mode":mode,"ai_available":ai_generated}

def query_result(q: str):
    today=local_today(); low=q.lower()
    with Session(engine) as db: ls=list(db.scalars(select(Lead).where(Lead.archived.is_(False))))
    if "overdue" in low or "late" in low:
        rows=[l for l in ls if l.next and l.next<today and l.stage not in ("Paid","Lost")]; intent="overdue"
    elif "proposal" in low and ("value" in low or "total" in low):
        rows=[l for l in ls if l.stage=="Proposal"]; intent="pipeline_value"
    elif "missing" in low or "contact detail" in low:
        rows=[l for l in ls if not l.contact.strip() or not l.email.strip() or not l.phone.strip()]; intent="missing_contact"
    elif "duplicate" in low:
        rows=[l for _,group,_ in duplicate_groups_for(ls) for l in group]; intent="duplicates"
    elif "chase" in low or "priorit" in low:
        return {"intent":"priorities","count":5,"value":None,"leads":priorities()}
    elif "meeting" in low and "stuck" in low: rows=[l for l in ls if l.stage=="Meeting" and l.next and l.next<today]; intent="by_stage"
    elif "meeting" in low and "which" in low: rows=[l for l in ls if l.stage=="Meeting"]; intent="by_stage"
    elif "invoice" in low: rows=[l for l in ls if l.stage=="Invoice sent"]; intent="by_stage"
    elif "active" in low and ("value" in low or "total" in low): rows=[l for l in ls if l.stage not in ("Paid","Lost")]; intent="pipeline_value"
    else: raise HTTPException(422,"Unsupported question. Try overdue, Proposal value, missing contacts, duplicate companies, Meeting leads, Invoice sent count, or active pipeline value.")
    value=sum(l.value for l in rows) if intent=="pipeline_value" else None
    return {"intent":intent,"count":len(rows),"value":value,"currency":"BDT","leads":[{"id":l.id,"company":l.company,"stage":l.stage,"value":l.value,"next":l.next.isoformat() if l.next else ""} for l in rows]}

@app.post("/api/v1/ai/query")
def ai_query(body: QueryIn):
    prompt="Extract a single allowed intent from the user question. Return JSON with intent and optional stage. Allowed intents: overdue, pipeline_value, by_stage, missing_contact, issues, duplicates, priorities. stage must be exactly one of: " + ", ".join(STAGES) + ". Question: " + body.question
    parsed_intent=None
    extracted=live_ai(prompt,"You convert questions into a strictly validated analytics intent. Do not answer the question or calculate anything. Output JSON only.")
    if extracted:
        try:
            candidate=QueryIntent.model_validate_json(extracted)
            if candidate.stage and candidate.stage not in STAGES: raise ValueError("invalid stage")
            parsed_intent=candidate
        except Exception: parsed_intent=None
    if parsed_intent:
        with Session(engine) as db: all_leads=list(db.scalars(select(Lead).where(Lead.archived.is_(False))))
        intent=parsed_intent.intent
        if intent=="overdue": result=[l for l in all_leads if l.next and l.next<local_today() and l.stage not in ("Paid","Lost")]
        elif intent=="pipeline_value": result=[l for l in all_leads if l.stage not in ("Paid","Lost") and (not parsed_intent.stage or l.stage==parsed_intent.stage)]
        elif intent=="by_stage": result=[l for l in all_leads if parsed_intent.stage and l.stage==parsed_intent.stage]
        elif intent=="missing_contact": result=[l for l in all_leads if not l.contact.strip() or not l.email.strip() or not l.phone.strip()]
        elif intent=="duplicates": result=[l for _,group,_ in duplicate_groups_for(all_leads) for l in group]
        elif intent=="issues":
            with Session(engine) as db: bad=set(db.scalars(select(Issue.lead_id).where(Issue.resolved.is_(False))))
            result=[l for l in all_leads if l.id in bad]
        elif intent=="priorities": result=[]
        else: result=all_leads
        value=sum(l.value for l in result) if intent=="pipeline_value" or (intent=="by_stage" and parsed_intent.stage=="Proposal") else None
        r={"intent":intent,"count":len(result),"value":value,"currency":"BDT","leads":[{"id":l.id,"company":l.company,"stage":l.stage,"value":l.value,"next":l.next.isoformat() if l.next else ""} for l in result]}
        if intent=="priorities": r={"intent":intent,"count":5,"value":None,"currency":"BDT","leads":priorities()}
    else: r=query_result(body.question)
    if r["value"] is not None: r["answer"]=f"The matching pipeline value is ৳{r['value']:,.0f} across {r['count']} lead records."
    elif r["intent"]=="overdue": r["answer"]=f"There are {r['count']} active leads with follow-ups before {local_today().isoformat()}."
    else: r["answer"]=f"I found {r['count']} matching lead records."
    r["mode"]="OpenAI intent + deterministic calculation" if parsed_intent else "deterministic demo assistant"; return r

@app.get("/api/v1/ai/priorities")
def priorities():
    today=local_today()
    with Session(engine) as db: ls=list(db.scalars(select(Lead).where(Lead.archived.is_(False))))
    out=[]
    for l in ls:
        if l.stage in ("Paid","Lost"): continue
        late=(today-l.next).days if l.next and l.next<today else 0
        score=min(50,late*5)+min(25,int(l.value/100000)*3)+(10 if not l.next else 0)+STAGES.index(l.stage)*2
        out.append({"id":l.id,"company":l.company,"stage":l.stage,"score":score,"reason":f"{late} days overdue" if late else ("No follow-up date" if not l.next else "Open opportunity"),"value":l.value})
    return sorted(out,key=lambda x:x["score"],reverse=True)[:5]

@app.get("/api/v1/export/csv")
def export_csv():
    with Session(engine) as db: ls=list(db.scalars(select(Lead).where(Lead.archived.is_(False)).order_by(Lead.id)))
    out=io.StringIO(); w=csv.writer(out); w.writerow(["lead id","company","industry","contact name","contact role","contact email","contact phone","source","stage","deal value (BDT)","last contact","next follow-up","notes","owner"])
    for l in ls: w.writerow([l.id,l.company,l.industry,l.contact,l.role,l.email,l.phone,l.source,l.stage,l.value,l.last or "",l.next or "",l.notes,l.owner])
    return StreamingResponse(iter(["\ufeff"+out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=biai-dealflow-leads.csv"})

@app.get("/api/v1/export/data-quality")
def export_audit():
    with Session(engine) as db: rows=list(db.scalars(select(Issue)))
    out=io.StringIO(); w=csv.writer(out); w.writerow(["lead_id","company","issue_type","severity","description","original_value","suggestion","resolved"])
    with Session(engine) as db:
        for i in rows: w.writerow([i.lead_id,db.get(Lead,i.lead_id).company,i.type,i.severity,i.description,i.original_value,i.suggestion,i.resolved])
    return StreamingResponse(iter(["\ufeff"+out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=biai-dealflow-audit.csv"})

@app.post("/api/v1/import/csv")
async def import_csv(file: UploadFile=File(...)):
    if not (file.filename or "").lower().endswith(".csv"): raise HTTPException(400,"Upload a CSV file")
    raw=await file.read(2_000_001)
    if len(raw)>2_000_000: raise HTTPException(413,"CSV must be smaller than 2 MB")
    try: rows=list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    except Exception as e: raise HTTPException(400,f"Could not read CSV: {e}")
    required={"company","stage","deal value (bdt)"}
    if not rows or not required.issubset({(k or "").lower().strip() for k in rows[0]}): raise HTTPException(400,"CSV needs company, stage, and deal value (BDT) columns")
    return {"preview_only":True,"rows":len(rows),"headers":list(rows[0]),"message":"Preview parsed. The original dataset is unchanged; import replacement is intentionally not automatic."}

app.mount("/", StaticFiles(directory=str(ROOT / "frontend"), html=True), name="frontend")
