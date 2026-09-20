import json
import math
from pathlib import Path
import streamlit as st

st.set_page_config(
    page_title="GSU AI Academic Advisor",
    page_icon="🎓",
    layout="wide"
)

DATA_FILE = Path("data/gsu_msis_requirements.json")

@st.cache_data
def load_requirements():
    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)

req = load_requirements()

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
    help="Automatic PDF extraction will be connected in the next build stage."
)

if uploaded:
    st.info("File received. Automatic course extraction will be connected next.")

completed_labels = st.multiselect("Completed courses", label_list)
completed = [labels[x] for x in completed_labels]

current_choices = [x for x in label_list if labels[x] not in completed]
current_labels = st.multiselect("Courses currently in progress", current_choices)
current = [labels[x] for x in current_labels]

extra = st.text_area(
    "Additional constraints",
    placeholder="Example: I work Monday-Friday 8 AM-5 PM and prefer two evening courses."
)

st.subheader("3. Academic plan")

if st.button("Build My Academic Plan", type="primary", use_container_width=True):
    plan = remaining_plan(concentration, completed, current)
    recs = build_recommendations(plan, course_load)

    st.success("Plan generated from the Robinson MSIS curriculum structure.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Completed", len(completed))
    m2.metric("In progress", len(current))
    m3.metric("Core slots left", plan["core_needed_count"])
    m4.metric("Concentration items left", plan["conc_needed_count"])

    st.divider()
    left, right = st.columns(2)

    with left:
        st.markdown("### Recommended next courses")
        if recs:
            for item in recs:
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
                f'courses accounted for; the Robinson page displays a '
                f'{conc["displayed_hours"]}-hour concentration core.'
            )

        internship_code = req["internship"]["course"]["code"]
        if plan["internship_remaining"]:
            st.write(f"**Internship:** {internship_code} still appears outstanding.")
        else:
            st.write(f"**Internship:** {internship_code} accounted for.")

        if plan["directed_elective"]:
            st.warning(
                "Robinson also lists a Directed Elective for this concentration. "
                "The public page does not fully specify the rule needed for this prototype, "
                "so the app flags it for advisor verification rather than guessing."
            )

    st.divider()

    listed_remaining = (
        plan["core_needed_count"]
        + plan["conc_needed_count"]
        + (1 if plan["internship_remaining"] else 0)
    )

    if course_load > 0 and listed_remaining > 0:
        est_terms = math.ceil(listed_remaining / course_load)
        st.markdown("### Planning estimate")
        st.write(
            f"At **{course_load} course(s) per semester**, the currently modeled "
            f"requirements would take approximately **{est_terms} additional semester(s)**."
        )
        st.caption(
            "This is not an official graduation date. It does not yet account for "
            "course availability, prerequisites, waivers, transfer credit, catalog-year rules, "
            "or directed-elective details."
        )
    elif listed_remaining == 0:
        st.success("All requirements represented in this prototype appear accounted for.")

    st.markdown("### AI Advisor explanation")
    st.info(
        "Next step: connect a GenAI model so this section explains the plan in natural language, "
        "answers follow-up questions, and reasons over student constraints without changing the verified degree rules."
    )
