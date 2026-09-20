# GSU AI Academic Advisor — Planner + Fallback Build

New features:
- prerequisite-aware planning
- semester-by-semester planning
- optional Summer terms
- API fallback when OpenAI is unavailable or out of credits
- PDF course extraction retained

Important:
- prerequisite rules are enforced only when explicitly marked verified in the JSON
- course offering/availability is not treated as official unless verified
- graduation output is a planning estimate, not official clearance

Install:
python -m pip install -r requirements.txt

Run:
python -m streamlit run app.py
