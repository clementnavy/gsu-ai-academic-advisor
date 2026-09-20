# GSU AI Academic Advisor — Concentration-Specific + Conversational Advisor

## What changed

### 1. Concentration-specific academic record
Selecting a concentration now changes the Academic Record course list.

For example, Cybersecurity shows:
- shared MSIS core courses
- Cybersecurity concentration courses only
- CIS 8391 Field Study

Courses belonging only to the other concentrations are not shown.

The same behavior applies to every concentration.

### 2. Better AI advisor
The AI advisor now:
- answers broad academic-planning questions
- uses the exact current generated plan
- asks one focused follow-up question when a key fact is missing
- updates planning preferences from chat
- explains what it can verify
- explains useful decision factors when a rule is not verified
- ends with a Verify note when an official policy/course fact still needs confirmation

### 3. Rule-based fallback
If the OpenAI API is unavailable or out of credits, the site still answers common advising questions.

## Install

```cmd
python -m pip install -r requirements.txt
```

## Run

```cmd
python -m streamlit run app.py
```

## Suggested test

1. Select Cybersecurity.
2. Confirm Academic Record shows only shared core + Cybersecurity + CIS 8391.
3. Switch to AI for Data-Driven Business and confirm the concentration courses change.
4. Ask: `I work full-time.`
5. Ask: `I want only one course next semester.`
6. Ask: `Why did you recommend CIS 8088?`
7. Ask: `Can I take only one class and still be considered full-time?`
   - The advisor should ask which rule matters (academic, F-1, aid, or assistantship) or clearly flag verification.
