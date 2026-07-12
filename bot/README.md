# Midnight live-signing бот — статус и шаги активации

Направление 8/B. Профиль: латентность+капитал расслаблены (решение пользователя
2026-07-12). Цель — заряженный бот, авто-стрельба в первую позицию ≥$5k, малый газ.

## Что уже готово (безопасно, read-only/код, в git)

- **Детектор целей** `analysis/midnight_positions.py` — энумерация заёмщиков
  (SupplyCollateral/UpdatePosition) + `debt()` per позиция; классификация
  post-maturity-сейзабельных и приближающихся окон. **Текущая цепь: 0 целей ≥$5k**
  (147 заёмщиков, всё пыль ≤$16) — ликвидировать сейчас нечего.
- **Модель входа** `analysis/midnight_breakeven.py` (M-T2) — t\* + realized-net
  РЕАЛЬНЫМ Uniswap-роутером (не оракулом).
- **Callback-контракт** `bot/MidnightLiquidator.sol` — атомарный путь
  (seize→своп через Uniswap SwapRouter02→repay), owner-gated, **гард профита**
  (реверт, если выручка < долг+minProfit). Компилируется против реальных
  интерфейсов Midnight (solc 0.8.34, osaka). **НЕ задеплоен.**
- **Мониторинг** `monitor/` — нативный VPS-cron (пн/чт 06:17 UTC), шлёт в Telegram
  прямым Bot API; эскалирует при появлении цели ≥$5k. Не зависит от сессии Claude.

## ⚙️ Как завести ключ/подпись и куда положить (процедура пользователя)

Ключ хранится ЗАШИФРОВАННЫМ в foundry-keystore; сырой приватник нигде в plaintext,
не в git, не в `ps` (cast читает keystore + файл-пароль). Шаги на VPS:

```bash
# 1. Сгенерировать новый кошелёк (или пропусти, если импортируешь свой)
cast wallet new                       # печатает Address + Private key — сохрани адрес

# 2. Импортировать приватник в зашифрованный keystore под именем midnight-bot
cast wallet import midnight-bot --interactive   # вставь private key, задай пароль

# 3. Положить пароль в файл, который читает бот (никогда не в git)
mkdir -p ~/.midnight-bot
printf '%s' 'ТВОЙ_ПАРОЛЬ' > ~/.midnight-bot/pw && chmod 600 ~/.midnight-bot/pw

# 4. Профинансировать АДРЕС из шага 1 небольшим ETH на Base (на газ, ~0.01–0.02 ETH).
#    Капитал под позицию НЕ нужен — atomic callback свопит залог ДО repay.

# 5. Задеплоить callback-контракт с этого ключа:
bash /home/claude-agent/edge-research/bot/deploy.sh
#    -> скопируй "Deployed to: 0x…"
```

Итого: keystore в `~/.foundry/keystores/midnight-bot`, пароль в `~/.midnight-bot/pw`
(600). Больше НИЧЕГО руками — код уже в git.

## Боевой запуск (когда монитор поймает цель ≥$5k)

```bash
cd /home/claude-agent/edge-research
export MN_CONTRACT=0x<адрес_из_deploy>
# сухой прогон (ничего не шлёт, покажет cast-команду и net):
DRY_RUN=1 python3 -m bot.executor once
# боевой автономный цикл (стреляет САМ в цели ≥$5k):
DRY_RUN=0 MN_CONTRACT=$MN_CONTRACT nohup python3 -m bot.executor loop &
```

Executor автономен: сам находит цель, считает t\*+realized-net, и при net > $50
собирает `runLiquidation`, подписывает ключом и шлёт tx в t\* — без ручного шага.
Гарды: `DRY_RUN=1` по умолчанию; realized-net гард (не стреляет в убыток офчейн);
`minLoanOut` в calldata + гард контракта (реверт в убыток он-чейн — worst-case
потерянный газ). Алерты о выстрелах/реверте — в TG.

## Рекомендация по ПЕРВОМУ выстрелу (сильно советую)

Перед первым боевым `DRY_RUN=0` на реальной цели — дай мне прогнать fork-replay
контракта против неё (M-T3-стиль, секунды): подтвердить realized-net на КОНКРЕТНОМ
коллатерале до капитала. blue-chip (WETH/cbBTC) exit доказанно глубокий; mid/thin-cap
(MORPHO/WELL) — риск, гард контракта страхует от убытка, но fork-replay ловит и
пограничные реверты (liveness-грабли из M-T3). Дальше — полностью автономно.
