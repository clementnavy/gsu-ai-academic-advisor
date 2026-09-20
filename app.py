import json
import math
import os
from pathlib import Path

import streamlit as st
from openai import OpenAI

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
    # Streamlit Cloud / local secrets first, then environment variable.
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

Your job is to explain the academic plan and answer the student's follow-up questions.

Critical rules:
1. Treat the VERIFIED PROGRAM DATA and RULE-ENGINE RESULTS supplied by the application
   as the authoritative source for degree requirements in this conversation.
2. Never invent prerequisites, course availability, tuition, waivers, transfer-credit
   decisions, registration eligibility, or official graduation clearance.
3. If the supplied data does not answer a question, say that the information is not
   verified in this prototype and advise the student to confirm it with a GSU academic advisor.
4. You may personalize explanations using the student's work status, desired course load,
   schedule preference, and stated constraints.
5. Do not change degree requirements simply because the student asks for a faster or easier plan.
6. Distinguish completed courses from courses currently in progress.
7. If a directed-elective rule is flagged as needing verification, do not guess how it applies.
8. A graduation estimate is only a planning estimate. Do not state that the student is
   officially cleared to graduate.
9. Keep answers clear, practical, and concise. When useful, explain why a recommendation
   fits the student's constraints.
10. Do not request sensitive identifiers such as Social Security numbers, passwords,
    PantherCard credentials, or banking information.
""".strip()

def ask_ai(context, chat_history):
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Add it to Streamlit secrets."
        )

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

if "messages" not in st.session_state:
    st.session_state.messages = []

st.title("🎓 GSU AI Academic Advisor")
st.caption("Student Project Prototype — Georgia State University MS Information Systems")

st.warning(
    "Prototype only. This is not an official GSU advising, Degree Works, registration, "
    "or graduation-clearance system. Verify final decisions with an academic advisor."
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
label_list = list(labels.keys())

uploaded = st.file_uploader(
    "Upload transcript / Degree Works PDF",
    type=["pdf"],
    help="Automatic PDF course extraction is a later build stage."
)
if uploaded:
    st.info("File received. Automatic course extraction is not enabled yet.")

completed_labels = st.multiselect("Completed courses", label_list)
completed = [labels[x] for x in completed_labels]

current_choices = [x for x in label_list if labels[x] not in completed]
current_labels = st.multiselect("Courses currently in progress", current_choices)
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
            "actual course availability or unverified prerequisites."
        )
    else:
        st.success("All requirements represented in this prototype appear accounted for.")

st.divider()
st.subheader("4. 💬 Ask the AI Academic Advisor")

if not get_api_key():
    st.info(
        "Add OPENAI_API_KEY to your Streamlit secrets to enable the advisor chat."
    )

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
