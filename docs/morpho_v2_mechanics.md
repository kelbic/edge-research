# Morpho V2 (Midnight): механика ликвидаций по коду (Задача B)

Снимок 2026-07-05. Pre-registration: `docs/dir8_dir_morpho_preregistration.md`
(коммит `673f0d8`). Источник — КОД, не блог: `morpho-org/midnight`
`src/Midnight.sol` (1018 строк) @ commit `336b924` (2026-07-03), whitepaper
2026-05-28; topic0 сверены offline-keccak двумя независимыми путями.
[P] = первоисточник (код/он-чейн/офиц. реестр), [S] = вторичное.

## 0. Статус деплоя — ГЛАВНЫЙ ФАКТ: Markets V2 НЕ живёт

- «Markets V2» ре-архитектурирован и переименован в **Morpho Midnight**
  (fixed-rate/fixed-maturity, мульти-коллатерал, offer/intent-based; оффчейн
  offer-book «midnightMempool», он-чейн сеттлмент `take`).
- **Не задеплоен ни на одном чейне** [P]: офиц. реестр адресов SDK
  (`morpho-ts/src/addresses.ts` @ 2026-07-03) имеет поле `midnight` — пусто для
  ВСЕХ чейнов; в репо нет деплой-скриптов/адресов; аудиты — все DRAFT
  (Spearbit 04.2026, Blackthorn/TrustSec 05.2026); $400k публичный
  аудит-конкурс — конец мая 2026. **Borrow-поток V2 = $0.**
- [S, НЕНАДЁЖНО] Статьи «Midnight launched» (ainvest/kucoin/phemex) = релиз
  кода/вайтпейпера, опровергнуто [P] (класс ошибки «блог 50% vs код 5%»).
- **Vaults V2 живёт** (~30.09.2025, ~20 чейнов, вкл. Base
  `0x4501…5857`, Monad `0x8B2F…bb0c`, Flare) — но это ЧИСТО lender-side:
  аллокация через адаптеры в **Blue (V1)** рынки; «Morpho Market V2 Adapter —
  WIP» (README verbatim). **Ликвидации потока Vaults V2 = стандартный Blue
  `liquidate`** — для нашего прод-бота ничего не меняется.
- Flare-деплой ~02.2026 = Blue V1 + VaultV2Factory, НЕ Markets V2 [P: реестр].
- Morpho всего (DeFiLlama 2026-07-05): TVL $7.02B; borrow: Ethereum $1.88B +
  Base $1.54B = ~93% всего; Monad $57M borrowed (Blue V1).

## 1. Механика ликвидации Midnight [P: код]

Рынок: изолированный, immutable, permissionless-создание (`touchMarket`);
`Market{…, CollateralParams[] (до 128 коллатералов; у заёмщика до 16), maturity,
rcfThreshold, enterGate, liquidatorGate}`. Долг — zero-coupon-юниты к maturity.

**Один вход `liquidate(...)`, два триггера:**
1. **Normal mode**: `debt > maxDebt = Σ collateral_i × price_i × lltv_i` —
   health-based как Blue, но сумма по коллатералам.
2. **Post-maturity mode**: `block.timestamp > maturity` → **ЛЮБОЙ заёмщик с
   долгом ликвидируем, здоровье не важно**. Авто-ролловера НЕТ
   (`CannotIncreaseDebtPostMaturity`); repay доступен; ролловер до maturity —
   забота заёмщика (новые офферы).

**Механизм закрытия — НЕ аукцион:**
- Normal: **fixed-spread instant FCFS, формула Blue**:
  `lif = 1/(1 − cursor×(1−lltv))`, но cursor — пер-коллатеральный параметр
  рынка из governance-вайтлиста (тиры только добавляются), не хардкод 0.3.
  Ограничения: `maxLif ≤ 2e18`, `lltv×maxLif ≤ 0.999e36`. Конкретные cursors —
  TBD (ничего не задеплоено; в доках SDK пример 0.25e18 → ~3.6% бонус при
  LLTV 0.86).
- Post-maturity: **линейный рамп** `lif = min(maxLif, 1 + (maxLif−1)×(t−maturity)/3600с)`
  (`TIME_TO_MAX_LIF = 60 minutes`, ConstantsLib): бонус 0% в момент maturity →
  maxLif за час. Экономически reverse-Dutch (как Euler V2), но это обычный
  FCFS-вызов: каждый бот выбирает свой break-even момент входа. **Календарь
  maturity-окон реконструируем заранее из логов MarketCreated** (maturity —
  поле структуры в событии).
- **RCF (recovery close factor)** — отличие от Blue: в normal mode repay
  капирован «ровно до восстановления здоровья» (только частичные ликвидации),
  кроме дюст-гарда `rcfThreshold` → полная. **Post-maturity RCF отключён** —
  сейзабельна вся позиция.
- Bad debt: `debt − Σ collValue_i/maxLif_i`, социализируется НЕМЕДЛЕННО внутри
  `liquidate` хэйркатом `lossFactor` на всех кредиторов.
- **Permissionless, НО**: рынок может быть создан с `liquidatorGate` →
  `canLiquidate(msg.sender)` — **возможны KYC/whitelist-гейтед ликвидации**
  (пер-рынок, immutable). `liquidatorGate = 0` → полностью permissionless.
- Callback-флоу как в Blue (`ILiquidateCallback`, залог до repay).
- Liveness-грабли для бота [P: комменты LIVENESS]: `liquidate` ревертит, если
  оракул ЛЮБОГО активированного коллатерала ревертит; если оракул сейзимого
  вернул 0 (repaid-путь); во время «liquidation locked» в callback `take`.
- Pre-liquidations в ядре НЕТ (Blue-only продукт); де-факто grace period =
  60-мин рамп + RCF.

**Сигнатуры (offline-keccak, два независимых метода):**
```
Liquidate(address,bytes32,address,uint256,uint256,address,bool,address,address,uint256,uint256,uint256)
  (indexed: id_, collateral, borrower; postMaturityMode — в data)
  topic0 0xb137b989b9fd54b984273db8f16364f52f383aaca56076a320c1896e9fc2dad9
MarketCreated((uint256,address,address,(address,uint256,uint256,address)[],uint256,uint256,address,address),bytes32)
  topic0 0xdbf3e95a2290945645820c722294f678e0b3522a7dcf3cf2e2870268bf6c9472
```

## 2. Оракулы

- Пер-КОЛЛАТЕРАЛЬНЫЙ выбор оракула (тоньше Blue), интерфейс идентичен Blue
  (`IOracle.price()`, скейл 1e36). Живых рынков нет → провайдер-микс N/A;
  ожидание — реюз Blue-экосистемы (MorphoChainlinkOracleV2 и т.п.).
  OEV/SVR-специфики в репо НЕТ.
- Структурная заметка: мульти-коллатерал → большая поверхность
  оракул-liveness-отказа (реверт любого из оракулов блокирует ликвидацию).

## 3. Конкуренция/тулинг

- Офиц. `morpho-blue-liquidation-bot` — Blue-only (обновлён 2026-07-03),
  Midnight-поддержки нет. Доки по ликвидациям — Blue-only. Публичных
  Midnight-ликвидаторов не найдено. `midnight-sdk` — только offer-флоу.
  **Поле реально пустое** — тулинг под maturity-класс не написан никем.

## 3b. Разворотный пункт закрыт: post-maturity ликвидация permissionless-capable [P: код]

Проверено по Midnight.sol @336b924 (строки 636–637): `liquidatorGate`-проверка —
**единый require ДО ветки `postMaturityMode`**, т.е. одинаково гейтит обычную и
post-maturity ликвидацию. При `market.liquidatorGate == address(0)` — **полностью
permissionless** (любой `msg.sender`, включая post-maturity). Гейт опционален
пер-рынок (immutable, часть id, задаётся создателем рынка); протокол НЕ форсирует
дефолтный гейт. Вывод для «окно vs мёртво»: **не структурно закрыто** — окно живо
для {read-only, $0}, ЕСЛИ рынки деплоятся с gate=0 (ожидаемо для permissionless-
Morpho-стиля), и мертво-для-нас, если кураторы ставят KYC-гейт. Это эмпирический
вопрос деплоя, не кода → усиливает monitoring (механизм нас не исключает), не
переводит в prepare-tooling (рынков нет).

## 4. Вердикт по pre-registered критериям B: **monitoring**

Механика не-аукционная (FCFS + тайминг-рамп) — киллер режима 1 НЕ сработал;
но рынки не живы, поток $0 < порога $10M → prepare-tooling невозможен по
критерию «живой поток». Датчики переоткрытия:
- (а) поле `midnight` в `morpho-ts/src/addresses.ts` становится непустым /
  события MarketCreated на новом адресе;
- (б) Market V2 Adapter в vault-v2 перестаёт быть WIP (routing lender-ликвидности
  в Midnight = бутстрап borrow-потока);
- (в) финальные (не-DRAFT) аудиты в `midnight/audits/`.
Каденс: раз в 2 недели (дешёвый: git-чек двух файлов).

**Сильнейший контраргумент против monitoring (за более агрессивную подготовку):**
post-maturity сегмент — «расписанное» событие с пустым полем тулинга; первый
ликвидатор с maturity-календарём + break-even-тайминг моделью получает
Base-тейл-подобное окно, и календарь известен ЗАРАНЕЕ из MarketCreated. Ответ:
писать код до деплоя контрактов = ставка на неизменность DRAFT-аудируемого кода;
дешевле держать датчик (а)-(в) и войти в первые дни после деплоя.

## 5. Передача в monad-liquidator (независимо от вердикта)

1. **Maturity/rollover-мониторинг** как будущее расширение бота: календарь из
   MarketCreated, tick-лист maturity-окон, модель оптимального t входа в рамп
   (наша EV-методология тайминга напрямую применима; латентность НЕ решает).
2. **liquidatorGate-вотчер**: доля гейтед-рынков = мера огораживания поля.
3. **Оракул-вотчер V2-рынков** (когда появятся): SVR/Atom-фид = маркер
   OEV-изъятия (режим 1), как на Blue.
4. Vaults V2 уже на Monad — lender-side, ликвидации остаются Blue;
   для запаркованных Monad-мониторов ничего не меняется.
