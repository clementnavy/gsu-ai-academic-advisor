# GSU AI Academic Advisor — Live Verification Build

## New feature: live verification

The AI advisor can now use the OpenAI Responses API `web_search` tool to verify current
facts from official sources during the conversation.

Search is restricted to:
- gsu.edu (including Robinson, ISSS, Registrar, and other GSU subdomains)
- dhs.gov
- uscis.gov
- ed.gov

This means the advisor can check current information for questions involving:
- GSU program information
- deadlines
- tuition/fees
- current university guidance
- F-1 / ISSS topics
- financial aid and assistantship topics
- official office/contact information
- facts the student explicitly asks to verify

## Important design rule

Live web information does NOT replace:
- the student's official Degree Works audit
- official registration records
- advisor approval
- transfer/waiver decisions
- immigration determinations
- financial-aid eligibility decisions

The AI should explain what it found, show sources, and still identify any remaining item
that requires official confirmation.

## UI

The sidebar now has:

`Live official-source verification`

Turn it ON to allow the AI to search official sources.

## Test questions

Try:
- `Can I take only one class and still maintain F-1 status?`
- `Verify whether the MSIS Cybersecurity concentration starts in Spring.`
- `What are the current MSIS class times?`
- `What is the current tuition?`
- `Why did you recommend CIS 8088?`
- `Is CIS 8088 offered next semester?`

For questions where official sources do not provide a definite answer, the advisor should
say what was found and what still needs confirmation.

## Run

```cmd
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Deploy

```cmd
git add -A
git commit -m "Add live official-source verification"
git push origin main
```
