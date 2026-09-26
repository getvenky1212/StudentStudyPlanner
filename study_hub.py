"""
Study Hub - a student dashboard connected to Canvas, with AI study help.

  * Reads your courses, assignments and tests from Canvas
  * Puts them in your to-do list with alerts (overdue, due today, test soon)
  * Uses Gemini on Google Cloud (Vertex AI) to build study guides and flashcards
    from what's posted on Canvas, including scanned PDFs

Run:
    pip install -r requirements.txt
    streamlit run study_hub.py

With no Canvas token set, the app runs in DEMO mode with sample courses.
"""

import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

import ai_study
from canvas_client import CanvasClient, CanvasError, parse_time

st.set_page_config(page_title="Study Hub", layout="wide")

DATA_FILE = Path(__file__).with_name("study_hub_data.json")
PALETTE = [("#B23A26", "#F9E3DC"), ("#1E6B57", "#DCEFE8"), ("#8A5510", "#F5E9D3"),
           ("#2B59C3", "#E0E8FA"), ("#6B3FA0", "#ECE3F7"), ("#6B665C", "#EDE6D6")]
WINDOW_DAYS = 14  # how far ahead (and back, for overdue) the to-do list looks

SAMPLE_DECK = [
    {"q": "What is Avogadro's number?", "a": "6.022 x 10^23 particles per mole"},
    {"q": "Define an isotope.", "a": "Atoms of the same element with different numbers of neutrons"},
    {"q": "What does a catalyst do?", "a": "Speeds up a reaction by lowering activation energy, without being used up"},
]

st.markdown(
    """
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,600;9..144,700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
    <style>
      .stApp { background: #F3EFE6; color: #1B1A17; font-family: 'DM Sans', sans-serif; }
      h1, h2, h3 { font-family: 'Fraunces', serif !important; color: #1B1A17 !important; }
      .mono { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: #5C574D; }
      .muted { color: #5C574D; font-size: 13px; }
      .dot { display: inline-block; width: 10px; height: 10px; border-radius: 5px; margin-right: 6px; }
      .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
               font-weight: 600; margin-right: 6px; }
      .test-row { display: flex; gap: 14px; align-items: center; padding: 10px 12px;
                  border-radius: 14px; margin-bottom: 8px; border: 1px solid #E2DBCB; }
      .date-tile { width: 52px; text-align: center; background: #FFFDF8; border: 1px solid #E2DBCB;
                   border-radius: 10px; padding: 4px 0; flex-shrink: 0; }
      .date-tile .mon { font-family: 'JetBrains Mono', monospace; font-size: 11px; }
      .date-tile .day { font-family: 'Fraunces', serif; font-size: 22px; font-weight: 700; line-height: 1.1; }
      .flashcard { min-height: 190px; border-radius: 16px; padding: 22px; display: flex;
                   flex-direction: column; justify-content: center; align-items: center;
                   text-align: center; gap: 10px; margin: 10px 0; }
      .flashcard .side { font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .08em; opacity: .8; }
      .flashcard .text { font-family: 'Fraunces', serif; font-size: 21px; font-weight: 600; }
      .mat { display: flex; gap: 12px; align-items: center; background: #FFFDF8;
             border: 1px solid #E2DBCB; border-radius: 14px; padding: 12px; margin-bottom: 10px; }
      .ftype { width: 46px; height: 46px; border-radius: 10px; display: flex; align-items: center;
               justify-content: center; font-family: 'JetBrains Mono', monospace; font-size: 11px; flex-shrink: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)
esc = html.escape  # anything that came from Canvas gets escaped before we show it as HTML


# ---------------------------------------------------------------------------
# Local storage: your own tasks, what you've ticked off, and saved AI guides
# ---------------------------------------------------------------------------
def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"todos": [], "done_ids": [], "packs": {}}


def save_data():
    DATA_FILE.write_text(json.dumps(st.session_state.data, indent=2))


if "data" not in st.session_state:
    st.session_state.data = load_data()
    st.session_state.deck = SAMPLE_DECK
    st.session_state.deck_name = "Sample deck"
    st.session_state.card = 0
    st.session_state.flipped = False
    st.session_state.known = 0
data = st.session_state.data
for key, default in (("todos", []), ("done_ids", []), ("packs", {})):
    data.setdefault(key, default)


# ---------------------------------------------------------------------------
# Demo data (used when Canvas isn't connected)
# ---------------------------------------------------------------------------
def demo_canvas():
    now = datetime.now(timezone.utc)

    def at(days, hours=0):
        return (now + timedelta(days=days, hours=hours)).isoformat()

    courses = [
        {"id": 1, "name": "Chemistry 101", "course_code": "CHEM 101", "syllabus_body": ""},
        {"id": 2, "name": "Biology 110", "course_code": "BIO 110", "syllabus_body": ""},
        {"id": 3, "name": "World History", "course_code": "HIST 120", "syllabus_body": ""},
    ]

    def item(i, cid, code, name, due, test=False, desc=""):
        return {"id": f"canvas-{i}", "course_id": cid, "course": code, "name": name, "due_at": due,
                "url": None, "is_test": test, "submitted": False, "points": 100, "description": desc}

    items = [
        item(1, 1, "CHEM 101", "Lab report: reaction rates", at(-1)),
        item(2, 1, "CHEM 101", "Stoichiometry problem set", at(0, 6)),
        item(3, 1, "CHEM 101", "Unit 4 Exam", at(4), True, "Covers moles, stoichiometry and reaction energy."),
        item(4, 2, "BIO 110", "Cell respiration reading quiz", at(2), True),
        item(5, 2, "BIO 110", "Lab 3 worksheet", at(5)),
        item(6, 3, "HIST 120", "WWI essay outline", at(1, 3)),
        item(7, 3, "HIST 120", "Midterm exam", at(9), True, "Chapters 1-6."),
    ]
    return {"user": "Demo student", "courses": courses, "items": items}


DEMO_MATERIALS = {
    1: [
        {"title": "Unit 4 notes: the mole", "type": "PAGE", "module": "Unit 4", "url": None,
         "text": "A mole is 6.022 x 10^23 particles (Avogadro's number). Molar mass (g/mol) converts "
                 "grams to moles. Stoichiometry uses mole ratios from a balanced equation to relate "
                 "amounts of reactants and products. The limiting reactant is used up first and sets "
                 "the theoretical yield. Percent yield = actual / theoretical x 100."},
        {"title": "Reaction energy slides", "type": "PDF", "module": "Unit 4", "url": None,
         "text": "Exothermic reactions release heat (delta H < 0); endothermic reactions absorb heat "
                 "(delta H > 0). Activation energy is the minimum energy needed to start a reaction. "
                 "Catalysts lower activation energy and are not consumed."},
    ],
    2: [
        {"title": "Chapter 9: cellular respiration", "type": "PAGE", "module": "Week 5", "url": None,
         "text": "Cellular respiration turns glucose into ATP. Glycolysis happens in the cytoplasm and "
                 "makes 2 ATP. The Krebs cycle runs in the mitochondrial matrix. The electron transport "
                 "chain on the inner membrane makes most of the ATP and needs oxygen as the final "
                 "electron acceptor. Without oxygen, cells use fermentation."},
    ],
    3: [
        {"title": "Causes of WWI lecture notes", "type": "PAGE", "module": "Unit 2", "url": None,
         "text": "Historians group the causes of WWI as militarism, alliances, imperialism and "
                 "nationalism. The assassination of Archduke Franz Ferdinand in Sarajevo in June 1914 "
                 "triggered a chain of declarations of war through the alliance system."},
    ],
}


# ---------------------------------------------------------------------------
# Canvas connection (cached so we don't call Canvas on every click)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner="Reading your courses from Canvas...")
def fetch_canvas(base_url, token):
    client = CanvasClient(base_url, token)
    courses = client.courses()
    items = []
    for course in courses:
        items += client.assignments(course)
    return {"user": client.me().get("name", "Student"), "courses": courses, "items": items}


@st.cache_data(ttl=3600, show_spinner="Reading course materials from Canvas...")
def fetch_materials(base_url, token, course):
    return CanvasClient(base_url, token).course_materials(course)


with st.sidebar:
    st.markdown("### Canvas")
    base_url = st.text_input("Canvas address", os.environ.get("CANVAS_BASE_URL", ""),
                             placeholder="https://yourschool.instructure.com")
    token = st.text_input("Access token", os.environ.get("CANVAS_TOKEN", ""), type="password",
                          help="Canvas > Account > Settings > + New Access Token")
    connected = bool(base_url and token)
    if st.button("Sync now", use_container_width=True, disabled=not connected):
        fetch_canvas.clear()
        fetch_materials.clear()
    st.markdown("### AI")
    if ai_study.ai_available():
        st.caption(f"Gemini on Google Cloud: {ai_study.MODEL} · project {ai_study.PROJECT} · {ai_study.LOCATION}")
    else:
        st.caption("Set GOOGLE_CLOUD_PROJECT (and sign in with gcloud) to turn on AI study guides.")

if connected:
    try:
        canvas = fetch_canvas(base_url, token)
    except CanvasError as exc:
        st.error(str(exc))
        st.stop()
else:
    canvas = demo_canvas()
    st.info("Demo mode: showing sample courses. Add your Canvas address and access token "
            "in the sidebar to see your real work.")


def materials_for(course):
    if connected:
        return fetch_materials(base_url, token, course)
    return DEMO_MATERIALS.get(course["id"], [])


courses = canvas["courses"]
color_of = {c["id"]: PALETTE[i % len(PALETTE)] for i, c in enumerate(courses)}
course_by_id = {c["id"]: c for c in courses}


# ---------------------------------------------------------------------------
# Turn Canvas items into to-do rows with alert levels
# ---------------------------------------------------------------------------
now = datetime.now(timezone.utc)


def when_text(hours):
    if hours < 0:
        days = int(-hours // 24)
        return "overdue" if days == 0 else f"{days} day{'s' if days > 1 else ''} overdue"
    if hours < 1:
        return "due within the hour"
    if hours < 24:
        return f"due in {int(hours)} h"
    days = round(hours / 24)
    return "due tomorrow" if days == 1 else f"due in {days} days"


def build_rows():
    rows = []
    for it in canvas["items"]:
        due = parse_time(it["due_at"])
        if it["submitted"] or it["id"] in data["done_ids"] or not due:
            continue
        hours = (due - now).total_seconds() / 3600
        if not -24 * WINDOW_DAYS <= hours <= 24 * WINDOW_DAYS:
            continue
        level = "overdue" if hours < 0 else "urgent" if hours <= 24 else "soon" if hours <= 72 else "later"
        local = due.astimezone()
        rows.append({**it, "due": due, "hours": hours, "level": level,
                     "due_text": local.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")})
    rows.sort(key=lambda r: r["due"])
    return rows


rows = build_rows()
BADGE = {
    "overdue": ("#B23A26", "#FFFFFF"),
    "urgent": ("#F9E3DC", "#9E2F1D"),
    "soon": ("#F5E9D3", "#7A4A0C"),
    "later": ("#EDE6D6", "#4A463E"),
}


def mark_done(item_id):
    data["done_ids"].append(item_id)
    save_data()


def toggle_own(i):
    data["todos"][i]["done"] = not data["todos"][i]["done"]
    save_data()


def add_own():
    text = st.session_state.new_task.strip()
    if text:
        data["todos"].append({"text": text, "due": st.session_state.new_due.strip() or "No date", "done": False})
        save_data()
    st.session_state.new_task = ""
    st.session_state.new_due = ""


def clear_own_done():
    data["todos"] = [t for t in data["todos"] if not t["done"]]
    save_data()


# ---------------------------------------------------------------------------
# AI study guides
# ---------------------------------------------------------------------------
def make_pack(item):
    course = course_by_id[item["course_id"]]
    mats = materials_for(course)
    target = {"name": item["name"], "due_text": item.get("due_text", ""),
              "description": item.get("description", ""), "is_test": item.get("is_test")}
    with st.spinner(f"Building a study guide for {item['name']}..."):
        pack = ai_study.study_pack(course["name"], target, mats, now.astimezone().strftime("%A, %B %d, %Y"))
    pack["for"] = f"{item['course']}: {item['name']}"
    data["packs"][item["id"]] = pack
    save_data()
    use_deck(item["id"])


def use_deck(item_id):
    pack = data["packs"].get(item_id)
    if pack and pack.get("flashcards"):
        st.session_state.deck = pack["flashcards"]
        st.session_state.deck_name = pack.get("for", "Study guide")
        st.session_state.card = 0
        st.session_state.flipped = False
        st.session_state.known = 0


def run_pack(item):
    try:
        make_pack(item)
        st.toast("Study guide ready. Flashcards loaded.")
    except Exception as exc:  # show AI or Canvas problems without crashing the app
        st.error(f"Couldn't build the study guide: {exc}")


# ---------------------------------------------------------------------------
# Header + alerts
# ---------------------------------------------------------------------------
st.markdown(f"<div class='mono'>{now.astimezone().strftime('%A, %B %d').upper()}</div>", unsafe_allow_html=True)
first_name = (canvas.get("user") or "").split(" ")[0]
st.markdown(f"# Hi {esc(first_name)}, here's your day." if first_name else "# Here's your day.")

overdue = [r for r in rows if r["level"] == "overdue"]
urgent = [r for r in rows if r["level"] == "urgent"]
tests_week = [r for r in rows if r["is_test"] and 0 <= r["hours"] <= 24 * 7]
if overdue:
    st.error(f"**{len(overdue)} overdue:** " + ", ".join(f"{r['name']} ({r['course']})" for r in overdue))
if urgent:
    st.warning("**Due in the next 24 hours:** " + ", ".join(
        f"{r['name']} ({when_text(r['hours'])})" for r in urgent))
if tests_week:
    st.info(f"**Tests this week:** " + ", ".join(f"{r['name']} ({r['course']}, {when_text(r['hours'])})"
                                                for r in tests_week))
if "alerted" not in st.session_state and (overdue or urgent):
    st.toast(f"{len(overdue) + len(urgent)} item(s) need your attention today")
    st.session_state.alerted = True

col_todo, col_tests, col_cards = st.columns([1.2, 1, 1], gap="large")

# ---------------------------------------------------------------------------
# 1) To-do list: Canvas work (with alerts) + your own tasks
# ---------------------------------------------------------------------------
with col_todo:
    st.markdown("## To-do")
    st.markdown(f"<div class='mono'>{len(rows)} from Canvas · next {WINDOW_DAYS} days</div>", unsafe_allow_html=True)

    for r in rows:
        color = color_of.get(r["course_id"], PALETTE[-1])[0]
        bg, fg = BADGE[r["level"]]
        prefix = "Test: " if r["is_test"] else ""
        st.checkbox(f"{prefix}{r['name']}", key=f"done_{r['id']}", on_change=mark_done, args=(r["id"],),
                    help="Hides it from this list. Nothing is submitted to Canvas.")
        link = f" · <a href='{esc(r['url'])}' target='_blank'>Open in Canvas</a>" if r.get("url") else ""
        st.markdown(
            f"<div class='muted' style='margin:-10px 0 10px 28px'>"
            f"<span class='badge' style='background:{bg};color:{fg}'>{esc(when_text(r['hours']))}</span>"
            f"<span class='dot' style='background:{color}'></span>{esc(r['course'])} · {esc(r['due_text'])}{link}</div>",
            unsafe_allow_html=True,
        )

    if ai_study.ai_available() and st.button("Plan my day with AI", use_container_width=True):
        try:
            with st.spinner("Thinking about your day..."):
                st.session_state.plan = ai_study.daily_plan(rows, now.astimezone().strftime("%A %B %d, %I:%M %p"))
        except Exception as exc:
            st.error(f"Couldn't make a plan: {exc}")
    if st.session_state.get("plan"):
        st.info(st.session_state.plan)

    st.markdown("#### My own tasks")
    for i, t in enumerate(data["todos"]):
        label = f"~~{t['text']}~~" if t["done"] else t["text"]
        st.checkbox(label, value=t["done"], key=f"own_{i}_{t['text']}", on_change=toggle_own, args=(i,))
        st.markdown(f"<div class='muted' style='margin:-10px 0 8px 28px'>{esc(t['due'])}</div>",
                    unsafe_allow_html=True)
    with st.form("add_task"):
        c1, c2 = st.columns([2, 1])
        c1.text_input("New task", key="new_task", placeholder="Something not on Canvas")
        c2.text_input("Due", key="new_due", placeholder="e.g. Friday")
        st.form_submit_button("Add task", on_click=add_own, use_container_width=True)
    if any(t["done"] for t in data["todos"]):
        st.button("Clear finished tasks", on_click=clear_own_done)

# ---------------------------------------------------------------------------
# 2) Upcoming tests (from Canvas)
# ---------------------------------------------------------------------------
with col_tests:
    st.markdown("## Upcoming tests")
    upcoming_tests = [r for r in rows if r["is_test"] and r["hours"] >= 0]
    if not upcoming_tests:
        st.caption(f"No tests on Canvas in the next {WINDOW_DAYS} days.")
    for r in upcoming_tests:
        color, tint = color_of.get(r["course_id"], PALETTE[-1])
        local = r["due"].astimezone()
        row_bg = tint if r["hours"] <= 24 * 5 else "#FFFDF8"
        ready = r["id"] in data["packs"]
        st.markdown(
            f"""<div class='test-row' style='background:{row_bg}'>
              <div class='date-tile'><div class='mon' style='color:{color}'>{local.strftime('%b').upper()}</div>
              <div class='day'>{local.day}</div></div>
              <div style='flex:1'><b>{esc(r['name'])}</b>
              <div class='muted'>{esc(r['course'])} · <span style='color:{color};font-weight:600'>{esc(when_text(r['hours']))}</span>
              {' · study guide ready' if ready else ''}</div></div></div>""",
            unsafe_allow_html=True,
        )
        if ai_study.ai_available():
            b1, b2 = st.columns(2)
            if b1.button("Rebuild guide" if ready else "Make study guide", key=f"gen_{r['id']}",
                         use_container_width=True):
                run_pack(r)
                st.rerun()
            if ready and b2.button("Use flashcards", key=f"deck_{r['id']}", use_container_width=True):
                use_deck(r["id"])
                st.rerun()

# ---------------------------------------------------------------------------
# 3) Flashcards (AI-made from Canvas, or the sample deck)
# ---------------------------------------------------------------------------
def flip():
    st.session_state.flipped = not st.session_state.flipped


def next_card(knew):
    st.session_state.card = (st.session_state.card + 1) % len(st.session_state.deck)
    st.session_state.flipped = False
    st.session_state.known += 1 if knew else 0


with col_cards:
    st.markdown("## Flashcards")
    deck = st.session_state.deck
    card = deck[st.session_state.card % len(deck)]
    flipped = st.session_state.flipped
    st.markdown(f"<div class='mono'>{esc(st.session_state.deck_name)} · {st.session_state.card % len(deck) + 1}/"
                f"{len(deck)} · {st.session_state.known} known</div>", unsafe_allow_html=True)
    bg, fg = ("#F5E9D3", "#1B1A17") if flipped else ("#1B1A17", "#F3EFE6")
    st.markdown(
        f"<div class='flashcard' style='background:{bg};color:{fg}'>"
        f"<div class='side'>{'ANSWER' if flipped else 'QUESTION'}</div>"
        f"<div class='text'>{esc(card['a'] if flipped else card['q'])}</div></div>",
        unsafe_allow_html=True,
    )
    st.button("Flip card", on_click=flip, use_container_width=True)
    c1, c2 = st.columns(2)
    c1.button("Study again", on_click=next_card, args=(False,), use_container_width=True)
    c2.button("Got it", on_click=next_card, args=(True,), type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# 4) Study materials: what's posted on Canvas + AI study guides
# ---------------------------------------------------------------------------
st.markdown("## Study materials")
if not courses:
    st.caption("No active courses found on Canvas.")
    st.stop()

course = st.selectbox("Course", courses, format_func=lambda c: c["name"])
color, tint = color_of[course["id"]]
try:
    mats = materials_for(course)
except CanvasError as exc:
    st.error(str(exc))
    mats = []

if not mats:
    st.caption("Nothing posted in this course's modules yet (or your teacher hides them).")
cols = st.columns(3)
for i, m in enumerate(mats):
    if m.get("text"):
        readable = "AI can read this"
    elif m.get("pdf_bytes"):
        readable = "scanned PDF, Gemini reads the pages"
    else:
        readable = "open to view"
    title = esc(m["title"])
    if m.get("url"):
        title = f"<a href='{esc(m['url'])}' target='_blank' style='color:#1B1A17'>{title}</a>"
    cols[i % 3].markdown(
        f"<div class='mat'><div class='ftype' style='background:{tint};color:{color}'>{esc(m['type'])}</div>"
        f"<div><b>{title}</b><div class='muted'>{esc(m.get('module') or '')} · {readable}</div></div></div>",
        unsafe_allow_html=True,
    )

st.markdown("### AI study guide")
course_rows = [r for r in rows if r["course_id"] == course["id"] and r["hours"] >= 0]
course_rows.sort(key=lambda r: (not r["is_test"], r["due"]))
if not ai_study.ai_available():
    st.caption("Set GOOGLE_CLOUD_PROJECT and restart to build study guides with Gemini.")
elif not course_rows:
    st.caption("No upcoming work in this course to build a guide for.")
else:
    target = st.selectbox("Build a guide for", course_rows,
                          format_func=lambda r: f"{'Test: ' if r['is_test'] else ''}{r['name']} ({when_text(r['hours'])})")
    if st.button("Build study guide from Canvas materials", type="primary"):
        run_pack(target)

    pack = data["packs"].get(target["id"])
    if pack:
        t1, t2, t3, t4 = st.tabs(["Summary", "Key concepts", "Practice questions", "Study plan"])
        with t1:
            st.write(pack.get("summary", ""))
            st.caption(f"{len(pack.get('flashcards', []))} flashcards are loaded in the Flashcards panel.")
        with t2:
            for k in pack.get("key_concepts", []):
                src = f"  \n*Source: {k['source']}*" if k.get("source") else ""
                st.markdown(f"**{k.get('term', '')}**: {k.get('explanation', '')}{src}")
        with t3:
            for n, q in enumerate(pack.get("practice_questions", []), 1):
                st.markdown(f"**{n}. {q.get('question', '')}**")
                with st.expander("Show answer"):
                    st.write(q.get("answer", ""))
        with t4:
            for step in pack.get("study_plan", []):
                st.markdown(f"- {step}")
