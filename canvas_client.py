"""
canvas_client.py - reads your courses, assignments, tests and course
materials from Canvas LMS using the Canvas REST API.

You need:
  * CANVAS_BASE_URL  e.g. https://yourschool.instructure.com
  * CANVAS_TOKEN     a personal access token
                     (Canvas > Account > Settings > "+ New Access Token")

This module only READS from Canvas. It never submits or changes anything.
"""

import io
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# Words in an assignment name that usually mean it's a test
TEST_WORDS = re.compile(r"\b(exam|test|quiz|midterm|final)s?\b", re.IGNORECASE)


class CanvasError(Exception):
    """A problem talking to Canvas that the student should see."""


def html_to_text(html: str) -> str:
    """Turn Canvas HTML (pages, syllabus, descriptions) into plain text."""
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


def parse_time(value):
    """Canvas dates look like 2026-09-29T03:59:59Z. Returns a datetime or None."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_assignment(a: dict, course: dict) -> dict:
    """Keep only the fields the app needs, in a simple shape."""
    submission = a.get("submission") or {}
    types = a.get("submission_types") or []
    is_test = bool(
        a.get("is_quiz_assignment")
        or a.get("quiz_id")
        or "online_quiz" in types
        or TEST_WORDS.search(a.get("name") or "")
    )
    submitted = bool(submission.get("submitted_at")) or submission.get("workflow_state") in (
        "submitted", "graded", "pending_review",
    )
    return {
        "id": f"canvas-{a['id']}",
        "course_id": course["id"],
        "course": course.get("course_code") or course.get("name", "Course"),
        "name": a.get("name") or "Untitled",
        "due_at": a.get("due_at"),
        "url": a.get("html_url"),
        "is_test": is_test,
        "submitted": submitted,
        "points": a.get("points_possible"),
        "description": html_to_text(a.get("description") or "")[:3000],
    }


class CanvasClient:
    def __init__(self, base_url: str, token: str, timeout: int = 20):
        base_url = base_url.strip().rstrip("/")
        if not base_url.startswith("http"):
            base_url = "https://" + base_url
        self.base = base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token.strip()}"

    # -- low-level ---------------------------------------------------------
    def _get(self, path: str, params: dict | None = None):
        """GET from the API and follow Canvas's page-by-page 'next' links."""
        url = f"{self.base}/api/v1{path}"
        params = {"per_page": 100, **(params or {})}
        results = []
        while url:
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                raise CanvasError(f"Couldn't reach Canvas at {self.base}: {exc}") from exc
            if r.status_code == 401:
                raise CanvasError("Canvas didn't accept the access token (401). "
                                  "Check CANVAS_TOKEN or make a new one.")
            if r.status_code in (403, 404):
                # Hidden from students (e.g. Files tab turned off) - just skip it
                # ({} is safe both to loop over and to call .get() on)
                return results or {}
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                return data
            results.extend(data)
            url = r.links.get("next", {}).get("url")
            params = None  # the "next" link already contains the parameters
        return results

    # -- what the app uses -------------------------------------------------
    def me(self) -> dict:
        return self._get("/users/self")

    def courses(self) -> list[dict]:
        courses = self._get("/courses", {
            "enrollment_state": "active",
            "include[]": ["syllabus_body"],
        })
        return [
            {
                "id": c["id"],
                "name": c.get("name"),
                "course_code": c.get("course_code"),
                "syllabus_body": c.get("syllabus_body") or "",
            }
            for c in courses
            if c.get("name") and not c.get("access_restricted_by_date")
        ]

    def assignments(self, course: dict) -> list[dict]:
        items = self._get(f"/courses/{course['id']}/assignments", {
            "include[]": ["submission"],
            "order_by": "due_at",
        })
        return [normalize_assignment(a, course) for a in items]

    def modules(self, course_id) -> list[dict]:
        modules = self._get(f"/courses/{course_id}/modules", {"include[]": ["items"]})
        for m in modules:
            # Canvas leaves out items when a module is very large
            if m.get("items") is None:
                m["items"] = self._get(f"/courses/{course_id}/modules/{m['id']}/items")
        return modules

    def page_text(self, course_id, page_url: str) -> str:
        page = self._get(f"/courses/{course_id}/pages/{page_url}")
        return html_to_text(page.get("body") or "") if isinstance(page, dict) else ""

    def file_text(self, file_id, max_bytes: int = 15_000_000) -> tuple[str, str, bytes | None]:
        """
        Download a course file. Returns (type_label, text, pdf_bytes).
        pdf_bytes is only filled for scanned PDFs (no text layer), so Gemini
        can read the pages directly, since it understands PDFs natively.
        """
        f = self._get(f"/files/{file_id}")
        if not isinstance(f, dict) or not f.get("url"):
            return "FILE", "", None
        name = (f.get("display_name") or f.get("filename") or "").lower()
        label = name.rsplit(".", 1)[-1].upper()[:4] if "." in name else "FILE"
        if (f.get("size") or 0) > max_bytes:
            return label, "", None
        try:
            r = self.session.get(f["url"], timeout=60)
            r.raise_for_status()
        except requests.RequestException:
            return label, "", None

        if name.endswith(".pdf"):
            from pypdf import PdfReader
            try:
                reader = PdfReader(io.BytesIO(r.content))
                text = "\n".join((p.extract_text() or "") for p in reader.pages)
            except Exception:
                text = ""
            scanned = len(text.strip()) < 200 and len(r.content) <= 8_000_000
            return "PDF", text, (r.content if scanned else None)
        if name.endswith((".txt", ".md", ".csv")):
            return label, r.text, None
        if name.endswith((".html", ".htm")):
            return "PAGE", html_to_text(r.text), None
        return label, "", None  # slides, Word files, videos: link only

    def course_materials(self, course: dict, max_items: int = 30,
                         max_chars_each: int = 20_000) -> list[dict]:
        """Syllabus + pages, files and links posted in the course's modules."""
        materials = []
        syllabus = html_to_text(course.get("syllabus_body") or "")
        if syllabus:
            materials.append({
                "title": "Syllabus", "type": "PAGE", "module": "Course info",
                "text": syllabus[:max_chars_each],
                "url": f"{self.base}/courses/{course['id']}/assignments/syllabus",
            })

        for module in self.modules(course["id"]):
            for item in module.get("items") or []:
                if len(materials) >= max_items:
                    return materials
                kind = item.get("type")
                pdf_bytes = None
                if kind == "Page":
                    label, text = "PAGE", self.page_text(course["id"], item.get("page_url"))
                elif kind == "File":
                    label, text, pdf_bytes = self.file_text(item.get("content_id"))
                elif kind == "ExternalUrl":
                    label, text = "LINK", ""
                else:
                    continue  # assignments, quizzes, headers are covered elsewhere
                materials.append({
                    "title": item.get("title") or "Untitled",
                    "type": label,
                    "module": module.get("name", ""),
                    "text": (text or "")[:max_chars_each],
                    "pdf_bytes": pdf_bytes,
                    "url": item.get("html_url") or item.get("external_url"),
                })
        return materials
