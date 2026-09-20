# GSU AI Academic Advisor — GenAI Chat Build

## Install/update packages

```cmd
python -m pip install -r requirements.txt
```

## Local API secret

Create:

`.streamlit\secrets.toml`

with:

```toml
OPENAI_API_KEY = "YOUR_KEY"
```

Do not commit `secrets.toml`.

## Run

```cmd
python -m streamlit run app.py
```

## Streamlit Community Cloud

Open the deployed app's settings/secrets area and add:

```toml
OPENAI_API_KEY = "YOUR_KEY"
```

Then save/reboot the app.

## Model

The prototype uses `gpt-5.6-luna` to keep interactive advising relatively economical.

## Important

This is a classroom prototype, not an official GSU advising, registration, Degree Works,
or graduation-clearance system.
