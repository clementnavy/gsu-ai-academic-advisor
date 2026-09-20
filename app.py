import json
from pathlib import Path
import streamlit as st

st.set_page_config(
    page_title="AI Academic Advisor",
    page_icon="🎓",
    layout="wide"
)

DATA_FILE = Path("data/degree_requirements.json")

@st.cache_data
def load_degree_data():
    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)

degree = load_degree_data()
courses = degree["courses"]
course_codes = [c["code"] for c in courses]
course_lookup = {c["code"]: c for c in courses}

st.title("🎓 AI Academic Advisor")
st.caption("Prototype for personalized academic planning")

st.info(
    "This first version demonstrates the website and planning workflow. "
    "The degree requirements in this starter project are demo data only. "
    "We will replace them with verified GSU catalog data next."
)

with st.sidebar:
    st.header("About")
    st.write("This prototype helps students identify remaining courses and build a possible semester plan.")
    st.warning("Not an official degree audit. Final decisions must be verified with a university advisor.")

st.subheader("1. Student information")

col1, col2 = st.columns(2)

with col1:
    student_name = st.text_input("Name (optional)", placeholder="Example: Jordan Student")
    program = st.selectbox("Program", [degree["program"]])
    catalog_year = st.selectbox("Catalog year", [degree["catalog_year"]])

with col2:
    work_status = st.selectbox(
        "Employment status",
        ["Not working", "Part-time", "Full-time"]
    )
    course_load = st.selectbox(
        "Preferred number of courses per semester",
        [1, 2, 3, 4],
        index=1
    )
    preferred_schedule = st.selectbox(
        "Preferred schedule",
        ["No preference", "Evening", "Daytime", "Online/Hybrid"]
    )

st.subheader("2. Academic record")

uploaded_file = st.file_uploader(
    "Upload transcript or Degree Works PDF (upload UI only in this first version)",
    type=["pdf"]
)

st.caption("For now, choose completed courses manually. PDF extraction will be added in the next build stage.")

completed_courses = st.multiselect(
    "Completed courses",
    course_codes
)

current_courses = st.multiselect(
    "Courses currently in progress",
    [c for c in course_codes if c not in completed_courses]
)

additional_info = st.text_area(
    "Anything else the advisor should consider?",
    placeholder="Example: I work Monday-Friday 8 AM-5 PM and prefer evening classes."
)

def check_prerequisites(course, completed):
    missing = []
    for prereq in course.get("prerequisites", []):
        if prereq not in completed:
            missing.append(prereq)
    return missing

def build_plan():
    finished_or_current = set(completed_courses) | set(current_courses)

    remaining = [
        c for c in courses
        if c["code"] not in finished_or_current
    ]

    eligible = []
    blocked = []

    for course in remaining:
        missing = check_prerequisites(course, set(completed_courses))
        if missing:
            blocked.append((course, missing))
        else:
            eligible.append(course)

    recommendations = eligible[:course_load]
    return remaining, recommendations, blocked

st.subheader("3. Build your plan")

if st.button("Build My Academic Plan", type="primary", use_container_width=True):
    remaining, recommendations, blocked = build_plan()

    st.success("Academic plan generated.")

    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("Completed", len(completed_courses))
    metric2.metric("In progress", len(current_courses))
    metric3.metric("Remaining", len(remaining))

    st.divider()

    left, right = st.columns(2)

    with left:
        st.markdown("### Recommended next courses")
        if recommendations:
            for course in recommendations:
                st.write(f"**{course['code']}** — {course['name']} ({course['credits']} credits)")
        else:
            st.write("No currently eligible courses found in the demo requirements.")

        st.markdown("### Why this plan?")
        reasons = [
            f"You selected a preferred load of **{course_load} course(s) per semester**.",
            f"Your employment status is **{work_status}**.",
            f"Your schedule preference is **{preferred_schedule}**."
        ]
        for reason in reasons:
            st.write("• " + reason)

        if additional_info.strip():
            st.write("• Additional consideration:", additional_info)

    with right:
        st.markdown("### Remaining requirements")
        if remaining:
            for course in remaining:
                st.write(f"• {course['code']} — {course['name']}")
        else:
            st.write("No remaining demo requirements.")

        st.markdown("### Prerequisite warnings")
        if blocked:
            for course, missing in blocked:
                st.warning(
                    f"{course['code']} is blocked until: {', '.join(missing)}"
                )
        else:
            st.write("No prerequisite warnings.")

    st.divider()
    st.markdown("### AI Advisor")
    st.info(
        "The GenAI explanation will appear here after we connect the AI model. "
        "For now, the recommendation comes from rule-based degree logic."
    )

    st.caption(
        "Prototype only — not an official academic advising or graduation clearance tool."
    )
