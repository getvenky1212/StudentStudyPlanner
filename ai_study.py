"""
ai_study.py - uses Gemini on Google Cloud (Vertex AI, now called Gemini
Enterprise Agent Platform) to turn what your teacher posted on Canvas into
study guides, practice questions, flashcards and a daily plan.

Setup (one time):
  1. Create or pick a Google Cloud project and enable the Vertex AI API.
  2. Sign in so Python can use your account:
         gcloud auth application-default login
     (or point GOOGLE_APPLICATION_CREDENTIALS at a service-account key file)
  3. Set:
         GOOGLE_CLOUD_PROJECT   your project id
         GOOGLE_CLOUD_LOCATION  e.g. us-central1   (optional, default us-central1)
         GEMINI_MODEL           optional, default gemini-3.5-flash
"""

import json

from google import genai
from google.genai import types
from ai_study_config import (
    STUDY_PACK_SCHEMA,
    SYSTEM_GUIDE,
)
from ai_study_settings import (
    CONTEXT_BUDGET,
    DAILY_PLAN_MAX_TOKENS,
    LOCATION,
    MAX_ATTACHED_BYTES,
    MAX_PDF_ATTACHMENTS,
    MODEL,
    PROJECT,
    STUDY_PACK_MAX_TOKENS,
    TEMPERATURE,
)

_client = None


def ai_available() -> bool:
    return bool(PROJECT)


def client():
    """One shared Gemini client, using your Google Cloud login."""
    global _client
    if _client is None:
        try:
            _client = genai.Client(enterprise=True, project=PROJECT, location=LOCATION)
        except TypeError:  # older google-genai versions call it vertexai=True
            _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    return _client


def build_context(materials: list[dict], budget: int = CONTEXT_BUDGET):
    """
    Returns (text_block, pdf_parts). Text from pages/PDFs goes into one block;
    scanned PDFs are attached as files so Gemini can read the page images.
    """
    parts, used = [], 0
    pdf_parts, pdf_bytes_used = [], 0
    for m in materials:
        if m.get("text") and m["text"].strip() and used < budget:
            chunk = f"### {m['title']}  (module: {m.get('module') or 'n/a'})\n{m['text'].strip()}\n"
            chunk = chunk[: budget - used]
            parts.append(chunk)
            used += len(chunk)
        data = m.get("pdf_bytes")
        if (data and len(pdf_parts) < MAX_PDF_ATTACHMENTS * 2
                and pdf_bytes_used + len(data) <= MAX_ATTACHED_BYTES):
            pdf_parts.append(types.Part.from_text(text=f"Attached PDF: {m['title']}"))
            pdf_parts.append(types.Part.from_bytes(data=data, mime_type="application/pdf"))
            pdf_bytes_used += len(data)
    return "\n".join(parts), pdf_parts


def _generate(contents, system: str, max_tokens: int, schema: dict | None = None) -> str:
    settings = {"system_instruction": system, "max_output_tokens": max_tokens, "temperature": TEMPERATURE}
    if schema:
        settings.update(response_mime_type="application/json", response_json_schema=schema)
    config = types.GenerateContentConfig(**settings)
    response = client().models.generate_content(model=MODEL, contents=contents, config=config)
    if not response.text:
        raise ValueError("Gemini returned an empty reply (it may have been blocked or cut off). Try again.")
    return response.text


def study_pack(course: str, target: dict, materials: list[dict], today: str) -> dict:
    """
    target = {"name": ..., "due_text": ..., "description": ..., "is_test": bool}
    Returns {"summary", "key_concepts", "practice_questions", "flashcards", "study_plan"}.
    """
    text_block, pdf_parts = build_context(materials)
    if not text_block and not pdf_parts:
        raise ValueError("None of this course's materials could be read (pages, PDFs or "
                         "the syllabus). Nothing to study from yet.")

    kind = "test" if target.get("is_test") else "assignment"
    prompt = f"""Today is {today}.
Course: {course}
Upcoming {kind}: {target['name']} (due {target.get('due_text', 'no date')})
What the teacher wrote about it: {target.get('description') or 'nothing'}

<course_material>
{text_block or '(see attached PDFs)'}
</course_material>

Focus on the parts of the material most relevant to this {kind}.
Give 8-12 key concepts, 5-8 practice questions, 12-20 short flashcards, and a
realistic study plan with one line per day from today until it's due."""

    raw = _generate([prompt, *pdf_parts], SYSTEM_GUIDE,
                    max_tokens=STUDY_PACK_MAX_TOKENS, schema=STUDY_PACK_SCHEMA)
    pack = json.loads(raw)
    for key in ("key_concepts", "practice_questions", "flashcards", "study_plan"):
        pack.setdefault(key, [])
    pack.setdefault("summary", "")
    return pack


def daily_plan(items: list[dict], now_text: str) -> str:
    """A short, prioritized plan for today from the student's to-do list."""
    if not items:
        return "Nothing due in the next two weeks. Good time to review older material."
    lines = "\n".join(
        f"- {i['name']} ({i['course']}), due {i['due_text']}"
        f"{' [TEST]' if i.get('is_test') else ''}{' [OVERDUE]' if i.get('level') == 'overdue' else ''}"
        for i in items[:30]
    )
    prompt = (
        f"It is {now_text}. Here is my upcoming schoolwork:\n{lines}\n\n"
        "Give me a prioritized plan for today in at most 6 short bullet points "
        "(overdue work and anything due within 24 hours first, and start test prep early). "
        "Then add one line on what to begin this week. Be specific and realistic."
    )
    return _generate(prompt, "You are a friendly, practical study coach for a student.",
                     max_tokens=DAILY_PLAN_MAX_TOKENS)
