# SSDC Lesson Booking Tool (W.I.P.)

This project automates the SSDC lesson booking flow with Selenium. It logs in to SSDC, looks for available slots for all lesson dates, adds available slots to cart, and sends Telegram alerts when manual action or booking attention is needed.

FYI: Booking sites may rate-limit or block repeated automated activity, especially if multiple instances are running at the same time.

## What the script does

- Opens the SSDC login webpage in Chrome.
- Logs in with the configured account profile.
- Navigates to the Add Booking page.
- Clicks **Get The Earliest Date** to check for earliest availability.
- Clicks **Check for availability** when the flow indicates it should continue.
- Scans the availability table for clickable lesson slots.
- Clicks on available slots to book them.
- Sends Telegram alerts for CAPTCHA checks, selected slots, and important errors.
- Opens the payment review page in a second browser tab after a slot is selected.
- Supports Telegram commands such as pause, resume, status, and kill.

## Requirements

- Python 3.10 or newer
- Google Chrome
- A Telegram bot token
- Your Telegram chat ID
- SSDC login credentials

Install the Python packages:

```bash
pip install selenium undetected-chromedriver requests python-dotenv
```

If you prefer using a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install selenium undetected-chromedriver requests python-dotenv
```

## Setup

### 1. Create your .env file

Copy the example environment file:

```bash
cp .env.example .env
```

Then edit `.env` and fill in your Telegram settings:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
```

Do not commit `.env` to a public repository.

### 2. Create your profiles.py file

Copy the example profiles file:

```bash
cp profiles.example.py profiles.py
```

Then edit `profiles.py` and add your SSDC account details.

Example shape:

```python
USER_PROFILES = [
    {
        "id": 1,
        "name": "John Doe",
        "username": "YOUR_USERNAME",
        "password": "YOUR_PASSWORD",
    },
]
```

Do not commit `profiles.py` to a public repository.

### 3. Select which profile to use

Open `camp.py` and set:

```python
SELECTED_PROFILE_ID = 1
```

The value should match the `id` of the account you want the script to use from `profiles.py`.

## Running the script

From the project folder:

```bash
python3 camp.py
```

Chrome should open automatically. If a CAPTCHA or verification screen appears, solve it manually in the browser. The script should continue after the verification is cleared.

## Telegram commands

The script listens for Telegram commands while it is running:

```text
/pause
/resume
/status
/kill
```

Use `/pause` when you want the script to stop checking temporarily. Use `/resume` to continue checking. Use `/status` to confirm whether the script is running or paused. Use `/kill` only when you want the script to stop.

## Private files

```text
.env
profiles.py
__pycache__/
*.pyc
.DS_Store
```

## Troubleshooting

### The script is waiting for CAPTCHA, but I only see the login page

The page may still contain verification-related elements, or the site may not have finished loading. Wait briefly, then check whether the username and password fields eventually fill in. If it keeps happening, restart the script and Chrome.

### Chrome opens but nothing happens

Check that:

- Chrome is installed and up to date.
- Your Python dependencies are installed.
- `profiles.py` exists.
- `.env` exists.
- `SELECTED_PROFILE_ID` matches a profile in `profiles.py`.

### I see "Unusual activities were detected"
![SSDC unusual activities warning](docs/unusual-activities-r4.jpg)
Stop the script and wait before trying again. This usually means the site is rate-limiting or blocking repeated activity.

### No slots are being detected

This can happen when there are no slots available. It can also happen if SSDC changes the page layout. Watch the terminal logs to confirm whether the script is reaching the availability table and whether it reports any clickable slots.

### Telegram messages are not sending

Check that:

- `TELEGRAM_BOT_TOKEN` is correct.
- `TELEGRAM_CHAT_ID` is correct.
- You have sent at least one message to your Telegram bot before using it.
- Your network allows Telegram API requests.

## Notes

This script depends on the current SSDC website structure. If SSDC changes button IDs, table markup, modal behavior, or verification flow, the script may need updates.
