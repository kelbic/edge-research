# Направление 8: карта инстансов challenger/fisherman-bounties (Gate 0)

Снимок 2026-07-05. Pre-registration: `docs/dir8_dir_morpho_preregistration.md`
(коммит `673f0d8`). Дискриминатор класса: выигрыш по объективному proof
(детерминизм) vs по голосу/стейку (→ P1, режим 6, вне класса). [P] = код/он-чейн/
офиц. доки; [S] = вторичное; UNVERIFIED помечено.

## Сводная таблица (ядро Gate 0)

| Инстанс | Детерминизм | Permissionless | Bond | Награда челленджеру | **Выплачено хоть раз?** |
|---|---|---|---|---|---|
| EigenLayer core (AllocationManager) | нет — слэшит permissioned slasher AVS'а [P: `require(msg.sender == getSlasher(operatorSet))`] | нет (роль AVS) | — | **0% челленджеру по ELIP-006** (redistribution → immutable recipient AVS'а; clear permissionless, но платит recipient'у) | 15 слэшей за 14 мес; 0 adversarial, 0 challenger-triggered |
| Othentic (фреймворк) | по докам — объективный proof (double/incorrect attestation; но «истина» для incorrect = ⅔-консенсус AVS); **в публичном коде механизма НЕТ** | **НЕТ** — только зарегистрированный оператор (`--slashing-challenger`); «any EOA» — ошибка вторичного пересказа [S: Medium], противоречит офиц. докам | стейк оператора (роль), бонд не документирован | флэт-фи `challengerRewardFee` от AVS, первому | никаких событий не найдено; деплой-адресов нет; UNVERIFIED/likely 0 |
| Symbiotic core | нет — middleware-assert без проверки фолта [P: BaseSlasher.sol `NotNetworkMiddleware`] | нет | — | нет (funds → burner/vault-хуки) | **2 слэша за всю историю** (~$200 суммарно, 2025-08-15, Cap protocol) [P: полный скан] |
| Karak → OpenGDP | нет (DSS-дискреция + вето-мультисиг «for any reason») | нет | — | нет | никогда; протокол пивотнулся (GitHub 404, TVL −99.4%) |
| LayerZero базовый | — (санкций к DVN НЕТ вообще: Kelp-эксплойт 04.2026, $292M, последствий DVN — ноль) | — | — | — | — |
| LayerZero EigenZero DVN | нет — 5-member Security Council | да | 10 wETH (~$30k) | нет (компенсация жертвам) | никогда |
| Hyperlane CheckpointFraudProofs | **ДА** [P: код] | **ДА** (proof); слэшинг — Foundation+Council | нет | **НЕТ** (fraud-оракул без награды) | слэшей не было |
| Across (UMA) | нет — DVM-голос (P1) | да | ~0.45 ETH | бонд проигравшего | диспуты не задокументированы (UNVERIFIED) |
| Nomad | слэшинг был СТАБОМ `FakeSlashed` [P: код] | — | — | никогда не существовала | мёртв с 2022 |
| Connext/Everclear | нет (watcher = whitelist, только pause) | нет | — | нет | — |
| IBC misbehaviour / Cosmos x/evidence | **ДА** | **ДА** | газ | **НЕТ** («SHOULD be submitted», альтруизм Hermes) | продакшн-фрод-фризов не найдено |
| Chainlink Staking v0.2 alerting | **ДА** (staleness ETH/USD >3ч, он-чейн конфиг) | нет (только стейкеры + 20-мин приоритет операторов) | стейк | **фикс 7,000 LINK первому** | **0 алертов за 3.5 года** [P: полный скан v0.1+v0.2] |
| Pyth OIS | полу (решает Pythian Council) | форум | — | нет | 0 слэшей; rewards→0 (04.2026), Pythnet sunset Q3-26 |
| API3 claims | нет (Kleros-жюри; клеймант = застрахованный dApp) | нет | — | — | репо archived 2024 |
| RedStone AVS | «does not implement any slashing» [P: доки] | — | — | — | — |
| δ OP Stack FDG | **ДА** | **ДА** | 0.08 ETH вход, ~14+ ETH участие, до ~60 ETH/клейм, игра ~631 ETH | бонды проигравших, winner-take-all | игры существуют; конкретные выигрыши UNVERIFIED |
| δ Arbitrum BOLD | **ДА** | да (bonding pools) | 79–3,600 ETH | рефанд + 1% defender's bounty (через DAO) | реальных челленджей не было |
| (референс вне класса) Polkadot equivocation | ДА | де-факто блок-авторы | нет | 10% слэша репортеру | **ДА, регулярно** — но захвачено валидаторами |

## Замер частоты (ядро Gate 1) — сделано нашими сканами [P]

- **EigenLayer mainnet, вся история слэшинга (analysis/dir8_slashing.py,
  коммит 5825728; независимо повторено research-агентом с декодом receipts)**:
  15 OperatorSlashed от 3 AVS: 2 теста (`dummy://opset1`), 3 Aleph
  (~2.5k LST-шэров) и 10 EigenYields. **Экономическая поправка [P: receipts]:
  «промо-спам» — не пыль**: EigenYields слэшил СВОЕГО оператора на 100% и
  через redistribution увёл **~28.8k LST (~$90–120M) делегаторского стейка**
  в свои воулты (форум-инцидент t/14799; заголовочные «$250M» он-чейном НЕ
  подтверждаются; description-поле использовалось как реклама воултов).
  Итого: **0 adversarial-фолтов, 0 challenger-выплат за 14 мес**; единственное
  живое применение слэшинга — self-dealing redistribution-свипы владельцев AVS
  (примитив «вывод-через-слэшинг», не энфорсмент). Первый operator-set вообще
  создан лишь 2025-08-01 (+3.5 мес после «slashing live»). Контекст: 99
  operator-sets / 20 AVS, slasher настроен у 21, redistribution у 14 (у 12 из
  14 recipient = сам AVS).
- **Symbiotic, вся история** [P: скан агента, блоки 21.525M–25.469M]: 2 слэша,
  ~$200 суммарно, инициатор — контракт протокола Cap (не челленджер).
- **Chainlink alerting** [P: скан]: 0 AlertRaised за ~3.5 года при живом,
  поддерживаемом конфиге (последний FeedConfigSet 2026-06-26).

## Классификация пустоты (по pre-registered правилу)

Свидетельства смешанные, доминирует **(б) «пусто по дизайну» с примесью (а)**:
- За (б): Chainlink alerting — зрелый (3.5 года), детерминированный, живой
  конфиг, награда солидная ($100k+ по курсу LINK) — и НОЛЬ событий: детеррент
  работает, фид не бывает stale 3 часа. Polkadot: единственное место, где
  баунти платится регулярно, — и его съели валидаторы (инсайдеры канала).
  IBC: механика есть годы, фродов нет. CoC≫PfC на зрелых системах наблюдаем.
- За (а): на EigenLayer слэшинг просто не сконфигурирован у большинства
  (21/99), redistribution нов (14); молодые AVS не включают слэшинг-флоу.
  НО это не «окно для челленджера»: где слэшинг ВКЛЮЧЁН и работает — челленджер-
  роли с наградой всё равно нет (permissioned slasher).
- Решающее: **дефицит не внимания, а самого механизма** — «permissionless proof
  → награда» не существует нигде в живом виде при bond ≤$10k. Пустота потока
  усугубляется пустотой формы.

## Живой watch-item (единственный)

**Hyperlane**: детерминированные permissionless fraud-proofs УЖЕ в коде
(CheckpointFraudProofs.sol), слэшинг пока council-gated и бесплатный для
репортера. Если заявленный переход к trustless-энфорсменту выйдет с
Polkadot-стайл долей репортеру — first-prover watcher над этим контрактом
становится ровно целевым механизмом. Датчик: релизы hyperlane-monorepo /
docs protocol-economics (каденс — месяц).
