"""
TaskMind AI — FastAPI Backend
Features:
  1. Gemini AI task extraction with priority + deadline
  2. OCR screenshot → task extraction via Gemini Vision
  3. Gmail OAuth 2.0 → email fetch → task extraction
  4. Twilio SMS reminders (immediate + scheduled)
"""

import os, json, base64, re, logging
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, UploadFile, File, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("taskmind")

# ── Gemini Setup (google-genai SDK) ─────────────────────────────────────────
from google import genai as google_genai

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
_placeholder_keys = {"your_gemini_api_key_here", "", "YOUR_KEY_HERE"}
GEMINI_ACTIVE = bool(GEMINI_KEY) and GEMINI_KEY not in _placeholder_keys

gemini_client = google_genai.Client(api_key=GEMINI_KEY) if GEMINI_ACTIVE else None
GEMINI_MODEL  = "gemini-2.5-flash"

# ── Twilio Setup ─────────────────────────────────────────────────────────────
from twilio.rest import Client as TwilioClient
TWILIO_SID   = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM  = os.getenv("TWILIO_PHONE_NUMBER", "")
twilio_client = TwilioClient(TWILIO_SID, TWILIO_TOKEN) if (TWILIO_SID and TWILIO_TOKEN) else None

# ── Gmail OAuth Setup ─────────────────────────────────────────────────────────
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import google.auth.transport.requests

GMAIL_SCOPES       = ["https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_SECRETS     = os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials.json")
BACKEND_URL        = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
FRONTEND_URL       = os.getenv("FRONTEND_URL", "http://127.0.0.1:5500").rstrip("/")
OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", f"{BACKEND_URL}/email/callback")

if OAUTH_REDIRECT_URI.startswith("http://127.0.0.1") or OAUTH_REDIRECT_URI.startswith("http://localhost"):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

# In-memory token store (production: use a database)
_gmail_tokens: dict[str, dict] = {"oauth_states": {}}

# ── APScheduler ──────────────────────────────────────────────────────────────
from apscheduler.schedulers.background import BackgroundScheduler
scheduler = BackgroundScheduler()
scheduler.start()

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="TaskMind AI", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

TASK_EXTRACTION_PROMPT = """
You are an intelligent task manager AI. Analyze the following text and extract all actionable tasks.

Date handling rules:
- Use the CURRENT DATE CONTEXT below as the source of truth for relative dates.
- Convert relative dates like "today", "tomorrow", "tonight", "this Friday", and "next Monday" into a real calendar date.
- The "deadline" field must be human-readable and include the date, e.g. "Monday, 27 Apr 2026, 5:00 PM".
- Also include "deadline_iso" in ISO-like local time format, e.g. "2026-04-27T17:00:00+05:30".
- If a task has no stated or clearly implied deadline, set "deadline" and "deadline_iso" to empty strings. Do not invent a due date.
- If only a date is mentioned, use 11:59 PM as the deadline time.
- If only a vague time of day is mentioned, use: morning=9:00 AM, afternoon=2:00 PM, evening=6:00 PM, night/tonight=9:00 PM.
- "This Friday" means the upcoming Friday in the current week. "Next Friday" means the Friday after that.

For each task, provide:
- task: Clear, concise task description
- deadline: Specific absolute date/time using the date rules above
- deadline_iso: Same deadline in ISO-like local time format, or empty string
- priority: "HIGH", "MEDIUM", or "LOW" based on urgency and importance
- priority_reason: One short phrase explaining why the AI chose that priority
- Priority must be decided by AI from deadline urgency, explicit importance words, consequences, sender/request context, and effort. Use HIGH for urgent deadlines, blockers, meetings, submissions, exams, payment/legal/health issues, or explicit "urgent/asap/important". Use HIGH for any task related to OFFICE, COLLEGE, or SCHOOL work (assignments, projects, submissions, exams, classes). Use MEDIUM for scheduled but not urgent work. Use LOW for flexible errands or no-deadline tasks.
- suggested_time: Best time of day to do this task (e.g. "9:00 AM - 10:00 AM")
- source: Where this task came from (e.g. "text", "email", "screenshot")
- category: Category (e.g. "Work", "Personal", "Study", "Health")

Also provide:
- welcome_message: A motivational AI insight about the tasks (1 sentence)
- insights: Array of 3 productivity insights based on the tasks (each with title, detail, tone: "amber"/"blue"/"green")

Return ONLY valid JSON in this exact format:
{
  "success": true,
  "tasks": [...],
  "dashboard": {
    "welcome_message": "...",
    "stats": [
      {"label": "Total Tasks", "value": "N"},
      {"label": "Due Today", "value": "N"},
      {"label": "Overdue", "value": "N"},
      {"label": "This Week", "value": "N"}
    ],
    "day_streak": 5,
    "task_completion_rate": 72,
    "chart": [
      {"label": "Mon", "value": 4},
      {"label": "Tue", "value": 6},
      {"label": "Wed", "value": 5},
      {"label": "Thu", "value": 8},
      {"label": "Fri", "value": 6},
      {"label": "Sat", "value": 3},
      {"label": "Sun", "value": 5}
    ],
    "insights": [...]
  }
}

CURRENT DATE CONTEXT:
{date_context}

Text to analyze:
"""

OCR_EXTRACTION_PROMPT = """
Look at this screenshot/image carefully. 
First, extract ALL visible text from the image.
Then, identify any actionable tasks, to-dos, reminders, deadlines, or important items.

Date handling rules:
- Use the CURRENT DATE CONTEXT below as the source of truth for relative dates.
- Convert relative dates like "today", "tomorrow", "tonight", "this Friday", and "next Monday" into a real calendar date.
- The "deadline" field must be human-readable and include the date, e.g. "Monday, 27 Apr 2026, 5:00 PM".
- Also include "deadline_iso" in ISO-like local time format, e.g. "2026-04-27T17:00:00+05:30".
- If a task has no stated or clearly implied deadline, set "deadline" and "deadline_iso" to empty strings. Do not invent a due date.
- If only a date is mentioned, use 11:59 PM as the deadline time.
- If only a vague time of day is mentioned, use: morning=9:00 AM, afternoon=2:00 PM, evening=6:00 PM, night/tonight=9:00 PM.
- "This Friday" means the upcoming Friday in the current week. "Next Friday" means the Friday after that.

Priority rules:
- Use HIGH for urgent deadlines, blockers, meetings, submissions, exams, payment/legal/health issues, or explicit "urgent/asap/important".
- Use HIGH for any task related to OFFICE, COLLEGE, or SCHOOL work (assignments, projects, submissions, exams, classes, meetings, presentations).
- Use MEDIUM for scheduled but not urgent work.
- Use LOW for flexible errands or no-deadline tasks.
- Include "priority_reason": One short phrase explaining why the AI chose that priority.

Return ONLY valid JSON in this exact format:
{
  "success": true,
  "extracted_text": "all text visible in the image",
  "tasks": [
    {
      "task": "...",
      "deadline": "...",
      "deadline_iso": "...",
      "priority": "HIGH|MEDIUM|LOW",
      "priority_reason": "...",
      "suggested_time": "...",
      "source": "screenshot",
      "category": "..."
    }
  ],
  "dashboard": {
    "welcome_message": "...",
    "stats": [
      {"label": "Total Tasks", "value": "N"},
      {"label": "Due Today", "value": "N"},
      {"label": "Overdue", "value": "N"},
      {"label": "This Week", "value": "N"}
    ],
    "day_streak": 5,
    "task_completion_rate": 72,
    "chart": [
      {"label": "Mon", "value": 4},{"label": "Tue", "value": 6},
      {"label": "Wed", "value": 5},{"label": "Thu", "value": 8},
      {"label": "Fri", "value": 6},{"label": "Sat", "value": 3},
      {"label": "Sun", "value": 5}
    ],
    "insights": [
      {"title": "...", "detail": "...", "tone": "amber"},
      {"title": "...", "detail": "...", "tone": "blue"},
      {"title": "...", "detail": "...", "tone": "green"}
    ]
  }
}

CURRENT DATE CONTEXT:
{date_context}
"""

EMAIL_EXTRACTION_PROMPT = """
Analyze these email snippets and extract all actionable tasks, deadlines, and to-dos.
Focus on:
- Meeting invites, follow-up requests, deadlines mentioned
- Action items addressed to you
- Deliverables and commitments

Date handling rules:
- Use the CURRENT DATE CONTEXT below as the source of truth for relative dates.
- Convert relative dates into absolute dates.
- Include both "deadline" and "deadline_iso" for every task.
- If a task has no stated or clearly implied deadline, set "deadline" and "deadline_iso" to empty strings.

Return ONLY valid JSON in the same format as before (with success, tasks, dashboard fields).
CURRENT DATE CONTEXT:
{date_context}

Emails:
"""


def parse_gemini_json(text: str) -> dict:
    """Extract JSON from Gemini response, stripping markdown fences."""
    text = text.strip()
    # Remove markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    return json.loads(text)


def deadline_context() -> str:
    """Current local date/time context for deadline extraction."""
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday_lines = []
    for index, name in enumerate(weekdays):
        days_until = (index - now.weekday()) % 7
        this_date = now.date() + timedelta(days=days_until)
        next_date = this_date + timedelta(days=7)
        weekday_lines.append(
            f"this {name} = {this_date.strftime('%A, %d %b %Y')}; "
            f"next {name} = {next_date.strftime('%A, %d %b %Y')}"
        )
    return (
        f"Now: {now.strftime('%A, %d %B %Y, %I:%M %p %Z')} "
        f"(ISO: {now.isoformat()}). Timezone: Asia/Kolkata (UTC+05:30).\n"
        f"Tomorrow: {(now + timedelta(days=1)).strftime('%A, %d %b %Y')}.\n"
        "Upcoming weekday anchors:\n- " + "\n- ".join(weekday_lines) + "\n"
        "Never output a weekday/date combination that conflicts with these anchors."
    )


def prompt_with_context(prompt: str) -> str:
    return prompt.replace("{date_context}", deadline_context())


def format_deadline(dt: datetime) -> tuple[str, str]:
    hour = dt.strftime("%I").lstrip("0") or "12"
    return dt.strftime(f"%A, %d %b %Y, {hour}:%M %p"), dt.isoformat()


def best_task_context(task_name: str, source_text: str) -> str:
    parts = re.split(r"[\n.;]+|\s+and\s+", source_text, flags=re.IGNORECASE)
    task_words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", task_name)}
    best = ""
    best_score = 0
    for part in parts:
        words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", part)}
        score = len(task_words & words)
        if score > best_score:
            best = part
            best_score = score
    return best or source_text


def infer_deadline_from_context(context: str) -> Optional[datetime]:
    text = context.lower()
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    date_value = None

    if "tomorrow" in text:
        date_value = (now + timedelta(days=1)).date()
    elif re.search(r"\btoday\b|\btonight\b", text):
        date_value = now.date()
    else:
        weekdays = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        match = re.search(r"\b(this|next)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text)
        if match:
            modifier = match.group(1) or "this"
            target = weekdays[match.group(2)]
            days_until = (target - now.weekday()) % 7
            if modifier == "next":
                days_until += 7
            date_value = (now + timedelta(days=days_until)).date()

    if not date_value:
        return None

    time_match = re.search(r"\b(?:by|at|before)?\s*(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = time_match.group(3)
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
    elif "morning" in text:
        hour, minute = 9, 0
    elif "afternoon" in text:
        hour, minute = 14, 0
    elif "evening" in text:
        hour, minute = 18, 0
    elif "night" in text or "tonight" in text:
        hour, minute = 21, 0
    else:
        hour, minute = 23, 59

    return datetime(date_value.year, date_value.month, date_value.day, hour, minute, tzinfo=now.tzinfo)


def normalize_deadlines_from_text(data: dict, source_text: str) -> dict:
    for task in data.get("tasks", []):
        task_name = task.get("task") or task.get("task_description") or task.get("title") or ""
        context = best_task_context(task_name, source_text)
        inferred = infer_deadline_from_context(context)
        if inferred:
            task["deadline"], task["deadline_iso"] = format_deadline(inferred)
        else:
            task.setdefault("deadline", "")
            task.setdefault("deadline_iso", "")
    return data


def send_sms(to: str, body: str):
    """Send SMS via Twilio."""
    if not twilio_client:
        raise RuntimeError("Twilio not configured")
    msg = twilio_client.messages.create(body=body, from_=TWILIO_FROM, to=to)
    return msg.sid


# ════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ════════════════════════════════════════════════════════════════════════════

class ExtractRequest(BaseModel):
    text: str

class ReminderRequest(BaseModel):
    phone: str          # E.164 format e.g. +919876543210
    task: str
    deadline: Optional[str] = None
    remind_at: Optional[str] = None  # ISO datetime string for scheduled reminder

class ImmediateReminderRequest(BaseModel):
    phone: str
    message: str


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"status": "ok", "service": "TaskMind AI Backend", "version": "2.0.0"}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "gemini": GEMINI_ACTIVE,
        "gemini_model": GEMINI_MODEL if GEMINI_ACTIVE else None,
        "twilio": bool(TWILIO_SID and TWILIO_SID != "your_twilio_account_sid"),
        "gmail_oauth": Path(CLIENT_SECRETS).exists(),
    }


# ── 1. TEXT → TASK EXTRACTION ────────────────────────────────────────────────

@app.post("/extract")
async def extract_tasks(req: ExtractRequest):
    """Extract tasks from free-form text using Gemini AI."""
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")

    if not gemini_client:
        raise HTTPException(503, "Gemini API key not configured. Add your GEMINI_API_KEY to the .env file.")

    try:
        prompt = prompt_with_context(TASK_EXTRACTION_PROMPT) + req.text
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt
        )
        data = normalize_deadlines_from_text(parse_gemini_json(response.text), req.text)
        return JSONResponse(content=data)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}\nRaw: {response.text[:500]}")
        raise HTTPException(500, f"AI returned invalid JSON: {str(e)}")
    except Exception as e:
        logger.error(f"Extract error: {e}")
        raise HTTPException(500, str(e))


# ── 2. OCR SCREENSHOT → TASKS ─────────────────────────────────────────────────

@app.post("/ocr")
async def ocr_extract(file: UploadFile = File(...)):
    """Upload a screenshot/image → Gemini Vision extracts text & tasks."""
    if not gemini_client:
        raise HTTPException(503, "Gemini API key not configured. Add your GEMINI_API_KEY to the .env file.")

    allowed_types = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
    content_type = file.content_type or "image/jpeg"
    if content_type not in allowed_types:
        raise HTTPException(400, f"Unsupported file type: {content_type}. Use JPEG, PNG, or WebP.")

    try:
        image_bytes = await file.read()
        if len(image_bytes) > 20 * 1024 * 1024:
            raise HTTPException(400, "Image too large. Max 20MB.")

        from google.genai import types as genai_types
        image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=content_type)

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[image_part, prompt_with_context(OCR_EXTRACTION_PROMPT)]
        )

        data = parse_gemini_json(response.text)
        data = normalize_deadlines_from_text(data, data.get("extracted_text") or "")
        return JSONResponse(content=data)

    except json.JSONDecodeError as e:
        logger.error(f"OCR JSON parse error: {e}")
        raise HTTPException(500, f"AI returned invalid response: {str(e)}")
    except Exception as e:
        logger.error(f"OCR error: {e}")
        raise HTTPException(500, str(e))


# ── 3. GMAIL OAUTH 2.0 ───────────────────────────────────────────────────────

@app.get("/email/auth")
async def gmail_auth():
    """Redirect user to Gmail OAuth consent screen."""
    if not Path(CLIENT_SECRETS).exists():
        raise HTTPException(503, "Gmail credentials.json not found. Please set up Gmail API in Google Cloud Console.")

    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS,
            scopes=GMAIL_SCOPES,
            redirect_uri=OAUTH_REDIRECT_URI,
        )
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        _gmail_tokens["state"] = state
        _gmail_tokens.setdefault("oauth_states", {})[state] = {
            "code_verifier": flow.code_verifier,
        }
        return RedirectResponse(auth_url)
    except Exception as e:
        raise HTTPException(500, f"OAuth init failed: {str(e)}")


@app.get("/email/callback")
async def gmail_callback(code: str, state: str):
    """Handle Gmail OAuth callback and store tokens."""
    if not Path(CLIENT_SECRETS).exists():
        raise HTTPException(503, "credentials.json not found")

    oauth_state = _gmail_tokens.get("oauth_states", {}).get(state)
    if not oauth_state:
        raise HTTPException(400, "Invalid OAuth state. Please start Gmail connection again.")

    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS,
            scopes=GMAIL_SCOPES,
            redirect_uri=OAUTH_REDIRECT_URI,
            state=state,
            code_verifier=oauth_state.get("code_verifier"),
        )
        flow.fetch_token(code=code)
        creds = flow.credentials
        _gmail_tokens.get("oauth_states", {}).pop(state, None)
        _gmail_tokens["credentials"] = {
            "token":         creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri":     creds.token_uri,
            "client_id":     creds.client_id,
            "client_secret": creds.client_secret,
            "scopes":        creds.scopes,
        }
        # Redirect back to the frontend
        return RedirectResponse(f"{FRONTEND_URL}/index.html?gmail=connected")
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        raise HTTPException(500, f"OAuth failed: {str(e)}")


@app.get("/email/status")
async def gmail_status():
    """Check if Gmail is connected."""
    return {"connected": "credentials" in _gmail_tokens}


@app.get("/email/fetch")
async def fetch_emails(max_emails: int = 10):
    """Fetch recent emails and extract tasks using Gemini AI."""
    if "credentials" not in _gmail_tokens:
        raise HTTPException(401, "Gmail not connected. Please authenticate first at /email/auth")

    if not gemini_client:
        raise HTTPException(503, "Gemini API key not configured. Add your GEMINI_API_KEY to the .env file.")

    try:
        creds_data = _gmail_tokens["credentials"]
        creds = Credentials(
            token=creds_data["token"],
            refresh_token=creds_data["refresh_token"],
            token_uri=creds_data["token_uri"],
            client_id=creds_data["client_id"],
            client_secret=creds_data["client_secret"],
            scopes=creds_data["scopes"],
        )

        # Refresh token if expired
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
            _gmail_tokens["credentials"]["token"] = creds.token

        service = build("gmail", "v1", credentials=creds)

        # Fetch recent emails
        results = service.users().messages().list(
            userId="me", maxResults=max_emails, q="is:unread"
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return JSONResponse(content={
                "success": True,
                "tasks": [],
                "message": "No unread emails found",
                "dashboard": None
            })

        email_snippets = []
        for msg_ref in messages[:max_emails]:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["Subject", "From", "Date"]
            ).execute()

            subject = ""
            sender  = ""
            date    = ""
            for h in msg.get("payload", {}).get("headers", []):
                if h["name"] == "Subject": subject = h["value"]
                if h["name"] == "From":    sender  = h["value"]
                if h["name"] == "Date":    date    = h["value"]

            snippet = msg.get("snippet", "")
            email_snippets.append(
                f"From: {sender}\nDate: {date}\nSubject: {subject}\nPreview: {snippet}"
            )

        combined = "\n\n---\n\n".join(email_snippets)
        prompt   = prompt_with_context(EMAIL_EXTRACTION_PROMPT) + combined
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt
        )
        data     = normalize_deadlines_from_text(parse_gemini_json(response.text), combined)

        # Gemini may vary field names for email tasks; normalize them for the UI.
        normalized_tasks = []
        for task in data.get("tasks", []):
            normalized_tasks.append({
                "task": task.get("task") or task.get("task_description") or task.get("title") or "Untitled email task",
                "deadline": task.get("deadline") or task.get("due_date") or "",
                "deadline_iso": task.get("deadline_iso") or task.get("due_date_iso") or "",
                "priority": str(task.get("priority") or "LOW").upper(),
                "priority_reason": task.get("priority_reason") or task.get("priorityReason") or "",
                "suggested_time": task.get("suggested_time") or "",
                "source": "email",
                "category": task.get("category") or "Email",
                "source_email_subject": task.get("source_email_subject") or "",
            })
        data["tasks"] = normalized_tasks

        if not data.get("dashboard") and data.get("dashboard_summary"):
            summary = data["dashboard_summary"]
            data["dashboard"] = {
                "welcome_message": f"Imported {len(normalized_tasks)} task(s) from Gmail.",
                "stats": [
                    {"label": "Total Tasks", "value": str(summary.get("total_tasks", len(normalized_tasks)))},
                    {"label": "Due Today", "value": str(summary.get("tasks_due_today", 0))},
                    {"label": "Overdue", "value": str(summary.get("overdue_tasks", 0))},
                    {"label": "This Week", "value": str(summary.get("tasks_due_this_week", 0))},
                ],
                "day_streak": 1,
                "task_completion_rate": 0,
                "chart": [
                    {"label": "Mon", "value": 0}, {"label": "Tue", "value": 0},
                    {"label": "Wed", "value": 0}, {"label": "Thu", "value": 0},
                    {"label": "Fri", "value": 0}, {"label": "Sat", "value": 0},
                    {"label": "Sun", "value": 0},
                ],
                "insights": [],
            }

        return JSONResponse(content=data)

    except json.JSONDecodeError as e:
        raise HTTPException(500, f"AI JSON parse error: {str(e)}")
    except Exception as e:
        logger.error(f"Email fetch error: {e}")
        raise HTTPException(500, str(e))


# ── 4. SMS REMINDERS ─────────────────────────────────────────────────────────

@app.post("/reminder/send")
async def send_reminder_now(req: ImmediateReminderRequest):
    """Send an immediate SMS reminder."""
    if not twilio_client:
        raise HTTPException(503, "Twilio not configured. Add TWILIO_* keys to .env")

    try:
        sid = send_sms(req.phone, req.message)
        return {"success": True, "message_sid": sid, "sent_to": req.phone}
    except Exception as e:
        logger.error(f"SMS error: {e}")
        raise HTTPException(500, str(e))


@app.post("/reminder/set")
async def set_reminder(req: ReminderRequest, background_tasks: BackgroundTasks):
    """Schedule an SMS reminder for a task at a specific time."""
    if not twilio_client:
        raise HTTPException(503, "Twilio not configured. Add TWILIO_* keys to .env")

    message = f"⏰ TaskMind Reminder: {req.task}"
    if req.deadline:
        message += f"\n📅 Deadline: {req.deadline}"

    if req.remind_at:
        # Parse the scheduled datetime
        try:
            remind_dt = datetime.fromisoformat(req.remind_at)
        except ValueError:
            raise HTTPException(400, f"Invalid remind_at format. Use ISO format: 2026-04-26T09:00:00")

        if remind_dt <= datetime.now():
            raise HTTPException(400, "remind_at must be in the future")

        phone = req.phone
        scheduler.add_job(
            send_sms,
            "date",
            run_date=remind_dt,
            args=[phone, message],
            id=f"reminder_{phone}_{remind_dt.timestamp()}",
            replace_existing=False,
        )
        return {
            "success": True,
            "scheduled": True,
            "remind_at": remind_dt.isoformat(),
            "phone": req.phone,
            "message": message,
        }
    else:
        # Send immediately
        background_tasks.add_task(send_sms, req.phone, message)
        return {"success": True, "scheduled": False, "sent": True, "phone": req.phone}


@app.get("/reminder/list")
async def list_reminders():
    """List all scheduled reminders."""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id":       job.id,
            "next_run": str(job.next_run_time),
            "args":     str(job.args),
        })
    return {"reminders": jobs, "count": len(jobs)}


@app.delete("/reminder/{job_id}")
async def cancel_reminder(job_id: str):
    """Cancel a scheduled reminder."""
    try:
        scheduler.remove_job(job_id)
        return {"success": True, "cancelled": job_id}
    except Exception as e:
        raise HTTPException(404, f"Reminder not found: {str(e)}")


# ── 5. IN-APP NOTIFICATIONS ───────────────────────────────────────────────────
@app.get("/notifications/pending")
async def get_pending_notifications():
    """Get tasks that are due soon or overdue for in-app notifications."""
    from pathlib import Path
    tasks_file = Path("tasks.json")
    if not tasks_file.exists():
        return {"notifications": [], "count": 0}
    
    try:
        all_tasks = json.loads(tasks_file.read_text())
    except:
        all_tasks = []
    
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    notifications = []
    
    for task in all_tasks:
        if task.get("done"):
            continue
        deadline_iso = task.get("deadline_iso", "")
        if not deadline_iso:
            continue
        try:
            deadline_dt = datetime.fromisoformat(deadline_iso.replace("+05:30", ""))
        except:
            continue
        
        # Check if due within next 15 minutes or overdue
        time_diff = (deadline_dt - now).total_seconds() / 60
        
        if time_diff <= 15 and time_diff > -1440:  # Due within 15 min or overdue (within 24h)
            notifications.append({
                "id": task.get("id", ""),
                "task": task.get("task", ""),
                "deadline": task.get("deadline", ""),
                "priority": task.get("priority", "LOW"),
                "is_overdue": time_diff < 0,
                "minutes_until_due": int(time_diff) if time_diff > 0 else int(time_diff),
            })
    
    # Sort: overdue first, then by due time
    notifications.sort(key=lambda x: (not x["is_overdue"], x["minutes_until_due"]))
    
    return {"notifications": notifications[:10], "count": len(notifications)}


# ── DASHBOARD ────────────────────────────────────────────────────────────────

@app.get("/dashboard")
async def get_dashboard():
    """Return default dashboard data."""
    return {
        "success": True,
        "dashboard": {
            "welcome_message": "You're most productive in the mornings. Schedule important tasks before noon!",
            "stats": [
                {"label": "Total Tasks", "value": "0"},
                {"label": "Due Today",   "value": "0"},
                {"label": "Overdue",     "value": "0"},
                {"label": "This Week",   "value": "0"},
            ],
            "day_streak": 1,
            "task_completion_rate": 0,
            "chart": [
                {"label": "Mon", "value": 0},
                {"label": "Tue", "value": 0},
                {"label": "Wed", "value": 0},
                {"label": "Thu", "value": 0},
                {"label": "Fri", "value": 0},
                {"label": "Sat", "value": 0},
                {"label": "Sun", "value": 0},
            ],
            "insights": [
                {
                    "title":  "Getting Started",
                    "detail": "Add your first task using the + Add Task button or connect Gmail to import tasks automatically.",
                    "tone":   "amber",
                },
                {
                    "title":  "Smart Scheduling",
                    "detail": "Upload a screenshot of any to-do list or WhatsApp chat to instantly extract tasks with AI.",
                    "tone":   "blue",
                },
                {
                    "title":  "Stay on Track",
                    "detail": "Enable SMS reminders to never miss a deadline. Add your phone number on any task card.",
                    "tone":   "green",
                },
            ],
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("APP_PORT", 8000)), reload=True)
