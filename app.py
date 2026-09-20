import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

import streamlit as st
from openai import OpenAI
from pypdf import PdfReader

st.set_page_config(page_title="GSU AI Academic Advisor", page_icon="🎓", layout="wide")

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
    out, seen = [], set()
    for course in req["core_requirement"]["courses"] + req["concentrations"][concentration]["courses"] + [req["internship"]["course"]]:
        if course["code"] not in seen:
            out.append(course)
            seen.add(course["code"])
    return out

def course_lookup(concentration):
    return {c["code"]: c for c in all_course_options(concentration)}

def course_label(course):
    return f'{course["code"]} — {course["name"]}'

def extract_pdf_text(uploaded_file):
    uploaded_file.seek(0)
    reader = PdfReader(uploaded_file)
    return "\n".join((page.extract_text() or "") for page in reader.pages)

def classify_course_line(line):
    upper = f" {line.upper()} "
    current_markers = [" IN PROGRESS ", " IN-PROGRESS ", " IP ", " CURRENTLY ENROLLED ", " REGISTERED "]
    if any(x in upper for x in current_markers):
        return "current"
    grade_pattern = r'(?<![A-Z0-9])(?:A\+|A-|A|B\+|B-|B|C\+|C-|C|D\+|D-|D|F|P|S|U|CR)(?![A-Z0-9])'
    if re.search(grade_pattern, upper):
        return "completed"
    if any(x in upper for x in ["COMPLETE", "COMPLETED", "SATISFIED", "PASSED"]):
        return "completed"
    return "unknown"

def detect_courses(text, known_codes):
    known = {c.upper() for c in known_codes}
    pattern = re.compile(r'\b(CIS)\s*[-:]?\s*(\d{4})\b', re.I)
    results, other = {}, {}
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        for dept, number in pattern.findall(line):
            code = f"{dept.upper()} {number}"
            status = classify_course_line(line)
            target = results if code.upper() in known else other
            prev = target.get(code)
            if prev is None or (prev["status"] == "unknown" and status != "unknown"):
                target[code] = {"status": status, "line": line}
    return results, other

def missing_prereqs(course, completed):
    prereqs = course.get("prerequisites", [])
    if course.get("prereq_status") != "verified":
        return [], True
    return [p for p in prereqs if p not in completed], False

def remaining_plan(concentration, completed, current):
    done = set(completed) | set(current)
    completed_only = set(completed)
    core_courses = req["core_requirement"]["courses"]
    core_done = [c for c in core_courses if c["code"] in done]
    core_needed = max(0, req["core_requirement"]["choose"] - len(core_done))
    available_core = [c for c in core_courses if c["code"] not in done]

    conc = req["concentrations"][concentration]
    conc_done = [c for c in conc["courses"] if c["code"] in done]
    remaining_conc = [c for c in conc["courses"] if c["code"] not in done]
    conc_needed = max(0, conc.get("choose", len(conc["courses"])) - len(conc_done)) if conc["mode"] == "choose_n" else len(remaining_conc)

    internship = req["internship"]["course"]
    internship_remaining = internship["code"] not in done

    # Prerequisite-aware eligibility.
    blocked, eligible_core, eligible_conc = [], [], []
    for category, courses in [("Core", available_core), ("Concentration", remaining_conc)]:
        for course in courses:
            missing, unverified = missing_prereqs(course, completed_only)
            item = {"category": category, **course, "missing_prereqs": missing, "prereq_unverified": unverified}
            if missing:
                blocked.append(item)
            elif category == "Core":
                eligible_core.append(item)
            else:
                eligible_conc.append(item)

    internship_item = {"category": "Internship", **internship}
    missing, unverified = missing_prereqs(internship, completed_only)
    internship_item["missing_prereqs"] = missing
    internship_item["prereq_unverified"] = unverified

    return {
        "core_done": core_done,
        "core_needed_count": core_needed,
        "eligible_core": eligible_core,
        "conc_done": conc_done,
        "conc_needed_count": conc_needed,
        "eligible_conc": eligible_conc,
        "blocked": blocked,
        "internship_remaining": internship_remaining,
        "internship_item": internship_item,
        "directed_elective": conc.get("directed_elective", False),
    }

def next_terms(start_term, start_year, count, include_summer):
    order = ["Spring", "Summer", "Fall"] if include_summer else ["Spring", "Fall"]
    term = start_term
    year = start_year
    result = []

    for _ in range(count):
        result.append((term, year))
        idx = order.index(term)
        next_idx = (idx + 1) % len(order)
        next_term = order[next_idx]
        if next_idx <= idx:
            year += 1
        term = next_term
    return result

def planning_queue(plan):
    queue = []
    queue.extend(plan["eligible_core"][:plan["core_needed_count"]])
    queue.extend(plan["eligible_conc"][:plan["conc_needed_count"]])
    if plan["internship_remaining"]:
        queue.append(plan["internship_item"])
    return queue

def build_semester_plan(plan, course_load, start_term, start_year, include_summer):
    queue = planning_queue(plan)
    terms_needed = max(1, math.ceil(len(queue) / course_load)) if queue else 0
    terms = next_terms(start_term, start_year, terms_needed, include_summer) if terms_needed else []

    semester_rows = []
    idx = 0
    for term, year in terms:
        term_courses = []
        for _ in range(course_load):
            if idx >= len(queue):
                break
            course = queue[idx]
            term_courses.append(course)
            idx += 1
        semester_rows.append({"term": f"{term} {year}", "courses": term_courses})

    return semester_rows

def fallback_response(question, concentration, completed, current, course_load, work_status, schedule_pref, plan, semester_rows):
    q = question.lower()
    recs = [c for row in semester_rows for c in row["courses"]]
    rec_names = ", ".join(c["code"] for c in recs[:course_load]) or "no additional modeled courses"

    if "prereq" in q or "eligible" in q:
        if plan["blocked"]:
            details = "; ".join(f'{x["code"]} requires {", ".join(x["missing_prereqs"])}' for x in plan["blocked"])
            return f"Based on the verified prerequisite rules in this prototype: {details}."
        return "No course is currently blocked by a verified prerequisite rule in this prototype. Some prerequisites are still marked unverified, so confirm them with a GSU academic advisor."

    if "graduate" in q or "finish" in q or "when" in q:
        if semester_rows:
            return (
                f"Your planning sequence currently runs through **{semester_rows[-1]['term']}** "
                f"at {course_load} course(s) per semester. This is only a planning estimate; "
                "actual graduation depends on official degree audit results, course availability, "
                "directed-elective rules, and advisor approval."
            )
        return "The modeled requirements currently appear accounted for. This does not constitute official graduation clearance."

    if "work" in q or "full-time" in q or "lighter" in q or "load" in q:
        return (
            f"Because you selected **{work_status}** employment and a load of **{course_load} course(s) per semester**, "
            f"the rule engine recommends starting with **{rec_names}**. Your schedule preference is **{schedule_pref}**. "
            "Course meeting times are not yet verified in this prototype."
        )

    if "next" in q or "take" in q or "recommend" in q:
        return (
            f"Based on the modeled degree requirements and your selected load, the next recommended course(s) are **{rec_names}**. "
            "The app prioritizes remaining core selections, then concentration requirements, then the internship. "
            "Unverified prerequisites and term availability should be confirmed with an advisor."
        )

    return (
        f"Based on your current profile, you have {plan['core_needed_count']} core selection(s) and "
        f"{plan['conc_needed_count']} concentration item(s) still represented as outstanding in this prototype. "
        f"Your next modeled course(s) are {rec_names}. Ask me about prerequisites, what to take next, "
        "your workload, or your estimated completion term."
    )

def advisor_context(concentration, completed, current, work_status, course_load, schedule_pref, plan, semester_rows):
    rows = []
    for row in semester_rows:
        rows.append(row["term"] + ": " + ", ".join(c["code"] for c in row["courses"]))
    return f"""
Program: {req["program"]}
Concentration: {concentration}
Completed: {completed}
In progress: {current}
Work status: {work_status}
Preferred load: {course_load}
Schedule preference: {schedule_pref}
Core selections left: {plan["core_needed_count"]}
Concentration items left: {plan["conc_needed_count"]}
Internship outstanding: {plan["internship_remaining"]}
Blocked by verified prerequisites: {[x["code"] for x in plan["blocked"]]}
Semester plan:
{chr(10).join(rows)}
Important: prerequisite and course availability information may be unverified. Do not invent missing rules.
""".strip()

def ask_ai(context, history):
    key = get_api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured")
    client = OpenAI(api_key=key)
    history_text = "\n".join(f'{m["role"].upper()}: {m["content"]}' for m in history[-10:])
    response = client.responses.create(
        model=MODEL,
        instructions=(
            "You are an academic-advising assistant for a classroom prototype. "
            "Use only the supplied program/rule-engine context for degree-rule claims. "
            "Never invent prerequisites, course availability, waivers, transfer-credit decisions, "
            "or official graduation clearance. Be concise and explain uncertainty."
        ),
        input=f"{context}\n\nConversation:\n{history_text}",
        store=False,
    )
    return response.output_text

# ---------------- Session ----------------
for key, default in {
    "messages": [],
    "pdf_completed": [],
    "pdf_current": [],
    "pdf_unknown": [],
    "last_uploaded_name": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------- UI ----------------
st.title("🎓 GSU AI Academic Advisor")
st.caption("Student Project Prototype — MS Information Systems")

st.warning(
    "Prototype only. This is not an official degree audit, registration, or graduation-clearance system."
)

with st.sidebar:
    st.header("Curriculum source")
    st.write("Robinson College of Business — Information Systems, M.S.")
    st.link_button("Open Robinson MSIS page", req["source_url"])
    st.write("GenAI:", "✅ configured" if get_api_key() else "⚠️ fallback mode")

st.subheader("1. Student profile")
c1, c2 = st.columns(2)
with c1:
    concentration = st.selectbox("MSIS concentration", list(req["concentrations"].keys()))
    work_status = st.selectbox("Employment status", ["Not working", "Part-time", "Full-time"])
    course_load = st.selectbox("Preferred courses per semester", [1, 2, 3, 4], index=1)
with c2:
    schedule_pref = st.selectbox("Preferred schedule", ["No preference", "Evening", "Daytime", "Online/Hybrid"])
    start_term = st.selectbox("Planning starts", ["Spring", "Fall", "Summer"], index=1)
    start_year = st.number_input("Start year", min_value=2026, max_value=2035, value=2026, step=1)
    include_summer = st.checkbox("Include Summer semesters", value=False)

st.subheader("2. Academic record")
options = all_course_options(concentration)
labels = {course_label(c): c["code"] for c in options}
code_to_label = {v: k for k, v in labels.items()}
label_list = list(labels.keys())

uploaded = st.file_uploader("Upload transcript / Degree Works PDF", type=["pdf"])

if uploaded and st.session_state.last_uploaded_name != uploaded.name:
    try:
        text = extract_pdf_text(uploaded)
        if text.strip():
            detected, _ = detect_courses(text, list(code_to_label.keys()))
            st.session_state.pdf_completed = sorted([c for c, x in detected.items() if x["status"] == "completed"])
            st.session_state.pdf_current = sorted([c for c, x in detected.items() if x["status"] == "current"])
            st.session_state.pdf_unknown = sorted([c for c, x in detected.items() if x["status"] == "unknown"])
            st.session_state.last_uploaded_name = uploaded.name
            st.success(f"Detected {len(detected)} modeled MSIS course(s). Review them below.")
        else:
            st.error("No selectable text was found in the PDF. Enter courses manually.")
    except Exception as e:
        st.error(f"Could not read the PDF: {e}")

if st.session_state.pdf_unknown:
    st.warning("Needs manual review: " + ", ".join(st.session_state.pdf_unknown))

default_completed = [code_to_label[c] for c in st.session_state.pdf_completed if c in code_to_label]
completed_labels = st.multiselect("Completed courses", label_list, default=default_completed)
completed = [labels[x] for x in completed_labels]

current_choices = [x for x in label_list if labels[x] not in completed]
default_current = [code_to_label[c] for c in st.session_state.pdf_current if c in code_to_label and code_to_label[c] in current_choices]
current_labels = st.multiselect("Courses currently in progress", current_choices, default=default_current)
current = [labels[x] for x in current_labels]

plan = remaining_plan(concentration, completed, current)
semester_rows = build_semester_plan(plan, course_load, start_term, int(start_year), include_summer)

st.subheader("3. Prerequisite check")
if plan["blocked"]:
    for item in plan["blocked"]:
        st.error(f'{item["code"]} blocked — missing: {", ".join(item["missing_prereqs"])}')
else:
    st.success("No course is blocked by a verified prerequisite rule in the current dataset.")

unverified = [c for c in all_course_options(concentration) if c.get("prereq_status") != "verified"]
if unverified:
    st.info(
        f"Prerequisites are still unverified for {len(unverified)} modeled course(s). "
        "The planner does not invent prerequisite rules; verify them before registration."
    )

st.subheader("4. Semester-by-semester plan")
if semester_rows:
    for row in semester_rows:
        with st.container(border=True):
            st.markdown(f"### {row['term']}")
            for c in row["courses"]:
                st.write(f"**{c['code']}** — {c['name']} · {c['category']}")
                if not c.get("availability"):
                    st.caption("Course offering for this term has not been verified.")
    st.caption(
        f"Planning estimate through {semester_rows[-1]['term']}. "
        "This is not an official graduation date."
    )
else:
    st.success("No additional modeled courses are currently queued.")

st.subheader("5. 💬 Ask the Academic Advisor")
if not get_api_key():
    st.info("GenAI API is not configured. The advisor will automatically use rule-based fallback responses.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

question = st.chat_input("Ask about what to take next, prerequisites, workload, or estimated completion.")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    context = advisor_context(concentration, completed, current, work_status, course_load, schedule_pref, plan, semester_rows)

    with st.chat_message("assistant"):
        with st.spinner("Reviewing your plan..."):
            try:
                answer = ask_ai(context, st.session_state.messages)
                source = "GenAI"
            except Exception:
                answer = fallback_response(
                    question, concentration, completed, current, course_load,
                    work_status, schedule_pref, plan, semester_rows
                )
                source = "Rule-based fallback"
            st.markdown(answer)
            st.caption(f"Response mode: {source}")

    st.session_state.messages.append({"role": "assistant", "content": answer})

if st.session_state.messages and st.button("Clear advisor chat"):
    st.session_state.messages = []
    st.rerun()
