# DOCMind AI — Building a Trial .exe for Customers

## Before building — configure the trial

Open `trial_config.py` and set:

```python
TRIAL_EXPIRES  = "2026-04-07"   # exact expiry date for this customer
MAX_QUERIES    = 10              # how many questions they can ask
CONTACT_NAME   = "Your Name"
CONTACT_EMAIL  = "you@email.com"
CONTACT_PHONE  = "+64 XX XXX XXXX"
CUSTOMER_NAME  = "Acme Corp"    # shown in the app and expiry screen
```

Also make sure your `.env` has the API key:
```
ANTHROPIC_API_KEY=sk-ant-...
BRAVE_API_KEY=BSA...
```

## Build the .exe

Double-click `build_exe.bat` (or run in terminal):
```bat
build_exe.bat
```

First build takes 5–10 minutes. Output goes to:
```
dist\DocMindAI\
```

## What to send the customer

Zip the entire `dist\DocMindAI\` folder and send it.
They double-click `DocMindAI.exe` — browser opens automatically. No install needed.

## How it works

| Feature | Detail |
|---|---|
| Auto-open | Browser opens to http://127.0.0.1:5000 on launch |
| Expiry | App shows expired screen after the set date |
| Query cap | Stops after MAX_QUERIES questions |
| Counter storage | Saved in `%APPDATA%\.docmindai\` — resets on new machine |
| Expiry screen | Shows your contact details with email/phone links |
| Trial badge | Live counter in top bar e.g. "Trial: 8 queries · 2d left" |

## Different customers — different configs

Just change `trial_config.py` and rebuild each time:
- Customer A: 2 days, 10 queries
- Customer B: 7 days, 50 queries
- Customer C: specific expiry date

Each `.exe` is independently configured.

## File size

Expect ~500MB–1GB for the dist folder (includes PyTorch, sentence-transformers).
Zip it — should compress to ~300–600MB.
