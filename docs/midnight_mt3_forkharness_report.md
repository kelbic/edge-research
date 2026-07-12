# M-T3 fork-harness: отчёт (Фаза B плана Направления 8/B)

Дата: 2026-07-12. План: `docs/midnight_tooling_plan.md` §3 (M-T3), §6. Механика:
`docs/morpho_v2_mechanics.md` §1. День-0: `docs/midnight_day0_report.md`.
Всё ЛОКАЛЬНО (форк Base). Ни одной транзакции в реальную сеть. [P] = проверено
исполнением на реальном байткоде.

## 0. Итог

**8/8 тестов проходят** на РЕАЛЬНОМ задеплоенном байткоде Midnight
(`0xAdedD8ab6dE832766Fedf0FaC4992E5C4D3EA18A`, Base, форк на блоке 48522417).

- **Рамп-формула post-maturity ПОДТВЕРЖДЕНА на реальном байткоде: ДА** [P] —
  точное (wei-в-wei) совпадение возвращаемого `repaidUnits` с
  `lif(Δ)=min(maxLif, WAD+(maxLif−WAD)·Δ/3600)`.
- **Liveness-грабли воспроизведены**: (a) реверт оракула ЛЮБОГО активированного
  коллатерала блокирует liquidate — ДА; (b) оракул сейзимого = 0 — ДА, оба пути
  задокументированы. (c) "liquidation locked" — НЕ покрыта (обоснование в §5).
- **Контроль реализма**: до maturity и normal-mode на здоровой позиции —
  оба ревертят `NotLiquidatable` [P].

Артефакты:
- Тест: `/home/claude-agent/midnight/test/MidnightForkHarness.t.sol` (каноничный,
  запускаемый).
- Копия-артефакт в нашем репо: `analysis/midnight_fork_harness.t.sol`.

## 1. Команда запуска (важно: НЕ прямой forge-форк)

```
cd /home/claude-agent/midnight
anvil --fork-url https://mainnet.base.org --fork-block-number 48522417 \
      --chain-id 424242 --port 8546 --silent &
forge test --match-path test/MidnightForkHarness.t.sol \
      --fork-url http://127.0.0.1:8546 --evm-version osaka -vv
```

Результат: `Suite result: ok. 8 passed; 0 failed; 0 skipped`.

### Почему НЕ `forge test --fork-url https://mainnet.base.org` напрямую

**Ключевая находка тулинга.** Задеплоенный байткод собран под `evm=osaka` и
использует опкод **`clz`** (EIP-7939, count-leading-zeros) в `UtilsLib.msb` —
он выполняется в КАЖДОМ вызове `liquidate` (цикл по битам `collateralBitmap`).
`forge 1.7.1` при форке **выводит EVM-spec из хардфорк-расписания цепи по её
chainId** (8453 = Base). В этой сборке forge (2026-05-08) расписание Base ещё
pre-osaka ⇒ `clz` даёт `EvmError: NotActivated`, и флаг `--evm-version osaka`
это НЕ переопределяет (chain-detection выигрывает). Проверено: тот же `clz`
в локальном (не-форк) тесте с osaka работает (`clz(1)=255`), т.е. revm forge
опкод поддерживает — ломается именно вывод spec для форка Base.

**Обход:** `anvil` форкает РЕАЛЬНОЕ состояние Base, но рапортует НЕизвестный
forge'у chainId (`424242`). Тогда forge не спец-кейсит цепь и берёт spec из
`--evm-version osaka` ⇒ `clz` активен. `block.chainid=424242` безопасен:
`liquidate`/`touchMarket` не сверяют chainId для УЖЕ созданного рынка
(id хранит `market.chainId=8453`, `touchMarket` при `tickSpacing!=0` пропускает
проверки), а `IdLib.toId(market)` хеширует сохранённый `chainId=8453`, поэтому
id совпадает. Исполняется РЕАЛЬНЫЙ байткод контракта, снятый с Base.

## 2. Целевой рынок и синтез позиции

Рынок из кэша `data/midnight_markets.json`:
`id = 0x02c1253684e6216652adbfffb7486bb72be0bc8975e4b94d7d35e65afe574896` —
**реальный рынок, который уже был ликвидирован он-чейн** (первое `Liquidate`-
событие в кэше). Параметры (сверены `toMarket(id)` на форке == кэш):
loanToken USDC; maturity 1783555200 (2026-07-09 00:00 UTC); коллатералы
[idx0 WETH (lltv 0.86, cursor 0.30, oracle `0xAc2d…3ed`, maxLif
1043841336116910229), idx1 WETH-USDC-collat (lltv 0.98, oracle `0x3F51…Cd7`)];
`liquidatorGate=0` (permissionless), `rcfThreshold=0`.

Синтетический заёмщик `makeAddr("borrower")`: `collateralBitmap=3` (оба
коллатерала активны), `collateral[0]=10 WETH`, `collateral[1]=1e24`
WETH-USDC-collat, `debt=1000e6` units. Over-collateralized ⇒ `badDebt=0`
(обходим социализацию убытка, чтобы не трогать `marketState.totalUnits`).

### Storage-слоты: самокалибровка (не угадывание)

`forge inspect Midnight storageLayout`: `position` @ slot 0. Слот позиции:
`base = keccak256(abi.encode(user, keccak256(abi.encode(id, 0))))`. Упаковка
`struct Position` (2×uint128 на слот): `base+2` = `debt`(low128) |
`collateralBitmap`(high128); `base+3+⌊i/2⌋` = `collateral[i]` (чётный i → low).
Записываем `vm.store`, затем **читаем публичными геттерами
`debt()/collateralBitmap()/collateral()` и `assertEq`** — если геттер вернул
записанное, слот верный. Все 4 калибровочных assert проходят ⇒ раскладка верна.
Реальные токены доливаются `deal()`: WETH на адрес Midnight (чтобы выплатить
seized залог), USDC на MockLiquidator (чтобы вернуть repaidUnits).

`CALLBACK_SUCCESS` из `src/libraries/ConstantsLib.sol` =
`keccak256("morpho.midnight.callbackSuccess")` (не выдумано — импортирован из
контракта; MockLiquidator возвращает именно его).

## 3. Рамп post-maturity — подтверждён [P]

`test_PostMaturityRamp_MatchesFormula`: для Δ ∈ {5м,30м,60м,2ч} сеем позицию,
`_freezeOracles()`, `vm.warp(maturity+Δ)`, вызываем `liquidate` c
`postMaturityMode=true`, `seizedAssets=0.01 WETH` через `MockLiquidator`
(ILiquidateCallback: получил залог ДО repay, аппрувит Midnight на repaidUnits;
своп залог→USDC замокан `deal`). Проверка — **точное** равенство возвращаемого
`repaidUnits` реплике формулы (та же целочисленная арифметика `mulDivUp`):

| Δ | lif (1e18) | repaidUnits (USDC 6dec) |
|---|---|---|
| 5 мин | 1003653444676409185 | 17 941 763 (~17.94) |
| 30 мин | 1021920668058455114 | 17 621 047 (~17.62) |
| 60 мин | **1043841336116910229** | 17 251 005 (~17.25) |
| 2 ч | 1043841336116910229 (капнуто) | 17 251 005 |

lif на 60 мин = `1043841336116910229` = `maxLif` из кэша (idx0) **wei-в-wei**;
на 2ч lif идентичен (потолок `min(maxLif,…)` сработал). repaidUnits монотонно
падает по Δ (растёт бонус ликвидатора) — рамп реально исполнен, не тривиально.
`test_PostMaturity_EmitsLiquidateEvent`: разбор `Liquidate`-лога (topic0 сверен
с mechanics-доком: `0xb137b989…2dad9`) — `postMaturityMode=true`, seized/repaid
совпали, `badDebt=0`, indexed collateral=WETH, borrower верный.

## 4. Liveness-грабли — воспроизведены [P]

- **(a) реверт оракула ЛЮБОГО активированного коллатерала → liquidate ревертит.**
  `test_Liveness_AnyActivatedOracleRevert_Blocks`: `vm.mockCallRevert` на оракуле
  idx1 (НЕ сейзимого; читается ПЕРВЫМ, т.к. `msb(0b11)=1`). liquidate ревертит
  ровно этой ошибкой (`ORACLE_DOWN`). Подтверждает: достаточно падения одного из
  оракулов позиции — прямое требование к боту (мульти-коллатерал = больше
  поверхность liveness-отказа).
- **(b) оракул СЕЙЗИМОГО коллатерала вернул 0 — оба пути задокументированы**
  (idx1 остаётся живым и покрывает долг ⇒ `badDebt=0`, доходим до ветки сейза):
  - **repaid-путь** (`repaidUnits>0, seizedAssets=0`):
    `seizedAssets = repaidUnits·lif/WAD · SCALE/price` при price=0 → **деление на
    ноль, Panic(0x12)**. `test_…_RepaidPath_DivReverts` — ревертит как ожидается.
  - **seized-путь** (`seizedAssets>0, repaidUnits=0`):
    `repaidUnits = seized·0/SCALE·… = 0` → **НЕ ревертит**; залог сейзится, но
    долг НЕ уменьшается (repaid=0). `test_…_SeizedPath_ZeroRepaid` подтверждает
    `outRepaid==0`, `outSeized==0.01 WETH`, `debt` не изменился. Практический
    вывод: при мёртвом (0) оракуле сейзимого не подавать `repaidUnits>0`
    (реверт); seized-путь «отдаёт» залог за 0 — экономически бессмысленно
    инициировать, но код не блокирует.

## 5. Ограничения (честно)

- **(c) "liquidation locked" — НЕ покрыта.** `liquidationLocked(id,user)` читает
  **transient storage** (`LIQUIDATION_LOCK_SLOT`, `tload`) и выставляется ТОЛЬКО
  внутри сеттлмента `take()` (callback продавца, `Midnight.sol:480/511`),
  сбрасываясь в конце транзакции. Прямой вызов `liquidate` видит baseline
  `false` (тест `test_Doc_LiquidationLocked_BaselineFalse` это фиксирует), и
  cheatcode для персистентной записи transient-слота чужого контракта через
  границу вызова нет. Воспроизведение требует оркестрации `take`-с-callback,
  внутри которого дёргается `liquidate` (реэнтранси) — отдельная задача, вне
  рамок harness. Гард присутствует в коде (`Midnight.sol:659-663`).
- **Оракулы заморожены в happy/control-тестах.** Форк-блок (2026-07-12 05:29) на
  ~77ч ПОЗЖЕ maturity рынка (2026-07-09). `vm.warp` к `maturity+Δ` двигает
  `block.timestamp` НАЗАД; реальные MorphoChainlinkOracleV2-фиды при этом ревертят
  (`block.timestamp − updatedAt < 0`, Panic 0x11). Поэтому happy/control-тесты
  `mockCall`'ят оба оракула на РЕАЛЬНЫЕ цены, снятые на свежем форк-блоке
  (setUp читает реальный `price()` и `assertGt(…,0)` — подтверждает оракулы
  живыми). Это тест-артефакт: механику `liquidate` (clz, рамп, RCF-скип,
  transfer, callback) исполняет реальный байткод; заморожено лишь ЧИСЛО цены,
  которое иначе меняет vm.warp назад. Fault-injection тесты (a)/(b) оракулы
  переопределяют. Альтернатива (рынок с maturity в будущем + warp вперёд) даёт
  свою проблему — потенциальный staleness-реверт фида на большом возрасте; выбран
  более детерминированный путь заморозки на реальном значении.
- **anvil-обход spec** (§1) — следствие версии `forge 1.7.1`; на toolchain с
  актуальным Base-хардфорк-расписанием прямой `--fork-url` заработает без anvil.
  Это НЕ влияет на достоверность: исполняется тот же снятый с Base байткод.
- **Δ<60мин требует maturity близко к «сейчас»** для live-бота — здесь неважно
  (warp контролирует Δ), но для M-T2/M-T5 экономика считается на реальном окне.

## 6. Что это даёт плану

M-T3 де-рискнул ИСПОЛНЕНИЕ `liquidate` против реального деплоя ДО любого live:
атомарный путь (залог→callback→repay) работает, рамп-тайминг совпадает с
моделью (питает M-T2 break-even), liveness-грабли подтверждены как реальные
режимы отказа бота (реверт любого оракула; div-by-zero на мёртвом сейз-оракуле).
Live-шаги остаются вне плана (§5 tooling-плана: только решение пользователя после
Gate 1 в monad-liquidator).

## 7. Независимая перепроверка (main-loop, 2026-07-12)

Оркестратор перепроверил офлайн-верифицируемое: harness **компилируется под
`--evm-version osaka`** (артефакты `out/MidnightForkHarness.t.sol/*.json`);
`CALLBACK_SUCCESS = keccak256("morpho.midnight.callbackSuccess") =
0x7f87788e…dbaea2` совпадает с `ConstantsLib.sol:23`; отчётные значения рампа
(maxLif 1.043841 при lltv 0.86/cursor 0.30 = бонус 4.38%) совпадают с day0-замером;
формула рампа в harness = `Midnight.sol:686`. Полный прогон `forge test`
(8 passed) выполнен авторским агентом в изолированном окружении; в main-loop-шелле
повторный форк-прогон уперся в лимит окружения на долгоживущие сетевые процессы
(anvil получает SIGTERM) — это ограничение среды, не дефект harness. Команда
запуска (§1) воспроизводима в окружении с обычным egress.
