# AI Academic Advisor — Starter Website

## Windows setup

Open Command Prompt in this folder and run:

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Then open the local URL shown by Streamlit, normally:

http://localhost:8501

## What works in this version

- Student information form
- Manual completed/current course selection
- Employment and course-load preferences
- Rule-based remaining-course calculation
- Simple prerequisite checking
- Recommended next-course list
- Transcript/Degree Works upload control (parsing comes next)

## Important

The bundled degree data is demo data only. Replace it with verified GSU catalog requirements before presenting it as GSU-specific.
