# Airline Disruption Recovery Lab

This folder contains the runtime files for deploying the prototype on Streamlit Community Cloud. It uses synthetic schedules, passenger groups, aircraft, weather effects, capacities, and costs. It does not connect to live airline, weather, or passenger systems and requires no API keys.

## Files required by the app

- `app.py`: Streamlit entry point and navigation for Disruption Recovery and Airport Operations.
- `src/`: shared scenario, validation, capacity, DGCA demonstration, optimizer, and airport-scene code.
- `views/`: the two Streamlit pages.
- `config/`: synthetic airline profiles and demo schedule loaded by the app.
- `requirements.txt`: the tested Python package versions.
- `.streamlit/config.toml`: app theme. Keep this folder at the repository root.

## Put this folder in GitHub

Use the **contents** of `streamlit_deploy` as the root of a GitHub repository. Do not place this folder one level below the repository root: the app expects `src`, `views`, `config`, and `.streamlit` beside `app.py`.

From PowerShell opened inside this folder, initialize and push the repository (replace the URL with your GitHub repository URL):

```powershell
git init
git add .
git commit -m "Prepare airline recovery app for deployment"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

## Deploy on Streamlit Community Cloud

1. Sign in at [share.streamlit.io](https://share.streamlit.io/) with GitHub and authorize access to this repository.
2. Choose **Create app**, then select the repository, the `main` branch, and `app.py` as the app entry point.
3. In **Advanced settings**, select Python 3.14 if it is available, matching the version used for this project.
4. Deploy. Community Cloud installs the packages listed in `requirements.txt`; the included `config` files provide the sample scenario automatically.

No `.venv`, `requirements-dev.txt`, test output, personal data, secrets, or API keys are needed in the deployment repository. Never commit `.streamlit/secrets.toml` or credentials.

## Run locally from this folder

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the local URL printed in the terminal. Keep the terminal running while using the app.
