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

# -------------------------------------------------------------------
# Basic helpers
# -------------------------------------------------------------------

def get_api_key():
    try:
        return st.secrets["OPENAI_API_KEY"]
    except Exception:
        return os.getenv("OPENAI_API_KEY")

def shared_core_courses():
    return req["core_requirement"]["courses"]

def concentration_courses(concentration):
    return req["concentrations"][concentration]["courses"]

def internship_course():
    return req["internship"]["course"]

def relevant_courses(concentration):
    """
    Only the common MSIS core + the selected concentration + internship.
    Courses from other concentrations are never shown in the student's
    Academic Record selectors.
    """
    items = []
    seen = set()
    for course in shared_core_courses() + concentration_courses(concentration) + [internship_course()]:
        if course["code"] not in seen:
            items.append(course)
            seen.add(course["code"])
    return items

def course_label(course):
    return f'{course["code"]} — {course["name"]}'

def course_map(concentration):
    return {c["code"]: c for c in relevant_courses(concentration)}

# -------------------------------------------------------------------
# PDF parsing
# -------------------------------------------------------------------

def extract_pdf_text(uploaded_file):
    uploaded_file.seek(0)
    reader = PdfReader(uploaded_file)
    return "\n".join((page.extract_text() or "") for page in reader.pages)

def classify_course_line(line):
    upper = f" {line.upper()} "

    current_markers = [
        " IN PROGRESS ",
        " IN-PROGRESS ",
        " IP ",
        " CURRENTLY ENROLLED ",
        " REGISTERED ",
    ]
    if any(marker in upper for marker in current_markers):
        return "current"

    grade_pattern = r'(?<![A-Z0-9])(?:A\+|A-|A|B\+|B-|B|C\+|C-|C|D\+|D-|D|F|P|S|U|CR)(?![A-Z0-9])'
    if re.search(grade_pattern, upper):
        return "completed"

    if any(marker in upper for marker in ["COMPLETE", "COMPLETED", "SATISFIED", "PASSED"]):
        return "completed"

    return "unknown"

def detect_courses(text, known_codes):
    known = {c.upper() for c in known_codes}
    pattern = re.compile(r'\b(CIS)\s*[-:]?\s*(\d{4})\b', re.I)

    relevant = {}
    other_cis = {}

    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue

        for dept, number in pattern.findall(line):
            code = f"{dept.upper()} {number}"
            status = classify_course_line(line)
            target = relevant if code.upper() in known else other_cis

            previous = target.get(code)
            if previous is None or (
                previous["status"] == "unknown" and status != "unknown"
            ):
                target[code] = {"status": status, "line": line}

    return relevant, other_cis

# -------------------------------------------------------------------
# Academic rule engine
# -------------------------------------------------------------------

def missing_prerequisites(course, completed):
    """
    Enforce a prerequisite only when the dataset explicitly marks it verified.
    """
    if course.get("prereq_status") != "verified":
        return [], True

    prerequisites = course.get("prerequisites", [])
    missing = [p for p in prerequisites if p not in completed]
    return missing, False

def build_rule_plan(concentration, completed, current):
    completed_set = set(completed)
    done = set(completed) | set(current)

    core = shared_core_courses()
    core_done = [c for c in core if c["code"] in done]
    core_needed = max(
        0,
        req["core_requirement"]["choose"] - len(core_done)
    )
    remaining_core = [c for c in core if c["code"] not in done]

    conc = req["concentrations"][concentration]
    conc_done = [c for c in conc["courses"] if c["code"] in done]
    remaining_conc = [
        c for c in conc["courses"] if c["code"] not in done
    ]

    if conc["mode"] == "choose_n":
        conc_needed = max(0, conc["choose"] - len(conc_done))
    else:
        conc_needed = len(remaining_conc)

    eligible_core = []
    eligible_concentration = []
    blocked = []

    for category, courses in [
        ("MSIS Core", remaining_core),
        (f"{concentration} Concentration", remaining_conc),
    ]:
        for course in courses:
            missing, unverified = missing_prerequisites(course, completed_set)

            item = {
                **course,
                "category": category,
                "missing_prerequisites": missing,
                "prerequisite_unverified": unverified,
            }

            if missing:
                blocked.append(item)
            elif category == "MSIS Core":
                eligible_core.append(item)
            else:
                eligible_concentration.append(item)

    internship = internship_course()
    internship_remaining = internship["code"] not in done
    missing, unverified = missing_prerequisites(internship, completed_set)

    internship_item = {
        **internship,
        "category": "Field Study / Internship",
        "missing_prerequisites": missing,
        "prerequisite_unverified": unverified,
    }

    return {
        "core_done": core_done,
        "core_needed_count": core_needed,
        "eligible_core": eligible_core,
        "concentration_done": conc_done,
        "concentration_needed_count": conc_needed,
        "eligible_concentration": eligible_concentration,
        "blocked": blocked,
        "internship_remaining": internship_remaining,
        "internship_item": internship_item,
        "directed_elective": conc.get("directed_elective", False),
    }

def planning_queue(plan):
    queue = []

    for course in plan["eligible_core"][:plan["core_needed_count"]]:
        queue.append({
            **course,
            "selection_reason": (
                "It fills one of the remaining shared MSIS core-selection slots."
            ),
        })

    for course in plan["eligible_concentration"][:plan["concentration_needed_count"]]:
        queue.append({
            **course,
            "selection_reason": (
                "It is a remaining course represented in the student's selected concentration."
            ),
        })

    if (
        plan["internship_remaining"]
        and not plan["internship_item"]["missing_prerequisites"]
    ):
        queue.append({
            **plan["internship_item"],
            "selection_reason": (
                "It represents the required CIS 8391 field-study component."
            ),
        })

    return queue

def next_terms(start_term, start_year, count, include_summer):
    order = ["Spring", "Summer", "Fall"] if include_summer else ["Spring", "Fall"]

    term = start_term
    year = start_year
    terms = []

    for _ in range(count):
        terms.append((term, year))

        current_index = order.index(term)
        next_index = (current_index + 1) % len(order)
        term = order[next_index]

        if next_index <= current_index:
            year += 1

    return terms

def build_semester_plan(
    plan,
    normal_load,
    next_semester_load,
    start_term,
    start_year,
    include_summer,
):
    queue = planning_queue(plan)

    if not queue:
        return []

    rows = []
    queue_index = 0
    term_index = 0

    for term, year in next_terms(
        start_term,
        start_year,
        len(queue) + 10,
        include_summer,
    ):
        if queue_index >= len(queue):
            break

        load = (
            next_semester_load
            if term_index == 0 and next_semester_load
            else normal_load
        )
        load = max(1, int(load))

        selected = []
        for _ in range(load):
            if queue_index >= len(queue):
                break
            selected.append(queue[queue_index])
            queue_index += 1

        rows.append({
            "term": f"{term} {year}",
            "courses": selected,
            "planned_load": load,
        })
        term_index += 1

    return rows

# -------------------------------------------------------------------
# Conversational planning-state parser
# -------------------------------------------------------------------

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
}

def find_course_count(message):
    match = re.search(
        r'\b(one|two|three|four|1|2|3|4)\s+'
        r'(?:course|courses|class|classes)\b',
        message,
        re.I,
    )
    if not match:
        return None
    return NUMBER_WORDS[match.group(1).lower()]

def apply_chat_overrides(message):
    """
    Natural-language chat changes update planning preferences, not degree rules.
    """
    text = message.lower()
    changes = []

    # Employment.
    if re.search(r"\b(?:i\s+)?work\s+full[- ]time\b", text):
        st.session_state.override_work_status = "Full-time"
        changes.append("Employment → Full-time")

    elif re.search(r"\b(?:i\s+)?work\s+part[- ]time\b", text):
        st.session_state.override_work_status = "Part-time"
        changes.append("Employment → Part-time")

    elif any(
        phrase in text
        for phrase in [
            "i don't work",
            "i do not work",
            "not working",
            "i am not working",
        ]
    ):
        st.session_state.override_work_status = "Not working"
        changes.append("Employment → Not working")

    # Summer planning.
    if any(
        phrase in text
        for phrase in [
            "include summer",
            "take summer classes",
            "use summer",
            "classes in summer",
        ]
    ):
        st.session_state.override_include_summer = True
        changes.append("Summer → Included")

    elif any(
        phrase in text
        for phrase in [
            "no summer",
            "skip summer",
            "don't include summer",
            "do not include summer",
        ]
    ):
        st.session_state.override_include_summer = False
        changes.append("Summer → Excluded")

    # Course-load changes.
    count = find_course_count(text)

    if count is not None:
        if any(
            phrase in text
            for phrase in [
                "next semester",
                "next term",
                "upcoming semester",
                "coming semester",
            ]
        ):
            st.session_state.override_next_semester_load = count
            changes.append(
                f"Next-semester load → {count} course(s)"
            )

        elif any(
            phrase in text
            for phrase in [
                "per semester",
                "each semester",
                "every semester",
                "per term",
                "each term",
            ]
        ):
            st.session_state.override_normal_load = count
            st.session_state.override_next_semester_load = None
            changes.append(
                f"Normal semester load → {count} course(s)"
            )

    return changes

def effective_profile(base_work, base_load, base_include_summer):
    return {
        "work_status": (
            st.session_state.override_work_status
            or base_work
        ),
        "normal_load": (
            st.session_state.override_normal_load
            or base_load
        ),
        "next_semester_load": (
            st.session_state.override_next_semester_load
        ),
        "include_summer": (
            st.session_state.override_include_summer
            if st.session_state.override_include_summer is not None
            else base_include_summer
        ),
    }

# -------------------------------------------------------------------
# Advisor clarification logic
# -------------------------------------------------------------------

def likely_needs_follow_up(question, profile):
    """
    Ask a focused follow-up when the question depends on a personal/policy fact
    the app does not know. This is intentionally conservative.
    """
    q = question.lower()

    enrollment_questions = any(
        phrase in q
        for phrase in [
            "am i allowed",
            "can i take one course",
            "minimum course",
            "full-time student",
            "part-time student",
            "maintain status",
            "visa",
            "f-1",
            "financial aid",
            "assistantship",
            "ga ",
            "graduate assistant",
        ]
    )

    if enrollment_questions:
        return (
            "Before I give you a more specific answer, which rule are you asking about: "
            "**academic program planning, F-1 enrollment/status, financial aid, or a graduate assistantship?** "
            "Those can have different minimum-enrollment requirements."
        )

    scheduling_questions = any(
        phrase in q
        for phrase in [
            "what time",
            "evening class",
            "online class",
            "which day",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "course offered",
            "available next semester",
        ]
    )

    if scheduling_questions:
        return (
            "Which **semester and year** do you want me to plan for? "
            "Exact class times and offerings must be checked against the official schedule."
        )

    return None

# -------------------------------------------------------------------
# Rule-based fallback answers
# -------------------------------------------------------------------

def first_course(semester_rows):
    if semester_rows and semester_rows[0]["courses"]:
        return semester_rows[0]["courses"][0]
    return None

def find_course(code, semester_rows):
    code = code.upper()
    for row_idx, row in enumerate(semester_rows):
        for course_idx, course in enumerate(row["courses"]):
            if course["code"].upper() == code:
                return row_idx, course_idx, row, course
    return None

def fallback_answer(
    question,
    concentration,
    profile,
    plan,
    semester_rows,
    changes,
):
    prefix = ""

    if changes:
        prefix = (
            "**I updated your planning scenario:** "
            + "; ".join(changes)
            + ".\n\n"
        )

    follow_up = likely_needs_follow_up(question, profile)

    # If a clarification is essential, ask it instead of guessing.
    if follow_up:
        return prefix + follow_up + (
            "\n\n**Verify:** Any official enrollment/status requirement with the appropriate GSU office."
        )

    q = question.lower()

    # Course-specific explanation.
    match = re.search(r'\bCIS\s*[-:]?\s*(\d{4})\b', question, re.I)
    if match:
        code = f"CIS {match.group(1)}"
        located = find_course(code, semester_rows)
        first = first_course(semester_rows)

        if "why" in q or "recommend" in q:
            if located:
                row_idx, course_idx, row, course = located
                if row_idx == 0 and course_idx == 0:
                    return prefix + (
                        f"**{code} is the first course in your current generated plan.** "
                        f"The rule engine put it there because {course['selection_reason'].lower()} "
                        "That ordering is a planning choice, not proof that GSU requires this course first.\n\n"
                        "**Verify:** prerequisite details and whether the course is actually offered in the planned term."
                    )

                return prefix + (
                    f"**{code} is in your current plan, but it is not the first recommendation.** "
                    f"It appears in **{row['term']}** because {course['selection_reason'].lower()} "
                    f"The current first recommendation is **{first['code'] if first else 'none'}**.\n\n"
                    "**Verify:** prerequisite details and term availability."
                )

            return prefix + (
                f"**{code} is not in your current generated plan.** "
                f"The current first recommendation is **{first['code'] if first else 'none'}**. "
                "I will not invent a reason for a recommendation the planner did not make."
            )

    if any(
        phrase in q
        for phrase in [
            "what should i take next",
            "what do i take next",
            "next course",
            "recommend next",
        ]
    ):
        if not semester_rows:
            return prefix + (
                "The current modeled requirements do not queue another course."
            )

        row = semester_rows[0]
        course_names = ", ".join(
            f"**{c['code']}**" for c in row["courses"]
        )
        reasons = "; ".join(
            f"{c['code']}: {c['selection_reason']}"
            for c in row["courses"]
        )

        return prefix + (
            f"For **{row['term']}**, the current planner recommends {course_names}. "
            f"{reasons}\n\n"
            "**Verify:** official prerequisites and actual course availability before registering."
        )

    if any(
        phrase in q
        for phrase in [
            "when will i finish",
            "when can i graduate",
            "graduation",
            "finish my degree",
            "complete my degree",
        ]
    ):
        if semester_rows:
            return prefix + (
                f"Under the current planning assumptions, your modeled sequence ends in "
                f"**{semester_rows[-1]['term']}**. This is a planning estimate, not official graduation clearance.\n\n"
                "**Verify:** Degree Works, course availability, prerequisites, directed-elective details, "
                "transfer/waiver decisions, and advisor approval."
            )

    if any(
        phrase in q
        for phrase in [
            "work full-time",
            "full-time job",
            "work part-time",
            "workload",
        ]
    ):
        return prefix + (
            f"I am using **{profile['work_status']}** employment and "
            f"**{profile['normal_load']} course(s) per normal semester** in your planning scenario. "
            "Employment affects the workload recommendation, but it does not change degree requirements.\n\n"
            "**Verify:** actual class meeting times and any enrollment rules that apply to your student status."
        )

    first = first_course(semester_rows)
    finish = semester_rows[-1]["term"] if semester_rows else "no additional modeled term"

    return prefix + (
        f"Here is what I can currently verify from the planner: your selected concentration is "
        f"**{concentration}**, your first modeled recommendation is "
        f"**{first['code'] if first else 'none'}**, and the current plan runs through "
        f"**{finish}**.\n\n"
        "I can help explain your requirements, workload, course sequence, estimated completion, "
        "and planning tradeoffs. If your question depends on an official policy or live course offering, "
        "I will ask for the missing detail or tell you what must be verified."
    )

# -------------------------------------------------------------------
# GenAI context and answer
# -------------------------------------------------------------------

def build_ai_context(
    concentration,
    completed,
    current,
    profile,
    plan,
    semester_rows,
    changes,
):
    common_core = "\n".join(
        f'- {c["code"]}: {c["name"]}'
        for c in shared_core_courses()
    )

    concentration_list = "\n".join(
        f'- {c["code"]}: {c["name"]}'
        for c in concentration_courses(concentration)
    )

    plan_lines = []
    for row in semester_rows:
        courses = []
        for c in row["courses"]:
            courses.append(
                f'{c["code"]} | {c["category"]} | reason={c["selection_reason"]} | '
                f'prereq={"unverified" if c.get("prerequisite_unverified") else "verified/cleared"}'
            )
        plan_lines.append(
            f'{row["term"]} ({row["planned_load"]} course load): '
            + " ; ".join(courses)
        )

    return f"""
ACADEMIC ADVISING CONTEXT

Institution: {req["institution"]}
Program: {req["program"]}
Selected concentration: {concentration}
Curriculum source: {req["source_url"]}

COMMON MSIS CORE
{common_core}

SELECTED CONCENTRATION COURSES ONLY
{concentration_list}

STUDENT ACADEMIC RECORD
Completed: {", ".join(completed) if completed else "None selected"}
In progress: {", ".join(current) if current else "None selected"}

ACTIVE PLANNING PROFILE
Employment: {profile["work_status"]}
Normal course load: {profile["normal_load"]}
Next-semester override: {profile["next_semester_load"] or "None"}
Summer included: {profile["include_summer"]}
Latest conversational changes: {changes or "None"}

RULE ENGINE
Core slots remaining: {plan["core_needed_count"]}
Selected-concentration items remaining: {plan["concentration_needed_count"]}
Internship outstanding: {plan["internship_remaining"]}
Directed-elective verification flag: {plan["directed_elective"]}
Verified prerequisite blocks: {[c["code"] for c in plan["blocked"]]}

CURRENT GENERATED PLAN
{chr(10).join(plan_lines) if plan_lines else "No additional modeled courses queued."}

LIMITATIONS
- Course availability by semester is not yet verified.
- Prerequisite information may be unverified.
- F-1, financial-aid, assistantship, registration, and minimum-enrollment rules are not stored as authoritative policy here.
- Official graduation clearance must come from GSU.
""".strip()

AI_INSTRUCTIONS = """
You are the conversational AI Academic Advisor in a Georgia State University MSIS classroom prototype.

GOAL
Help the student reason through academic planning in a natural conversation, like a helpful advisor.

HOW TO ANSWER
1. Answer the student's actual question first.
2. Use the supplied rule-engine context as authoritative for the CURRENT generated plan.
3. Use only the SELECTED CONCENTRATION section for concentration-specific course claims.
4. Shared MSIS core courses may apply across concentrations.
5. If the question is missing an important personal fact, ask ONE focused follow-up question that would materially improve the answer.
6. Do not ask unnecessary questions when you already have enough information.
7. When a student changes a preference in chat, use the ACTIVE PLANNING PROFILE supplied by the app.
8. Explain why a course was recommended using the exact selection reason from the CURRENT GENERATED PLAN.
9. Never invent a recommendation for a course that is not in the current plan.

LIVE VERIFICATION
10. When live web search is available, use it for current or policy-dependent facts such as:
    - current GSU program information
    - current course/program pages
    - deadlines
    - tuition/fees
    - current enrollment guidance
    - F-1/ISSS guidance
    - financial-aid or assistantship information
    - current office/contact information
    - facts the student explicitly asks you to verify
11. Prefer official Georgia State University sources. For federal immigration rules, official U.S. government sources may also be used.
12. Do not treat search snippets, blogs, Reddit, or unofficial pages as authoritative university policy.
13. If official sources conflict or are incomplete, say so clearly.
14. Live verification does not override the student's personal Degree Works record or official advisor decisions.

VERIFICATION BEHAVIOR
15. Clearly distinguish:
    a. facts verified from the stored curriculum/rule engine,
    b. facts verified live from official sources,
    c. planning assumptions,
    d. items that still need human/official confirmation.
16. When relevant, end with a short line beginning with **Verify:** naming the exact remaining item.
17. Do not claim official graduation clearance.
18. Do not invent prerequisites, course availability, transfer-credit decisions, waivers, registration eligibility,
    immigration/F-1 outcomes, financial-aid eligibility, or graduate-assistantship eligibility.

CONVERSATIONAL BEHAVIOR
19. You may answer broad academic-advising questions, not just course-plan questions.
20. If the student's question could mean different things, briefly explain the distinction and ask the single most useful follow-up.
21. Be practical and concise, but explain enough for the student to understand why.
22. If there is uncertainty, do not stop at "I can't verify." Give the useful reasoning you can, search official sources when appropriate, then state what remains to be confirmed.
""".strip()

OFFICIAL_VERIFICATION_DOMAINS = [
    "gsu.edu",
    "dhs.gov",
    "uscis.gov",
    "ed.gov",
]

def extract_web_sources(response):
    """
    Pull URL citations from Responses API output.
    Returns a de-duplicated list of {"title": ..., "url": ...}.
    """
    found = []
    seen = set()

    try:
        for item in response.output:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                for annotation in getattr(content, "annotations", []) or []:
                    if getattr(annotation, "type", None) != "url_citation":
                        continue

                    url = getattr(annotation, "url", None)
                    title = getattr(annotation, "title", None)

                    # SDK versions may nest citation details.
                    if not url:
                        citation = getattr(annotation, "url_citation", None)
                        url = getattr(citation, "url", None) if citation else None
                        title = title or (getattr(citation, "title", None) if citation else None)

                    if url and url not in seen:
                        seen.add(url)
                        found.append({
                            "title": title or url,
                            "url": url,
                        })
    except Exception:
        pass

    return found

def call_ai(context, history, live_verification=True):
    key = get_api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    client = OpenAI(api_key=key)

    history_text = "\n".join(
        f'{message["role"].upper()}: {message["content"]}'
        for message in history[-14:]
    )

    tools = []
    if live_verification:
        tools.append({
            "type": "web_search",
            "filters": {
                "allowed_domains": OFFICIAL_VERIFICATION_DOMAINS
            },
            "search_context_size": "medium",
        })

    response = client.responses.create(
        model=MODEL,
        instructions=AI_INSTRUCTIONS,
        tools=tools,
        input=f"""
{context}

RECENT CONVERSATION
{history_text}

LIVE VERIFICATION STATUS
{"Enabled. Search official sources when the question depends on current or policy-specific information." if live_verification else "Disabled. Do not claim current verification beyond the stored rule-engine context."}

Respond to the latest USER message.
""".strip(),
        store=False,
    )

    return response.output_text, extract_web_sources(response)

# -------------------------------------------------------------------
# Session state
# -------------------------------------------------------------------

defaults = {
    "messages": [],
    "pdf_completed": [],
    "pdf_current": [],
    "pdf_unknown": [],
    "last_uploaded_key": None,
    "override_work_status": None,
    "override_normal_load": None,
    "override_next_semester_load": None,
    "override_include_summer": None,
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# -------------------------------------------------------------------
# UI
# -------------------------------------------------------------------

st.title("🎓 GSU AI Academic Advisor")
st.caption(
    "Student Project Prototype — Georgia State University MS Information Systems"
)

st.warning(
    "Prototype only. This is not an official GSU Degree Works, registration, "
    "immigration, financial-aid, or graduation-clearance system."
)

with st.sidebar:
    st.header("Advisor status")
    st.write(
        "AI service:",
        "✅ Connected" if get_api_key() else "⚠️ Rule-based fallback"
    )
    live_verification = st.toggle(
        "Live official-source verification",
        value=True,
        help=(
            "When enabled, the AI may search official GSU and relevant U.S. government "
            "websites for current policies and information."
        ),
    )
    if live_verification:
        st.success("Live verification enabled")
    else:
        st.caption("Live verification disabled")

    st.link_button(
        "Robinson MSIS curriculum",
        req["source_url"]
    )

    if st.button("Reset chat planning changes"):
        st.session_state.override_work_status = None
        st.session_state.override_normal_load = None
        st.session_state.override_next_semester_load = None
        st.session_state.override_include_summer = None
        st.rerun()

# ---------------- Profile ----------------

st.subheader("1. Student profile")

left, right = st.columns(2)

with left:
    concentration = st.selectbox(
        "MSIS concentration",
        list(req["concentrations"].keys()),
        help=(
            "The Academic Record section below will automatically load "
            "the courses relevant to this concentration."
        ),
    )

    base_work = st.selectbox(
        "Employment status",
        ["Not working", "Part-time", "Full-time"],
    )

    base_load = st.selectbox(
        "Normal courses per semester",
        [1, 2, 3, 4],
        index=1,
    )

with right:
    schedule_pref = st.selectbox(
        "Preferred schedule",
        [
            "No preference",
            "Evening",
            "Daytime",
            "Online/Hybrid",
        ],
    )

    start_term = st.selectbox(
        "Planning starts",
        ["Spring", "Fall", "Summer"],
        index=1,
    )

    start_year = st.number_input(
        "Start year",
        min_value=2026,
        max_value=2035,
        value=2026,
        step=1,
    )

    base_include_summer = st.checkbox(
        "Include Summer semesters",
        value=False,
    )

profile = effective_profile(
    base_work,
    base_load,
    base_include_summer,
)

st.markdown("#### Active planning profile")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Employment", profile["work_status"])
m2.metric(
    "Normal load",
    f'{profile["normal_load"]} course(s)',
)
m3.metric(
    "Next semester",
    (
        f'{profile["next_semester_load"]} course(s)'
        if profile["next_semester_load"]
        else "Normal load"
    ),
)
m4.metric(
    "Summer",
    "Included" if profile["include_summer"] else "Excluded",
)

# ---------------- Academic record ----------------

st.subheader("2. Academic record")

st.info(
    f"You selected **{concentration}**. "
    "The course selectors below contain the shared MSIS core, "
    f"**{concentration} courses only**, and CIS 8391. "
    "Courses from the other concentrations are not shown."
)

core = shared_core_courses()
conc_courses = concentration_courses(concentration)
internship = internship_course()

with st.expander("Courses included for this concentration", expanded=True):
    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown("**Shared MSIS Core**")
        for course in core:
            st.write(
                f'{course["code"]} — {course["name"]}'
            )

    with c2:
        st.markdown(f"**{concentration}**")
        for course in conc_courses:
            st.write(
                f'{course["code"]} — {course["name"]}'
            )

    with c3:
        st.markdown("**Field Study**")
        st.write(
            f'{internship["code"]} — {internship["name"]}'
        )

courses = relevant_courses(concentration)
labels = {
    course_label(c): c["code"]
    for c in courses
}
code_to_label = {
    code: label
    for label, code in labels.items()
}
label_list = list(labels.keys())

uploaded = st.file_uploader(
    "Upload transcript / Degree Works PDF",
    type=["pdf"],
)

# A unique key prevents old PDF selections from one concentration
# from leaking into another concentration.
upload_key = (
    f"{uploaded.name if uploaded else 'none'}|{concentration}"
)

if (
    uploaded
    and st.session_state.last_uploaded_key != upload_key
):
    try:
        text = extract_pdf_text(uploaded)

        if text.strip():
            detected, other = detect_courses(
                text,
                list(code_to_label.keys()),
            )

            st.session_state.pdf_completed = sorted(
                code
                for code, info in detected.items()
                if info["status"] == "completed"
            )

            st.session_state.pdf_current = sorted(
                code
                for code, info in detected.items()
                if info["status"] == "current"
            )

            st.session_state.pdf_unknown = sorted(
                code
                for code, info in detected.items()
                if info["status"] == "unknown"
            )

            st.session_state.last_uploaded_key = upload_key

            st.success(
                f"Detected {len(detected)} course(s) relevant to "
                f"the **{concentration}** planning view."
            )

            if other:
                st.caption(
                    f"The PDF also contained {len(other)} CIS course(s) "
                    "outside this concentration-specific planning view."
                )

        else:
            st.error(
                "No selectable text was found in the PDF. "
                "Enter the courses manually."
            )

    except Exception as exc:
        st.error(
            f"Could not read the PDF: {exc}"
        )

if st.session_state.pdf_unknown:
    visible_unknown = [
        c for c in st.session_state.pdf_unknown
        if c in code_to_label
    ]
    if visible_unknown:
        st.warning(
            "Detected but needs manual review: "
            + ", ".join(visible_unknown)
        )

default_completed = [
    code_to_label[code]
    for code in st.session_state.pdf_completed
    if code in code_to_label
]

completed_labels = st.multiselect(
    f"Completed courses — {concentration}",
    label_list,
    default=default_completed,
)

completed = [
    labels[label]
    for label in completed_labels
]

current_choices = [
    label
    for label in label_list
    if labels[label] not in completed
]

default_current = [
    code_to_label[code]
    for code in st.session_state.pdf_current
    if (
        code in code_to_label
        and code_to_label[code] in current_choices
    )
]

current_labels = st.multiselect(
    f"Courses currently in progress — {concentration}",
    current_choices,
    default=default_current,
)

current = [
    labels[label]
    for label in current_labels
]

# ---------------- Rule engine ----------------

plan = build_rule_plan(
    concentration,
    completed,
    current,
)

semester_rows = build_semester_plan(
    plan,
    profile["normal_load"],
    profile["next_semester_load"],
    start_term,
    int(start_year),
    profile["include_summer"],
)

# ---------------- Plan ----------------

st.subheader("3. Current academic plan")

if semester_rows:
    for row in semester_rows:
        with st.container(border=True):
            st.markdown(
                f"### {row['term']}"
            )
            st.caption(
                f'Planned load: {row["planned_load"]} course(s)'
            )

            for course in row["courses"]:
                st.write(
                    f'**{course["code"]}** — {course["name"]}'
                )
                st.caption(
                    f'{course["category"]}: '
                    f'{course["selection_reason"]}'
                )

                if course.get("prerequisite_unverified"):
                    st.caption(
                        "⚠ Prerequisites are not yet verified "
                        "in this prototype."
                    )

                if not course.get("availability"):
                    st.caption(
                        "⚠ Offering in this exact term "
                        "has not been verified."
                    )

    st.info(
        f"Current planning estimate ends in "
        f"**{semester_rows[-1]['term']}**. "
        "This is not official graduation clearance."
    )

else:
    st.success(
        "No additional modeled course is currently queued."
    )

if plan["blocked"]:
    st.markdown("#### Verified prerequisite blocks")
    for course in plan["blocked"]:
        st.error(
            f'{course["code"]}: missing '
            f'{", ".join(course["missing_prerequisites"])}'
        )

# ---------------- AI advisor ----------------

st.subheader("4. 💬 AI Academic Advisor")

st.write(
    "Ask the advisor about your degree plan, course sequence, workload, "
    "graduation estimate, concentration, prerequisites, scheduling, or "
    "other academic-planning questions. When important information is missing, "
    "the advisor will ask a follow-up question instead of guessing."
)

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        sources = message.get("sources", [])
        if sources:
            with st.expander("Live verification sources"):
                for source in sources:
                    st.markdown(f'- [{source["title"]}]({source["url"]})')

        if message.get("mode"):
            st.caption(f'Response mode: {message["mode"]}')

question = st.chat_input(
    "Example: I work full-time. Can I take only one course next semester?"
)

if question:
    st.session_state.messages.append({
        "role": "user",
        "content": question,
    })

    # Update conversational profile first.
    changes = apply_chat_overrides(question)

    profile = effective_profile(
        base_work,
        base_load,
        base_include_summer,
    )

    # Recalculate the rule engine after every conversational change.
    plan = build_rule_plan(
        concentration,
        completed,
        current,
    )

    semester_rows = build_semester_plan(
        plan,
        profile["normal_load"],
        profile["next_semester_load"],
        start_term,
        int(start_year),
        profile["include_summer"],
    )

    context = build_ai_context(
        concentration,
        completed,
        current,
        profile,
        plan,
        semester_rows,
        changes,
    )

    with st.chat_message("assistant"):
        with st.spinner(
            "Reviewing your academic scenario..."
        ):
            try:
                answer, sources = call_ai(
                    context,
                    st.session_state.messages,
                    live_verification=live_verification,
                )
                response_mode = (
                    "GenAI + rule engine + live verification"
                    if live_verification
                    else "GenAI + degree-rule engine"
                )

            except Exception:
                answer = fallback_answer(
                    question,
                    concentration,
                    profile,
                    plan,
                    semester_rows,
                    changes,
                )
                sources = []
                response_mode = "Rule-based advisor fallback"

            st.markdown(answer)

            if sources:
                with st.expander("Live verification sources"):
                    for source in sources:
                        st.markdown(
                            f'- [{source["title"]}]({source["url"]})'
                        )

            st.caption(
                f"Response mode: {response_mode}"
            )

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "sources": sources,
        "mode": response_mode,
    })

    # Refresh page so active profile and plan show the changes.
    st.rerun()

if st.session_state.messages:
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()
