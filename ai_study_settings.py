"""Editable Gemini settings; environment variables override these defaults."""

import os

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

MAX_PDF_ATTACHMENTS = 4
MAX_ATTACHED_BYTES = 15_000_000
CONTEXT_BUDGET = 120_000
TEMPERATURE = 0.3
STUDY_PACK_MAX_TOKENS = 8192
DAILY_PLAN_MAX_TOKENS = 1024