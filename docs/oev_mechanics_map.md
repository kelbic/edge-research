# Карта механики OEV-площадок (Шаг 1 Gate 0, Направление 7)

Снимок 2026-07-05. Критерии вердиктов зафиксированы ДО этой разведки:
`docs/oev_gate0_criteria.md`, коммит `b277aa2`. Источники: [P] — первоисточник
(доки/гитхаб/он-чейн/governance), [S] — вторичный пересказ. Всё несверенное
помечено НЕ ПОДТВЕРЖДЕНО.

## Сводная таблица

| | Chainlink SVR | Pyth Express Relay | API3 OEV |
|---|---|---|---|
| Аукцион | ETH-mainnet: first-price sealed-bid через Flashbots MEV-Share (+Titan с ~05.2026); Base/Arb/BNB/HyperEVM: sealed-bid через Atlas (off-chain сбор, on-chain сеттлмент) | Off-chain sealed-bid first-price у единственного релеера (Douro Labs) | Исторически: ОТКРЫТЫЕ он-чейн биды на роллапе (окно ~25 с + 5 с award); с 11.2025 — ЗАКРЫТЫЕ private-аукционы с partnered searchers |
| Вход | Permissionless: ETH — только Flashbots-ключ (самогенерируемый); Atlas-чейны — бонд 0.1 ETH (Base), «onboarding… does not require communication with Chainlink Labs» [P] | Бид без auth (проверено по openapi + исходникам); профиль для истории — admin-gated | ЗАКРЫТ для новых: «OEV updates are possible only for the Api3 partnered searchers» [P]; публично — только гонка по ЗАДЕРЖАННЫМ signed-API данным |
| Время на EV+бид | ETH: ~1 слот 12 с (гонка включения); Atlas: окно **2 с** [P] | Не документировано (НЕ ПОДТВЕРЖДЕНО «~блок-тайм») | Было: ~25 с; сейчас: N/A (приватно) |
| Списание бида | Только при włączении/выигрыше (coinbase-transfer / bidToken при исполнении) | Только при успешном исполнении (верифицировано по ExpressRelay.sol) | Бид платится на целевом чейне при fulfillment; на роллапе лочился коллатерал 10% |
| Сплит | Aave: 65% DAO / 35% Chainlink; билдерам ~10% (ETH) или 3.5% (Atlas) | Релеер 6% / DAO 94% платформенной комиссии (OP-PIP-67) | 80% dApp / 20% API3 |
| Fallback | Апдейт без ликвидации; stale → публичный фид через 5–6 блоков (ETH, расхождение источников) / 60 с (Base) | Fallback-биды релеера | Deviation-threshold push-оракул как был |
| Статус 07.2026 | ЖИВОЙ, растёт: $18.3M recaptured all-time, $8.3M за Q1-2026 [P-vendor] | УМИРАЕТ: EVM удалён 04.2025, sunset-предложение 04.2026, продажа Douro за $300k (CO-PIP-122, голосуется) | Жив, но мал (~$10–30k OEV/мес) и закрыт для входа |
| История аукционов | Реконструируемо: MEV-Share hint-архив с 02.2024 (public API, проверен живым запросом) + он-чейн 3-tx фингерпринт | НЕТ: sealed, только свои биды по auth; победители частично видны он-чейн (Mode мёртв, Solana жив) | Роллап ВЫКЛЮЧЕН 11.2025 (RPC/explorer NXDOMAIN); живой субститут — бэкенд их дашборда (по-событийно, с bidAmountUsd) |

## 1. Chainlink SVR — детали

**Механика [P: docs.chain.link/data-feeds/svr-feeds + searcher-onboarding-*]:**
один DON-репорт идёт двумя каналами — публичный агрегатор и DualAggregator,
доставляемый через приватный канал; право забандлить ликвидацию с price-апдейтом
аукционится. Ethereum: сёрчер ловит hint (tx `forward(address,bytes)`, селектор
`0x6fadcf72`, на AuthorizedForwarder) из MEV-Share SSE-стрима, шлёт бандл
[апдейт-по-хэшу + своя ликвидация], бид = ETH на `block.coinbase`; билдер берёт
максимум; ~90% бида рефандится на SVR fee Safe. С ~мая 2026 параллельный
аукцион у Titan (мультиплекс). Atlas-чейны: sealed-bid через
`wss://svr-bid-endpoint.chain.link/ws/solver`, окно **2 с**, Chainlink-нода сама
бандлер (третьи RPC не могут переупорядочить); 2 solver-слота (один
для high-reputation, один открытый; финал — по биду, не по репутации).

**Вход:** ETH — permissionless фактом (MEV-Share без allowlist; `devrel@`
в доках — soft-контакт; НЕ ПОДТВЕРЖДЕНО, даётся ли какой-то hint-детали только
после контакта). Atlas: бонд `depositAndBond()` 0.1 ETH (Base, Atlas v1.6.4
`0x583dcFef0D240DC80753F0F0B26513feE27D9B77`), solver-контракт с
`atlasSolverCall`, EIP-712 подписи; KYC нет [P].

**Покрытие (07.2026):** Ethereum, Base, Arbitrum, BNB, HyperEVM. Aave —
якорь: Phase 1 (AIP #274, 29.03.2025) BTC/LINK/AAVE → Phase 2 (05.2025)
WBTC/cbBTC/WETH/wstETH/… → Phase 3 (06.2025) ETH-корреляты, покрытие 58.6%
Core → **Base+Arbitrum с ~конца марта 2026** (AIP #461; Base: 12 активов —
WETH, wstETH, weETH, cbETH, wrsETH, ezETH, cbBTC, tBTC, LBTC, USDC, syrupUSDC,
AAVE). Сейчас ~75% TVL Aave-Ethereum ≈ ~95% OEV-релевантного. Compound — live
(Q1 2026), Venus — live (BNB). **Morpho: протокольной интеграции НЕТ**
[P: docs.morpho.org упоминает Oval, не SVR], но SVR просачивается
по-рыночно через выбор оракула куратором: Steakhouse WBTC/USDC (Ethereum)
уже на SVR-BTC/USD фиде [P: kitchen.steakhouse.financial 2026-07-02].

**Сверка ключевой цифры (задача промпта):** «~95% аукционов за 1–2 блока,
~0.03% дольше 5 блоков» — **конфляция двух разных статистик** из
LlamaRisk-ревью Phase 1 (2025-03-07, llamarisk.com/research/2025-03-07…):
(а) «~95.5% всех MEV-Share аукционов резолвятся с включением < 3 блоков» —
историческая статистика MEV-Share ВООБЩЕ (Dune, пре-лонч), не живого SVR;
(б) «0.03% всех price-апдейтов пришли ≤5 блоков после предыдущего» — метрика
tail-риска частоты апдейтов, НЕ частота fallback'а. Отчёта Flashbots с такими
цифрами НЕ СУЩЕСТВУЕТ (не найден). Частота срабатывания fallback нигде не
опубликована; LlamaRisk 2026-03-16: fallback «не наблюдался триггерящимся
из-за инфраструктурных проблем».

**Публикованные метрики [P]:** $16M recaptured / $675M ликвидаций / ~3,900
SVR-событий к началу марта 2026 (LlamaRisk в треде 24241); $18.3M all-time,
$8.3M за Q1-2026 [P-vendor: Chainlink Q1 review]. Recapture-rate: 20.9%
ранний (Chaos Labs) → 53% lifetime → 73%+ в последние недели, до 80%+ на
высокобонусном коллатерале. **«93 уникальных сёрчера выигрывали
Flashbots-аукционы»** (к 03.2026, LlamaRisk) — таблицы долей нет в тексте,
живёт в дашбордах (svr.llamarisk.com, dune.com/pull/chainlink-svr).

**Точки данных для Шага 2:** (1) MEV-Share hint-архив
`https://mev-share.flashbots.net/api/v1/history` — жив (проверено запросом:
953M hints с 2024-02-14, пагинация 500); (2) он-чейн фингерпринт выигранного
аукциона (верифицирован на блоке 25430607): tx N `forward` →
`SecondaryRoundIdUpdated(uint32)` (topic0
`0x8d530b9ddc4b318d28fdd4c3a21fcfecece54c1a72a824f262985b99afef009b`) на
DualAggregator; tx N+1 — ликвидация сёрчера (Aave `LiquidationCall`); tx N+2 —
рефанд билдера на SVR fee Safe `0x149b41b1e4c00b5f9aa34b14fd9f84cfd2f014e5`
(= читаемый размер бида). Адреса DualAggregator'ов — из
reference-data-directory.vercel.app/feeds-mainnet.json, кросс-сверены с
bgd-labs/aave-address-book (BTC/USD `0xdc715c…0502`, ETH/USD `0x7c7FdF…E3F8`
и др.). Оговорка: `SecondaryRoundIdUpdated` сам по себе ≠ выигранный аукцион
(невыигранные апдейты падают публичными catch-up'ами ~каждые 300 блоков) —
требовать смежность позиций + рефанд-tx.

## 2. Pyth Express Relay — площадка умирает

**Хедлайн [P: forum.pyth.network]:** 2026-04-10 — «Path Forward»: доход
< $2k/мес против $5–15k/мес опекс, «на $1 расходов Douro DAO видит ~$0.20»;
предложен sunset к концу Q2-2026. 2026-06-30 — CO-PIP-122: продажа продукта
Douro Labs за $300k USDC (голосование идёт на 2026-07-05). **EVM-контракты
удалены из репо 2025-04-11** (единственный документированный EVM-мейннет —
Mode); живая активность — SOL-типы на лимит-ордерах Kamino
(~$852 комиссий/30д по DeFiLlama-адаптеру, запрошено 2026-07-05). Объёмы
рухнули после того, как Jupiter запустил свой RFQ и убрал ER из агрегатора
(WSOL −98%). Лендинг-ликвидаций через ER на EVM в 2026 — ноль
задокументированных; Synthetix/Zerolend — только анонсы 2024, живое
использование НЕ ПОДТВЕРЖДЕНО.

**Механика (историческая):** off-chain sealed-bid first-price у единственного
релеера (Douro Labs с OP-PIP-67, 04.2025; до того Asymmetric Research);
несколько победителей возможны; бид списывается только при успешном исполнении
(верифицировано по ExpressRelay.sol, commit 177c66fa); атомарность апдейт+
ликвидация — свойство конструкции tx сёрчера, НЕ гарантия релея. Длительность
аукциона НЕ документирована («~блок-тайм» — не подтверждено).

**Прозрачность — K4 подтверждён тремя слоями [P]:** (1) в доках нет
history/stats; (2) openapi: история бидов только своя, по bearer-токену,
макс 20 записей; (3) исходники сервера: `get_bids` жёстко скоуплен на
profile.id. Проигравшие биды существуют только в БД оператора. Победители
частично реконструируемы он-чейн (Mode: `MulticallIssued` с бидом в открытую;
Solana: `SubmitBid` инструкция) — но Mode-деплой мёртв.

## 3. API3 OEV — permissionless-вход закрыт, роллап выключен

**Хедлайн [P: api3dao/api3-docs, коммит 6eb2607, 2025-10-27]:** «there will
be no public OEV Network and OEV Auctioneer… we're working with partnered
searchers»; все средства вывести с роллапа до конца ноября 2025. Раздел
oev-searchers удалён из доков 2026-04-22 (коммит d150e78). Текущие доки:
«OEV updates are possible only for the Api3 partnered searchers and not the
general public»; публике оставлен «MEV with Signed APIs» по **задержанным**
данным (длина задержки не опубликована) — структурно позади партнёров.
Критерии отбора партнёров НЕ ДОКУМЕНТИРОВАНЫ.

**Механика (историческая, 2024-07 → 2025-11):** НЕ zk-роллап (анонс Polygon
CDK не реализован) — Arbitrum Nitro AnyTrust L2 у Caldera, chain id 4913,
ETH-газ. Аукционы по-dApp'ово, повторяющиеся окна 30 с (Arbitrum: 15 с) =
25 с бид-фаза + 5 с award; биды — ОТКРЫТЫЕ tx `placeBid` на OevAuctionHouse
`0x34f13A5C0AD750d212267bcBc230c87AEFD35CC5` (можно перебивать в реальном
времени); off-chain Auctioneer (AWS, DAO) выбирает максимум; победитель на
целевом чейне вызывает `payOevBid` → в callback обновляет фид подписанными
данными (cutoff = конец бид-фазы — окно эксклюзивности) и ликвидирует;
flash-loan-style проверка баланса ≥ bid. Коллатерал 10% бида; protocol fee
на тот момент 0 bps; сплит сейчас 80% dApp / 20% API3.

**Реплеябельность — реализованный риск:** RPC (`oev.rpc.api3.org`,
`oev-network.calderachain.xyz`), explorer и bridge — NXDOMAIN (проверено
2026-07-05, независимо: моя проба и разведagent). Деплоймент-запись chain 4913
удалена из api3dao/contracts в v32.0.0 (2025-11-13). «Retrospectively
verifiable on-chain» держалось ровно до тех пор, пока API3 платила за
инфраструктуру. Архивного снапшота не найдено.

**Живой субститут для Шага 2 [P, проверено живыми запросами]:**
`https://oev-dashboard-backend-aws.api3.org` — открытый JSON API официального
дашборда: `GET /liquidations?dapp=<key>` отдаёт по-событийную историю
(txHash, blockNumber, sender, type OEV/MEV/BFL, collateralSeizedUsd,
debtRepaidUsd, incentiveUsd, **bidAmountUsd**, gasCostUsd, protocolFeeUsd;
18-дес. fixed point). Ключи: lendle-mantle (6,364 событий, история с 2023-08),
yei-sei, init-capital-mantle, morpho-api3-ethereum, compound-mantle-usde и др.
Это САМООТЧЁТ API3 → обязателен спот-чек txHash против реальных чейнов
(делается в `analysis/oev_api3.py verify`).

**Покрытие:** OEV-enabled dApps (последний каталог до удаления доков):
Lendle/INIT (Mantle), Yei/Takara (Sei), dTRINITY/MachFi/Stability (Sonic),
Moonwell (Moonbeam), Nerite (Arbitrum), Tokos (Somnia), **4 Morpho-рынка на
Ethereum** (cbBTC/USDC 86%, wstETH/USDC 86%, WBTC/USDC 86%, MVL/USDC 77%) +
OEV-Boosted воулты Yearn (проценты OEV рециклируются через Merkl). Ключи
aave-v3-ethereum, moonwell-base/optimism в дашборде — ПУСТЫЕ (мониторятся,
потока нет). Масштаб: порядка $10–30k OEV/мес на всю программу.

## 4. Кратко: UMA Oval и RedStone Atom

**Oval (UMA + Flashbots, 01.2024):** обёртка над Chainlink-фидами поверх
MEV-Share; ~90% кикбэк протоколу. Единственный прод — 3–4 Morpho-рынка на
Ethereum в 2024: **$8,502 recaptured за всю историю** (Morpho-форум,
2024-08-16). Репо заглохли (oval-contracts — последний пуш 06.2024);
формального shutdown нет (НЕ ПОДТВЕРЖДЕНО) — де-факто дормант, категорию
забрал SVR (Chainlink заявляет ~99% рынка OEV-recapture [S/vendor]).

**RedStone Atom (07.2025, JV с FastLane):** sealed-bid < 300 мс на Atlas,
атомарный сеттлмент. На лонче live только Unichain (Compound, Morpho, Venus,
Upshift). Страница atom (2026-07-05) заявляет live: Monad, MegaETH, Unichain,
BNB, Base, Berachain, HyperEVM; НО счётчики на самой странице — $0
placeholder'ы, публичных данных аукционов НЕТ, а заявление «recaptured more
than $500M» почти наверняка конфляция с индустриальной оценкой утечки OEV.
**Ключевой структурный факт: Chainlink купил Atlas (22.01.2026) и деприкейтит
его деплой у RedStone** [P: The Block] — на чём Atom работает сегодня,
НЕ ПОДТВЕРЖДЕНО. Вход сёрчером: маркетинг «anyone», onboarding-доков нет —
permissionless-ность НЕ ПОДТВЕРЖДЕНА. Для замера непригоден (нет данных) —
по критериям это K4.

## 5. Решающий вопрос: Morpho и наши рельсы

- **Morpho-Base (наш живой трек): OEV-механизма НЕТ** ни на одном значимом
  рынке (кросс-сверено: Chainlink ecosystem page — только Data Feeds;
  docs.morpho.org; каталог API3; ключ moonwell-base пуст). Тейл остаётся
  legacy-каналом открытых гонок — тезис STATE §18 подтверждён.
- **Вектор эрозии конкретизировался:** SVR заходит в Morpho НЕ протокольно,
  а по-рыночно через кураторов (Steakhouse WBTC/USDC на Ethereum — уже);
  API3 OEV-Boosted рынки Morpho на Ethereum живут с 06.2025. Маркер
  shadow-мониторинга прежний: смена оракула/фида на наблюдаемых рынках.
- **Atom заявляет Monad live** — заметка запаркованным Monad-мониторам.
- Следствие для трудозатрат: OEV-сёрчинг = выход на ЧУЖИЕ протоколы
  (Aave/Compound/Venus + long-tail лендеры) — новая интеграционная поверхность,
  переиспользуются EV-подход и fork-replay, но не адаптеры Morpho.

## 6. Латентный бюджет (K5, предварительно)

- SVR Ethereum: ~12-с слот; SVR Atlas (Base/Arb): окно 2 с → наш замеренный
  цикл 166–700 мс проходит (пограничных <500 мс нет). K5 проходим.
- Express Relay: N/A (умирает).
- API3: N/A (вход закрыт); исторические 25 с проходились бы свободно.

## Кросс-чек пирога (Шаг 0 промпта)

- «Aave V3 роздал $23.4M ликвид. инцентивов за 2024» — **НЕ ПОДТВЕРЖДЕНО**
  первоисточником (кандидат: Dune risk_labs, недоступен — HTTP 500);
  порядок величины правдоподобен (средний бонус 5.2% [Pangea 2025-09]).
- «Venus $5.8M за 2024» — НЕ ПОДТВЕРЖДЕНО.
- «Маржа сёрчера в старом канале <10%» — направленно ПОДТВЕРЖДЕНО для
  Ethereum-канала (кейс: бонус $141,477 → $141,416 билдеру [Chainlink];
  RedStone: «>90% уходит билдеру»); на Base/L2 доля другая — наши замеры
  monad-liquidator релевантнее.
- Свежий верифицированный якорь: SVR-Ethereum $675M ликвидаций / $16M
  recaptured / ~3,900 событий к 03.2026 [P: LlamaRisk в Aave-треде 24241].
