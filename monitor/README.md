# Midnight форвардный мониторинг (нативный VPS-cron, без облака)

Живая версия крутится из `/home/claude-agent/midnight-monitor/`, поставлена в
OS-crontab: `17 6 * * 1,4 flock -n /tmp/midnight_monitor.lock timeout 600 /home/claude-agent/midnight-monitor/run.sh`
(пн/чт 06:17 UTC). Копия здесь — для версионирования/ревью.

- `monitor.py` — прогоняет календарь (M-T1), вотчеры (M-T4), пороги (M-T5) +
  position-сканер (borrower-уровень); шлёт сжатый отчёт в Telegram ПРЯМЫМ Bot API
  (токен из `~/.claude/channels/telegram/.env`, не хранится в репо). При §5-триггере
  (боевая позиция ≥$5k / Adapter не WIP / OEV-фид / ≥10 ликвидаторов / re-deploy) —
  выносит `⚠️ ЭСКАЛАЦИЯ` первой строкой. Кэши возвращаются к git-состоянию.
- `run.sh` — обёртка для cron (flock+timeout, лог в monitor.log).

Не требует сессии Claude. Никаких live-транзакций (read-only + отправка сообщений).
