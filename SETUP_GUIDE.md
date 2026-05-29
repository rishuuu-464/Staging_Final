# 🏭 Staging Hub — Flask Setup Guide

## Your Project is Ready!

All code files are created at `C:\Users\<YOUR_USERNAME>\Desktop\staging_main\`:

> **Remote Desktop:** Host `ACXW-FSCDAEXT` / IP `172.31.44.121`
> Replace `<YOUR_USERNAME>` with the Windows user on the remote desktop.

```
staging_main/
├── app.py              ← Flask server (replaces code.gs)
├── sheets.py           ← Google Sheets connection helper
├── requirements.txt    ← Python packages (already installed)
├── .env                ← Your Sheet ID goes here
├── credentials.json    ← (YOU create this — step 1 below)
├── templates/
│   ├── index.html      ← Worker Portal
│   └── dashboard.html  ← Live Dashboard
└── venv/               ← Python virtual environment
```

---

## STEP 1: Create Google Cloud Service Account (FREE, ~5 min)

> You already have a Google account (G Suite). No credit card needed.

### 1.1 — Create a Google Cloud Project
1. Open browser → go to **https://console.cloud.google.com/**
2. Sign in with the **same Google account** that owns the Staging Sheet
3. Click **"Select a project"** (top bar) → **"New Project"**
4. Name: `Staging Hub` → Click **Create**
5. Make sure this new project is selected in the top bar

### 1.2 — Enable Google Sheets API + Drive API
1. In the left menu → **APIs & Services** → **Library**
2. Search **"Google Sheets API"** → Click it → Click **ENABLE**
3. Go back to Library → Search **"Google Drive API"** → Click it → Click **ENABLE**

### 1.3 — Create a Service Account
1. Left menu → **APIs & Services** → **Credentials**
2. Click **"+ CREATE CREDENTIALS"** → **Service Account**
3. Name: `staging-bot` → Click **Create and Continue**
4. Role: Select **Editor** → Click **Continue** → **Done**

### 1.4 — Download the JSON Key
1. In the Credentials page, click on your new service account (`staging-bot@...`)
2. Go to **"Keys"** tab → **Add Key** → **Create new key**
3. Choose **JSON** → Click **Create**
4. A file downloads (something like `staging-hub-xxxxx.json`)
5. **Rename it** to `credentials.json`
6. **Move it** into `C:\Users\<YOUR_USERNAME>\Desktop\staging_main\`

### 1.5 — Share Your Google Sheet
1. Open the downloaded `credentials.json` → find the `"client_email"` field  
   It will look like: `staging-bot@staging-hub-xxxxx.iam.gserviceaccount.com`
2. **Copy** that email address
3. Open your **Google Sheet (Stagging_Project1)** in Chrome
4. Click **Share** → paste the service account email → set to **Editor** → click **Send**

---

## STEP 2: Set Your Spreadsheet ID

1. Open your Google Sheet in the browser
2. Look at the URL:  
   `https://docs.google.com/spreadsheets/d/`**`1aBcDeFgHiJkLmNoPqRsTuVwXyZ`**`/edit`
3. Copy the long string between `/d/` and `/edit`
4. Open `C:\Users\<YOUR_USERNAME>\Desktop\staging_main\.env` in any text editor
5. Replace `PASTE_YOUR_SPREADSHEET_ID_HERE` with your actual ID:
   ```
   SPREADSHEET_ID=1aBcDeFgHiJkLmNoPqRsTuVwXyZ
   ```
6. Save the file

---

## STEP 3: Run the Flask Server

Open **VS Code Terminal** (or PowerShell) and run:

```powershell
cd C:\Users\<YOUR_USERNAME>\Desktop\staging_main
.\venv\Scripts\Activate.ps1
python app.py
```

You will see:
```
=========================================================
  🏭 STAGING HUB — Flask Server
=========================================================
  Worker Portal : http://localhost:5000
  Dashboard     : http://localhost:5000/dashboard
  Network URL   : http://<YOUR-PC-IP>:5000
=========================================================

 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:5000
 * Running on http://192.168.x.x:5000     ← USE THIS FOR HHDs
```

---

## STEP 4: Access from Devices

| Device | URL |
|--------|-----|
| Remote Desktop (browser) | `http://localhost:5000` or `http://172.31.44.121:5000` |
| HHDs on the floor | `http://172.31.44.121:5000` (or the IP shown in terminal) |
| Dashboard (supervisor) | `http://172.31.44.121:5000/dashboard` |

> **Important:** HHDs must be on the **same WiFi/network** as your PC.

---

## How to Find Your PC's IP Address

Run this in PowerShell:
```powershell
(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "Wi-Fi").IPAddress
```
Or check in **Settings → Network → Wi-Fi → Properties → IPv4 address**.

---

## Daily Workflow

1. **Morning:** Open VS Code terminal → Run the 3 commands above (activate + run)
2. **Workers:** Open `http://<PC-IP>:5000` on each HHD
3. **End of day:** Press `Ctrl+C` in the terminal to stop the server
4. Your sheet data stays in Google Sheets and gets backed up to Drive as usual

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError` | Make sure you ran `.\venv\Scripts\Activate.ps1` first |
| `credentials.json not found` | Make sure the file is in the `staging_main` folder |
| `SPREADSHEET_ID` error | Check `.env` has your actual Sheet ID, no quotes |
| HHD can't connect | Check both devices are on the same WiFi/LAN |
| Windows Firewall blocks | Allow Python through firewall when prompted |

> **Firewall:** The first time you run the server, Windows will ask to allow Python through the firewall. Click **"Allow access"** for both Private and Public networks.
