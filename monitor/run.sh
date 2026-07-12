#!/bin/bash
# Обёртка для OS-cron: нативный мониторинг Midnight -> Telegram Bot API.
# Ставится в crontab: 17 6 * * 1,4 /home/claude-agent/midnight-monitor/run.sh
# Логи -> monitor.log. Не требует сессии Claude и облака.
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.foundry/bin"
cd /home/claude-agent/edge-research || exit 1
/usr/bin/python3 /home/claude-agent/midnight-monitor/monitor.py >> /home/claude-agent/midnight-monitor/monitor.log 2>&1
