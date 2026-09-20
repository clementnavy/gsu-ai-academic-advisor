# GSU AI Academic Advisor — PDF Reader Build

## New feature

The app now reads text-based transcript / Degree Works PDFs and:

- extracts PDF text with `pypdf`
- detects CIS course codes
- heuristically separates likely completed vs. in-progress courses
- pre-populates the completed/current course selectors
- shows uncertain detections for student review
- reports other CIS courses found outside the currently modeled degree list

## Install

```cmd
python -m pip install -r requirements.txt
```

## Run

```cmd
python -m streamlit run app.py
```

## Important limitation

This version reads PDFs with embedded/selectable text.

If a transcript or Degree Works file is a scanned/image-only PDF, text extraction may return
nothing. OCR can be added in a later version.

Students should always review the automatically detected courses before building the plan.
