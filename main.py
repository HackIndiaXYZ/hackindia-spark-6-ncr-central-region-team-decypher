import json
import logging
import os
import re
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from google import genai

# ======================================================
# LOAD ENV
# ======================================================
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("taskmind")

# ======================================================
# APP
# ======================================================
app = FastAPI(
    title="TaskMind AI API",
    description="Extract structured tasks from messy text using Gemini with resilient local fallback.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================================================
# MODELS
# ======================================================
class ExtractRequest(BaseModel):
    text: str = Field(..., description="Raw user text")


class TaskItem(BaseModel):
    task: str
    deadline: str
    priority: str
    suggested_time: str
    estimated_time: str


class ExtractResponse(BaseModel):
    success: bool
    tasks: List[TaskItem]
    notice: Optional[str] = None
    suggestion: Optional[str] = None


class StatCard(BaseModel):
    label: str
    value: str
    tone: str


class InsightCard(BaseModel):
    title: str
    detail: str
    tone: str


class ChartPoint(BaseModel):
    label: str
    value: int


class DashboardData(BaseModel):
    welcome_title: str
    welcome_message: str
    today_label: str
    stats: List[StatCard]
    day_streak: int
    task_completion_rate: int
    chart: List[ChartPoint]
    insights: List[InsightCard]
    highlights: List[str]


class ExtractResponseExtended(ExtractResponse):
    dashboard: DashboardData


# ======================================================
# ERROR HANDLING
# ======================================================
@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "tasks": [],
            "error": exc.detail,
        },
    )


@app.exception_handler(Exception)
async def server_error(_: Request, exc: Exception):
    logger.exception(exc)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "tasks": [],
            "error": "Internal server error",
        },
    )

# ======================================================
# HELPERS
# ======================================================
def _clean_task_text(raw: str) -> str:
    cleaned = re.sub(
        r"\b(i need to|need to|must|have to|please|i should)\b",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,!")

    if not cleaned:
        return ""

    return cleaned[0].upper() + cleaned[1:]


def _infer_priority(text: str) -> str:
    t = text.lower()

    if any(x in t for x in ["urgent", "exam", "submit", "assignment", "deadline"]):
        return "High"

    if any(x in t for x in ["study", "prepare", "meeting", "project"]):
        return "Medium"

    return "Low"


def _infer_deadline(text: str) -> str:
    t = text.lower()

    if "today" in t:
        return "Today"

    if "tomorrow" in t:
        return "Tomorrow"

    if "weekend" in t:
        return "This Weekend"

    if "monday" in t:
        return "By Monday"

    return "This Week"


def _infer_suggested_time(deadline: str, priority: str) -> str:
    d = deadline.lower()

    if "today" in d:
        return "6 PM - 8 PM"

    if "tomorrow" in d:
        return "9 AM - 11 AM"

    if "weekend" in d:
        return "10 AM - 12 PM"

    if priority == "High":
        return "Next focused 2-hour block"

    return "5 PM - 6 PM"


def _infer_estimated_time(text: str) -> str:
    t = text.lower()

    if any(x in t for x in ["assignment", "study", "project", "prepare"]):
        return "2 hrs"

    if any(x in t for x in ["wash", "clean", "buy"]):
        return "45 mins"

    if any(x in t for x in ["call", "email", "message"]):
        return "20 mins"

    return "1 hr"


def _extract_json_from_text(text: str):
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        return None

    try:
        return json.loads(text[start:end + 1])
    except:
        return None


# ======================================================
# FALLBACK LOCAL EXTRACTION
# ======================================================
def _extract_fallback_tasks(text: str) -> List[TaskItem]:
    chunks = re.split(r",|;|\n|\band\b", text)

    tasks = []

    for chunk in chunks:
        task = _clean_task_text(chunk)

        if not task:
            continue

        priority = _infer_priority(task)
        deadline = _infer_deadline(chunk)
        suggested = _infer_suggested_time(deadline, priority)
        estimated = _infer_estimated_time(task)

        tasks.append(
            TaskItem(
                task=task,
                deadline=deadline,
                priority=priority,
                suggested_time=suggested,
                estimated_time=estimated,
            )
        )

    return tasks[:10]


# ======================================================
# DASHBOARD
# ======================================================
def _build_dashboard(tasks: List[TaskItem], notice=None):
    total = len(tasks)
    high = sum(1 for x in tasks if x.priority == "High")

    return DashboardData(
        welcome_title="Welcome Back, Headmaster",
        welcome_message=notice or "TaskMind AI is ready to organize your day.",
        today_label=datetime.now().strftime("%B %Y"),
        stats=[
            StatCard(label="Total Magic", value=str(total), tone="neutral"),
            StatCard(label="High Priority", value=str(high), tone="red"),
            StatCard(label="Focus Score", value="88%", tone="green"),
            StatCard(label="Streak", value="5 Days", tone="gold"),
        ],
        day_streak=5,
        task_completion_rate=88,
        chart=[
            ChartPoint(label="M", value=4),
            ChartPoint(label="T", value=6),
            ChartPoint(label="W", value=5),
            ChartPoint(label="T", value=8),
            ChartPoint(label="F", value=6),
            ChartPoint(label="S", value=3),
            ChartPoint(label="S", value=5),
        ],
        insights=[
            InsightCard(
                title="Peak Focus",
                detail="Do high-priority tasks in morning.",
                tone="green",
            ),
            InsightCard(
                title="Momentum",
                detail="Finish one small task first.",
                tone="blue",
            ),
        ],
        highlights=[
            "Students preparing for exams",
            "Professionals meeting deadlines",
            "Entrepreneurs managing goals",
        ],
    )


# ======================================================
# GEMINI EXTRACTION
# ======================================================
from google import genai

async def _extract_with_gemini(text: str, api_key: str):
    client = genai.Client(api_key=api_key)

    prompt = f"""
Convert messy human text into tasks.

Return ONLY JSON:

{{
  "tasks":[
    {{
      "task":"...",
      "deadline":"...",
      "priority":"High",
      "suggested_time":"...",
      "estimated_time":"..."
    }}
  ]
}}

Input:
{text}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    raw = response.text

    extracted = _extract_json_from_text(raw)

    if not extracted:
        return []

    tasks = []

    for item in extracted.get("tasks", []):
        tasks.append(
            TaskItem(
                task=item.get("task", ""),
                deadline=item.get("deadline", "This Week"),
                priority=item.get("priority", "Medium"),
                suggested_time=item.get("suggested_time", "5 PM"),
                estimated_time=item.get("estimated_time", "1 hr"),
            )
        )

    return tasks[:10]


# ======================================================
# ROUTES
# ======================================================
@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "ok",
        "service": "TaskMind AI API",
    }


@app.post("/extract", response_model=ExtractResponseExtended)
async def extract_tasks(payload: ExtractRequest):

    text = payload.text.strip()

    if not text:
        raise HTTPException(status_code=400, detail="Input text cannot be empty.")

    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    # No key -> fallback
    if not api_key:
        fallback = _extract_fallback_tasks(text)

        return ExtractResponseExtended(
            success=True,
            tasks=fallback,
            notice="Magic servers are busy. Showing local predictions.",
            dashboard=_build_dashboard(fallback),
        )

    try:
        tasks = await _extract_with_gemini(text, api_key)

        if tasks:
            return ExtractResponseExtended(
                success=True,
                tasks=tasks,
                dashboard=_build_dashboard(tasks),
            )

        return ExtractResponseExtended(
            success=True,
            tasks=[],
            suggestion="No tasks detected.",
            dashboard=_build_dashboard([]),
        )

    except Exception as e:
        logger.warning("Gemini failed: %s", e)

        fallback = _extract_fallback_tasks(text)

        return ExtractResponseExtended(
            success=True,
            tasks=fallback,
            notice="Magic servers are busy. Showing local predictions.",
            dashboard=_build_dashboard(fallback),
        )