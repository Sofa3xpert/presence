#!/bin/bash
# Send the student daily brief via Telegram.
# Runs via crontab every morning at 7:00 HKT.
cd /Users/petrbaldakov/DEV/presence
/Users/petrbaldakov/DEV/presence/.venv/bin/python -c "
from pathlib import Path
from presence.cycle import run_student_brief
data = Path.home() / 'Library/Application Support/Presence'
brief, delivered = run_student_brief(data, send=True)
print('Delivered' if delivered else 'Not delivered')
" >> /tmp/presence_brief.log 2>&1
