import io
import json
import math
import os
import re
from pathlib import Path

import streamlit as st
from openai import OpenAI
from pypdf import PdfReader

st.set_page_config(
    page_title="GSU AI Academic Advisor",
    page_icon="🎓",
    layout="wide"
)

DATA_FILE = Path("data/gsu_msis_requirements.json")
MODEL = "gpt-5.6-luna"

@st.cache_data
def load_requirements():
    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)

req = load_requirements()

def get_api_key():
    try:
        return st.secrets["OPENAI_API_KEY"]
    except Exception:
        return os.getenv("OPENAI_API_KEY")

def all_course_options(concentration):
    items = []
    seen = set()

    for c in req["core_requirement"]["courses"]:
        if c["code"] not in seen:
            items.append(c)
            seen.add(c["code"])

    for c in req["concentrations"][concentration]["courses"]:
        if c["code"] not in seen:
            items.append(c)
            seen.add(c["code"])

    internship = req["internship"]["course"]
    if internship["code"] not in seen:
        items.append(internship)

    return items

def course_label(course):
    return f'{course["code"]} — {course["name"]}'

def extract_pdf_text(uploaded_file):
    """Extract embedded text from a text-based PDF."""
    uploaded_file.seek(0)
    reader = PdfReader(uploaded_file)
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append(text)
    return "\n".join(pages)

def normalize_course_code(dept, number):
    return f"{dept.upper()} {number}"

def classify_course_line(line):
    """
    Heuristic classification for transcripts / Degree Works.
    Returns: 'completed', 'current', or 'unknown'
    """
    upper = line.upper()

    # Common in-progress markers.
    current_markers = [
        " IN PROGRESS", "IN-PROGRESS", " IP ", "CURRENTLY ENROLLED",
        "CURRENT", "REGISTERED"
    ]
    if any(marker in f" {upper} " for marker in current_markers):
        return "current"

    # Common completed-grade markers.
    # The boundaries reduce false positives from ordinary words.
    grade_pattern = r'(?<![A-Z0-9])(?:A\+|A-|A|B\+|B-|B|C\+|C-|C|D\+|D-|D|F|P|S|U|CR)(?![A-Z0-9])'
    if re.search(grade_pattern, upper):
        return "completed"

    # Degree Works often uses "COMPLETE" language.
    completed_markers = [
        "COMPLETE", "COMPLETED", "SATISFIED", "PASSED"
    ]
    if any(marker in upper for marker in completed_markers):
        return "completed"

    return "unknown"

def detect_courses_from_text(text, known_course_codes):
    """
    Finds course codes in extracted PDF text and tries to classify them.
    Known GSU MSIS courses are prioritized, but other CIS courses are also reported.
    """
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]

    known_upper = {c.upper() for c in known_course_codes}
    results = {}
    other_cis = {}

    # Accept forms like CIS 8080, CIS-8080, CIS8080.
    pattern = re.compile(r'\b(CIS)\s*[-:]?\s*(\d{4})\b', re.I)

    for line in lines:
        matches = pattern.findall(line)
        for dept, number in matches:
            code = normalize_course_code(dept, number)
            status = classify_course_line(line)
            target = results if code.upper() in known_upper else other_cis

            # Prefer stronger classifications over unknown.
            prev = target.get(code)
            if prev is None:
                target[code] = {"status": status, "line": line}
            elif prev["status"] == "unknown" and status != "unknown":
                target[code] = {"status": status, "line": line}
            elif prev["status"] == "current" and status == "completed":
                # If the same course appears as both, completed wins.
                target[code] = {"status": status, "line": line}

    return results, other_cis

def remaining_plan(concentration, completed, current):
    done = set(completed) | set(current)

    core_courses = req["core_requirement"]["courses"]
    core_done = [c for c in core_courses if c["code"] in done]
    core_needed_count = max(0, req["core_requirement"]["choose"] - len(core_done))
    available_core = [c for c in core_courses if c["code"] not in done]

    conc = req["concentrations"][concentration]
    conc_courses = conc["courses"]
    conc_done = [c for c in conc_courses if c["code"] in done]

    if conc["mode"] == "choose_n":
        conc_needed_count = max(0, conc["choose"] - len(conc_done))
        remaining_conc = [c for c in conc_courses if c["code"] not in done]
    else:
        remaining_conc = [c for c in conc_courses if c["code"] not in done]
        conc_needed_count = len(remaining_conc)

    internship = req["internship"]["course"]
    internship_remaining = internship["code"] not in done

    return {
        "core_done": core_done,
        "core_needed_count": core_needed_count,
        "available_core": available_core,
        "conc_done": conc_done,
        "conc_needed_count": conc_needed_count,
        "remaining_conc": remaining_conc,
        "internship_remaining": internship_remaining,
        "internship": internship,
        "directed_elective": conc.get("directed_elective", False),
    }

def build_recommendations(plan, course_load):
    recs = []

    core_added = 0
    for c in plan["available_core"]:
        if len(recs) >= course_load or core_added >= plan["core_needed_count"]:
            break
        recs.append({"category": "Core", **c})
        core_added += 1

    conc_added = 0
    for c in plan["remaining_conc"]:
        if len(recs) >= course_load or conc_added >= plan["conc_needed_count"]:
            break
        recs.append({"category": "Concentration", **c})
        conc_added += 1

    if plan["internship_remaining"] and len(recs) < course_load:
        recs.append({"category": "Internship", **plan["internship"]})

    return recs

def make_advisor_context(
    concentration,
    completed,
    current,
    work_status,
    course_load,
    schedule_pref,
    current_term,
    extra,
    plan,
    recommendations,
):
    conc = req["concentrations"][concentration]

    core_list = "\n".join(
        f'- {c["code"]}: {c["name"]}'
        for c in req["core_requirement"]["courses"]
    )
    concentration_list = "\n".join(
        f'- {c["code"]}: {c["name"]}'
        for c in conc["courses"]
    )
    recommendation_list = "\n".join(
        f'- {r["code"]}: {r["name"]} ({r["category"]})'
        for r in recommendations
    ) or "- None generated"

    return f"""
VERIFIED PROGRAM DATA
Institution: {req["institution"]}
College: {req["college"]}
Program: {req["program"]}
Concentration selected: {concentration}
Source: {req["source_url"]}

MSIS core rule:
Choose {req["core_requirement"]["choose"]} of these courses:
{core_list}

Selected concentration courses shown in the verified dataset:
{concentration_list}

Concentration displayed hours: {conc["displayed_hours"]}
Directed elective listed: {conc.get("directed_elective", False)}

Internship:
{req["internship"]["course"]["code"]}: {req["internship"]["course"]["name"]}

STUDENT PROFILE
Current term: {current_term}
Completed courses: {", ".join(completed) if completed else "None selected"}
Courses in progress: {", ".join(current) if current else "None selected"}
Employment status: {work_status}
Preferred course load: {course_load} course(s) per semester
Schedule preference: {schedule_pref}
Additional constraints: {extra if extra.strip() else "None provided"}

RULE-ENGINE RESULTS
Core selections still needed: {plan["core_needed_count"]}
Concentration items still represented as outstanding: {plan["conc_needed_count"]}
Internship still outstanding: {plan["internship_remaining"]}
Directed elective requires advisor verification: {plan["directed_elective"]}

Current rule-engine recommendations:
{recommendation_list}
""".strip()

ADVISOR_INSTRUCTIONS = """
You are the AI Academic Advisor for a classroom prototype focused on Georgia State
University's MS Information Systems program.

Use the verified program data and rule-engine results supplied by the application.
Never invent degree requirements, prerequisites, course availability, transfer-credit
decisions, waivers, or official graduation clearance.

You may personalize explanations using employment status, desired course load,
schedule preference, and other stated constraints.

If information is not verified in the application, say so and recommend confirmation
with a GSU academic advisor.

Do not request sensitive identifiers such as SSNs, passwords, PantherCard credentials,
or banking information.
""".strip()

def ask_ai(context, chat_history):
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured. Add it to Streamlit secrets.")

    client = OpenAI(api_key=api_key)

    history_text = ""
    for msg in chat_history[-10:]:
        history_text += f'\n{msg["role"].upper()}: {msg["content"]}\n'

    response = client.responses.create(
        model=MODEL,
        instructions=ADVISOR_INSTRUCTIONS,
        input=f"""
ACADEMIC ADVISING CONTEXT
{context}

RECENT CONVERSATION
{history_text}

Answer the latest USER message using only the verified academic context above
for program-rule claims.
""".strip(),
        store=False,
    )

    return response.output_text

# -------------------- Session state --------------------

if "messages" not in st.session_state:
    st.session_state.messages = []

if "pdf_completed" not in st.session_state:
    st.session_state.pdf_completed = []

if "pdf_current" not in st.session_state:
    st.session_state.pdf_current = []

if "pdf_unknown" not in st.session_state:
    st.session_state.pdf_unknown = []

if "pdf_other_cis" not in st.session_state:
    st.session_state.pdf_other_cis = {}

if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None

# -------------------- UI --------------------

st.title("🎓 GSU AI Academic Advisor")
st.caption("Student Project Prototype — Georgia State University MS Information Systems")

st.warning(
    "Prototype only. This is not an official GSU advising, Degree Works, registration, "
    "or graduation-clearance system. Always review detected courses and verify final decisions "
    "with an academic advisor."
)

with st.sidebar:
    st.header("Curriculum source")
    st.write("Robinson College of Business — Information Systems, M.S.")
    st.write("Data checked: 2026-09-20")
    st.link_button("Open Robinson MSIS page", req["source_url"])

    if get_api_key():
        st.success("GenAI connection configured")
    else:
        st.error("GenAI API key not configured")

st.subheader("1. Student profile")

c1, c2 = st.columns(2)

with c1:
    name = st.text_input("Name (optional)")
    concentration = st.selectbox(
        "MSIS concentration",
        list(req["concentrations"].keys())
    )
    work_status = st.selectbox(
        "Employment status",
        ["Not working", "Part-time", "Full-time"]
    )

with c2:
    course_load = st.selectbox(
        "Preferred courses per semester",
        [1, 2, 3, 4],
        index=1
    )
    schedule_pref = st.selectbox(
        "Preferred schedule",
        ["No preference", "Evening", "Daytime", "Online/Hybrid"]
    )
    current_term = st.text_input("Current term", value="Fall 2026")

st.subheader("2. Academic record")

options = all_course_options(concentration)
labels = {course_label(c): c["code"] for c in options}
code_to_label = {v: k for k, v in labels.items()}
label_list = list(labels.keys())

uploaded = st.file_uploader(
    "Upload transcript / Degree Works PDF",
    type=["pdf"],
    help="The app reads text-based PDFs. Scanned/image-only PDFs may need OCR in a later version."
)

known_codes = list(code_to_label.keys())

if uploaded is not None:
    # Parse only when a new file is selected.
    if st.session_state.last_uploaded_name != uploaded.name:
        try:
            text = extract_pdf_text(uploaded)

            if not text.strip():
                st.session_state.pdf_completed = []
                st.session_state.pdf_current = []
                st.session_state.pdf_unknown = []
                st.session_state.pdf_other_cis = {}
                st.error(
                    "I could not extract text from this PDF. It may be an image/scanned PDF. "
                    "For now, enter courses manually."
                )
            else:
                detected, other_cis = detect_courses_from_text(text, known_codes)

                completed = []
                current = []
                unknown = []

                for code, info in detected.items():
                    if info["status"] == "completed":
                        completed.append(code)
                    elif info["status"] == "current":
                        current.append(code)
                    else:
                        unknown.append(code)

                st.session_state.pdf_completed = sorted(completed)
                st.session_state.pdf_current = sorted(current)
                st.session_state.pdf_unknown = sorted(unknown)
                st.session_state.pdf_other_cis = other_cis
                st.session_state.last_uploaded_name = uploaded.name

                st.success(
                    f"PDF analyzed: detected {len(detected)} known MSIS course(s). "
                    "Review the selections below before building your plan."
                )

        except Exception as e:
            st.error(f"Could not read the PDF: {e}")

# Show detection summary.
if st.session_state.pdf_completed or st.session_state.pdf_current or st.session_state.pdf_unknown:
    st.markdown("#### PDF detection results")

    d1, d2, d3 = st.columns(3)
    d1.metric("Likely completed", len(st.session_state.pdf_completed))
    d2.metric("Likely in progress", len(st.session_state.pdf_current))
    d3.metric("Needs review", len(st.session_state.pdf_unknown))

    if st.session_state.pdf_unknown:
        st.warning(
            "These detected courses could not be confidently classified as completed or in progress: "
            + ", ".join(st.session_state.pdf_unknown)
        )

if st.session_state.pdf_other_cis:
    with st.expander("Other CIS courses detected in the PDF"):
        st.write(
            "These CIS courses were found but are not part of the currently modeled concentration/core list. "
            "They may be electives, transfer-equivalent courses, or courses outside this prototype."
        )
        for code, info in st.session_state.pdf_other_cis.items():
            st.write(f"• **{code}** — detected status: {info['status']}")

default_completed_labels = [
    code_to_label[c]
    for c in st.session_state.pdf_completed
    if c in code_to_label
]

# Unknown courses are not automatically marked completed.
completed_labels = st.multiselect(
    "Completed courses",
    label_list,
    default=default_completed_labels,
    help="Automatically detected courses are preselected. Correct anything the PDF parser got wrong."
)
completed = [labels[x] for x in completed_labels]

current_choices = [x for x in label_list if labels[x] not in completed]
default_current_labels = [
    code_to_label[c]
    for c in st.session_state.pdf_current
    if c in code_to_label and code_to_label[c] in current_choices
]

current_labels = st.multiselect(
    "Courses currently in progress",
    current_choices,
    default=default_current_labels,
    help="Review these carefully. PDF status detection is heuristic."
)
current = [labels[x] for x in current_labels]

extra = st.text_area(
    "Additional constraints",
    placeholder="Example: I work Monday-Friday 8 AM-5 PM and prefer two evening courses."
)

plan = remaining_plan(concentration, completed, current)
recommendations = build_recommendations(plan, course_load)

st.subheader("3. Academic plan")

if st.button("Build My Academic Plan", type="primary", use_container_width=True):
    st.session_state["plan_built"] = True

if st.session_state.get("plan_built"):
    st.success("Plan generated from the verified MSIS curriculum structure.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Completed", len(completed))
    m2.metric("In progress", len(current))
    m3.metric("Core slots left", plan["core_needed_count"])
    m4.metric("Concentration items left", plan["conc_needed_count"])

    st.divider()
    left, right = st.columns(2)

    with left:
        st.markdown("### Recommended next courses")
        if recommendations:
            for item in recommendations:
                st.write(f'**{item["code"]}** — {item["name"]}')
                st.caption(item["category"])
        else:
            st.write("No additional listed course recommendation was generated.")

        st.markdown("### Personalized considerations")
        st.write(f"• Preferred load: **{course_load} course(s) per semester**")
        st.write(f"• Employment status: **{work_status}**")
        st.write(f"• Schedule preference: **{schedule_pref}**")
        if extra.strip():
            st.write(f"• Student note: {extra}")

    with right:
        st.markdown("### Degree progress")

        st.write(
            f'**MSIS core:** {len(plan["core_done"])} of '
            f'{req["core_requirement"]["choose"]} required core selections accounted for.'
        )

        conc = req["concentrations"][concentration]
        if conc["mode"] == "choose_n":
            st.write(
                f'**{concentration}:** {len(plan["conc_done"])} of '
                f'{conc["choose"]} required concentration selections accounted for.'
            )
        else:
            st.write(
                f'**{concentration}:** {len(plan["conc_done"])} listed concentration '
                f'courses accounted for; the public program page displays a '
                f'{conc["displayed_hours"]}-hour concentration core.'
            )

        internship_code = req["internship"]["course"]["code"]
        if plan["internship_remaining"]:
            st.write(f"**Internship:** {internship_code} still appears outstanding.")
        else:
            st.write(f"**Internship:** {internship_code} accounted for.")

        if plan["directed_elective"]:
            st.warning(
                "A Directed Elective is also listed for this concentration. "
                "The prototype flags this for advisor verification rather than guessing."
            )

    listed_remaining = (
        plan["core_needed_count"]
        + plan["conc_needed_count"]
        + (1 if plan["internship_remaining"] else 0)
    )

    if listed_remaining > 0:
        est_terms = math.ceil(listed_remaining / course_load)
        st.markdown("### Planning estimate")
        st.write(
            f"At **{course_load} course(s) per semester**, the currently modeled "
            f"requirements would take approximately **{est_terms} additional semester(s)**."
        )
        st.caption(
            "This is not an official graduation date and does not yet account for "
            "actual course availability, transfer-credit decisions, or unverified prerequisites."
        )
    else:
        st.success("All requirements represented in this prototype appear accounted for.")

st.divider()
st.subheader("4. 💬 Ask the AI Academic Advisor")

if not get_api_key():
    st.info("Add OPENAI_API_KEY to your Streamlit secrets to enable the advisor chat.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

question = st.chat_input(
    "Ask: What should I take next? Can I take a lighter load? When might I finish?"
)

if question:
    st.session_state.messages.append({"role": "user", "content": question})

    with st.chat_message("user"):
        st.markdown(question)

    context = make_advisor_context(
        concentration=concentration,
        completed=completed,
        current=current,
        work_status=work_status,
        course_load=course_load,
        schedule_pref=schedule_pref,
        current_term=current_term,
        extra=extra,
        plan=plan,
        recommendations=recommendations,
    )

    with st.chat_message("assistant"):
        with st.spinner("Reviewing your academic plan..."):
            try:
                answer = ask_ai(context, st.session_state.messages)
            except Exception as e:
                answer = (
                    "I couldn't connect to the GenAI service. "
                    f"Technical detail: {e}"
                )
            st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})

if st.session_state.messages:
    if st.button("Clear advisor chat"):
        st.session_state.messages = []
        st.rerun()
