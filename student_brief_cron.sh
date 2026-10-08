#!/bin/bash
# Send the student daily brief via Telegram.
# Runs via crontab every morning at 7:00 HKT.
# On Fridays, also sends the weekly summary.
cd /Users/petrbaldakov/DEV/presence
/Users/petrbaldakov/DEV/presence/.venv/bin/python -c "
from datetime import date
from pathlib import Path
from presence.cycle import run_student_brief, run_student_weekly
data = Path.home() / 'Library/Application Support/Presence'
brief, delivered = run_student_brief(data, send=True)
print('Daily:', 'Delivered' if delivered else 'Not delivered')
if date.today().weekday() == 4:  # Friday
    weekly, w_delivered = run_student_weekly(data, send=True)
    print('Weekly:', 'Delivered' if w_delivered else 'Not delivered')
" >> /tmp/presence_brief.log 2>&1
