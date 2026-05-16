$ErrorActionPreference = "Stop"
if (!(Test-Path .venv)) {
  python -m venv .venv
}
. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (!(Test-Path .env)) {
  Copy-Item .env.example .env
}
python app.py
