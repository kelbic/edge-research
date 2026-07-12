// ============================================================================
//  АРТЕФАКТ-КОПИЯ (M-T3 fork-harness) для репо edge-research.
//  Каноничное место запуска — /home/claude-agent/midnight/test/MidnightForkHarness.t.sol
//  (там forge-setup: lib/forge-std, src/Midnight.sol, foundry.toml evm=osaka).
//  Этот файл — точная копия для фиксации артефакта в edge-research; сам по себе
//  вне клона midnight он НЕ компилируется (нужны src/ Midnight и forge-std).
//
//  КАК ЗАПУСКАТЬ (из /home/claude-agent/midnight):
//    1) anvil --fork-url https://mainnet.base.org --fork-block-number 48522417 \
//              --chain-id 424242 --port 8546 --silent &
//    2) forge test --match-path test/MidnightForkHarness.t.sol \
//              --fork-url http://127.0.0.1:8546 --evm-version osaka -vv
//  Ожидаемо: 8 passed. Подробности почему нужен anvil-обход (opcode clz/osaka) —
//  в шапке контракта ниже и в docs/midnight_mt3_forkharness_report.md.
// ============================================================================
// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (c) 2026
//
// ============================================================================
//  M-T3 fork-harness (Фаза B pre-registered плана Направления 8/B)
//  Цель: прогнать НАСТОЯЩИЙ задеплоенный байткод Midnight на Base через
//        реальный код liquidate() (post-maturity рамп + liveness-грабли),
//        синтезировав позицию-заёмщика на форке через cheatcodes.
//  ВСЁ ЛОКАЛЬНО (форк). Ни одной транзакции в реальную сеть.
//
//  ЗАПУСК (из /home/claude-agent/midnight) — ДВА шага, см. пояснение ниже:
//    1) anvil --fork-url https://mainnet.base.org --fork-block-number 48522417 \
//              --chain-id 424242 --port 8546 --silent &
//    2) forge test --match-path test/MidnightForkHarness.t.sol \
//              --fork-url http://127.0.0.1:8546 --evm-version osaka -vv
//
//  ПОЧЕМУ НЕ `forge test --fork-url https://mainnet.base.org` НАПРЯМУЮ:
//    Задеплоенный байткод собран под evm=osaka и использует опкод clz (EIP-7939,
//    UtilsLib.msb) в КАЖДОМ liquidate. forge 1.7.1 при форке ВЫВОДИТ EVM-spec из
//    хардфорк-расписания цепи по её chainId (8453=Base). В этой сборке forge
//    расписание Base ещё pre-osaka => clz даёт `EvmError: NotActivated`, и
//    --evm-version osaka это НЕ переопределяет (chain-detection выигрывает).
//    Обход: anvil форкает РЕАЛЬНОЕ состояние Base, но рапортует НЕизвестный
//    chainId (424242) => forge не спец-кейсит цепь и берёт spec из --evm-version
//    osaka => clz активен. block.chainid=424242 безопасен: liquidate/touchMarket
//    не сверяют chainId для уже созданного рынка (id хранит market.chainId=8453).
//
//  Задеплоенный Midnight: 0xAdedD8ab6dE832766Fedf0FaC4992E5C4D3EA18A
//    (байткод бит-в-бит = forge-сборка тега e6f2bf2 = 336b924, см. day0-отчёт).
//  Целевой рынок: 0x02c125...4896 — РЕАЛЬНЫЙ рынок, который уже был
//    ликвидирован он-чейн post-maturity (первое Liquidate-событие в кэше).
//    loanToken=USDC, коллатералы [WETH(idx0), WETH-USDC-collat(idx1)],
//    maturity=1783555200 (2026-07-09 00:00 UTC).
// ============================================================================

import {Test, Vm} from "../lib/forge-std/src/Test.sol";
import {stdError} from "../lib/forge-std/src/StdError.sol";

import {IMidnight, Market, CollateralParams} from "../src/interfaces/IMidnight.sol";
import {ILiquidateCallback} from "../src/interfaces/ICallbacks.sol";
import {IOracle} from "../src/interfaces/IOracle.sol";
import {
    WAD,
    ORACLE_PRICE_SCALE,
    TIME_TO_MAX_LIF,
    CALLBACK_SUCCESS,
    maxLif
} from "../src/libraries/ConstantsLib.sol";

interface IERC20Min {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @dev Контракт-ликвидатор: реализует ILiquidateCallback.
///      Внутри onLiquidate залог уже получен (receiver==this) ДО того, как
///      Midnight требует вернуть долг. Своп залог->loanToken МОКАЕТСЯ:
///      тест предварительно фандит этот контракт loanToken'ом через deal()
///      (= выручка от свопа). Здесь только аппрувим Midnight на repaidUnits.
///      Тестируем ФЛОУ Midnight (атомарность: залог до repay), не DEX.
contract MockLiquidator is ILiquidateCallback {
    address public immutable MIDNIGHT;

    constructor(address midnight_) {
        MIDNIGHT = midnight_;
    }

    function onLiquidate(
        address, /* caller */
        bytes32, /* id */
        Market memory market,
        uint256, /* collateralIndex */
        uint256, /* seizedAssets */
        uint256 repaidUnits,
        address, /* borrower */
        address, /* receiver */
        bytes memory, /* data */
        uint256 /* badDebt */
    ) external returns (bytes32) {
        // Залог уже у нас. Аппрувим Midnight, чтобы он мог pull'ить repaidUnits.
        IERC20Min(market.loanToken).approve(MIDNIGHT, repaidUnits);
        return CALLBACK_SUCCESS;
    }
}

contract MidnightForkHarness is Test {
    // --- Задеплоенные адреса (Base) ---
    IMidnight constant MIDNIGHT = IMidnight(0xAdedD8ab6dE832766Fedf0FaC4992E5C4D3EA18A);
    uint256 constant FORK_BLOCK = 48522417;

    // --- Целевой рынок из кэша data/midnight_markets.json ---
    bytes32 constant MARKET_ID = 0x02c1253684e6216652adbfffb7486bb72be0bc8975e4b94d7d35e65afe574896;
    uint256 constant MATURITY = 1783555200; // 2026-07-09 00:00 UTC
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant WETH = 0x4200000000000000000000000000000000000006; // idx0 (сейзим этот)
    address constant WETH_USDC_COLLAT = 0x8324a9D8921453b8B970Aa8a359cAa84E0a02efb; // idx1
    address constant ORACLE0 = 0xAc2d52439742a902EFb0C6F542C5d26B574bc3ed; // ETH/USD (idx0)
    address constant ORACLE1 = 0x3F511770d3407bB546f2D944eDdD0E172B825Cd7; // idx1

    // topic0 события Liquidate (сверяется офчейн-keccak, см. morpho_v2_mechanics §1)
    bytes32 constant LIQUIDATE_TOPIC0 =
        keccak256("Liquidate(address,bytes32,address,uint256,uint256,address,bool,address,address,uint256,uint256,uint256)");

    // --- Синтетическая позиция ---
    address BORROWER;
    address RECEIVER; // получатель залога == MockLiquidator
    MockLiquidator liq;

    Market market; // прочитан через toMarket(id)

    // Параметры idx0 (для рамп-формулы)
    uint256 lltv0;
    uint256 cursor0;

    // Реальные цены оракулов, снятые на СВЕЖЕМ форк-блоке (см. _freezeOracles).
    uint256 price0Real;
    uint256 price1Real;

    // Значения ledger позиции
    uint256 constant COLL0 = 10e18; // 10 WETH (сейзабельный залог, ~$18k)
    uint256 constant COLL1 = 1e24; // 1e6 WETH-USDC-collat (покрывает долг => badDebt=0)
    uint256 constant DEBT = 1000e6; // 1000 units (~$1000 при maturity)
    uint128 constant BITMAP = 3; // idx0 | idx1 активированы

    function setUp() public {
        // Требуется АМБИЕНТНЫЙ форк из CLI с osaka-spec (см. шапку файла: anvil с
        // custom chain-id 424242 + forge --evm-version osaka). НЕ вызываем
        // vm.createSelectFork(): он вывел бы spec из хардфорк-расписания Base
        // (< osaka в этой сборке forge) и сломал бы clz в liquidate.
        require(
            address(MIDNIGHT).code.length > 0,
            "run on the osaka Base fork (see file header: anvil custom chain-id + --evm-version osaka)"
        );

        BORROWER = makeAddr("borrower");
        liq = new MockLiquidator(address(MIDNIGHT));
        RECEIVER = address(liq);

        // Читаем РЕАЛЬНЫЙ рынок с форка и сверяем с кэшем.
        market = MIDNIGHT.toMarket(MARKET_ID);
        assertEq(market.loanToken, USDC, "loanToken mismatch vs cache");
        assertEq(market.maturity, MATURITY, "maturity mismatch vs cache");
        assertEq(market.collateralParams.length, 2, "expected 2 collaterals");
        assertEq(market.collateralParams[0].token, WETH, "idx0 token");
        assertEq(market.collateralParams[0].oracle, ORACLE0, "idx0 oracle");
        assertEq(market.collateralParams[1].token, WETH_USDC_COLLAT, "idx1 token");
        assertEq(market.collateralParams[1].oracle, ORACLE1, "idx1 oracle");

        lltv0 = market.collateralParams[0].lltv;
        cursor0 = market.collateralParams[0].liquidationCursor;

        // Кросс-чек topic0 из mechanics-дока.
        assertEq(
            LIQUIDATE_TOPIC0,
            0xb137b989b9fd54b984273db8f16364f52f383aaca56076a320c1896e9fc2dad9,
            "Liquidate topic0 mismatch"
        );

        _seedPosition();

        // Снимаем РЕАЛЬНЫЕ цены оракулов на свежем форк-блоке (block.timestamp ==
        // время форка, updatedAt фида ~сейчас => без underflow). Ниже (в тестах)
        // мы warp'аем block.timestamp к maturity+Δ на ~77ч НАЗАД; реальные фиды
        // MorphoChainlinkOracleV2 при этом ревертят (block.timestamp-updatedAt < 0,
        // panic 0x11). Поэтому в happy/control-тестах ЗАМОРАЖИВАЕМ оракулы на эти
        // реальные значения (_freezeOracles) — liquidate исполняется РЕАЛЬНЫМ
        // байткодом, но с детерминированной ценой (артефакт теста, механику не
        // меняет). Fault-injection тесты (a)/(b) переопределяют нужный оракул.
        price0Real = IOracle(ORACLE0).price();
        price1Real = IOracle(ORACLE1).price();
        assertGt(price0Real, 0, "oracle0 must be live at fork");
        assertGt(price1Real, 0, "oracle1 must be live at fork");
    }

    /// @dev Замораживает оба оракула на реальные (снятые на форке) цены, чтобы
    ///      vm.warp назад не ломал Chainlink-фиды. Оракул-контракт при этом уже
    ///      подтверждён живым (setUp прочитал реальную цену).
    function _freezeOracles() internal {
        vm.mockCall(ORACLE0, abi.encodeWithSelector(IOracle.price.selector), abi.encode(price0Real));
        vm.mockCall(ORACLE1, abi.encodeWithSelector(IOracle.price.selector), abi.encode(price1Real));
    }

    // ------------------------------------------------------------------
    //  Синтез позиции через vm.store + САМОКАЛИБРОВКА через public getters.
    //  storage layout (forge inspect Midnight storageLayout):
    //    position : mapping(bytes32=>mapping(address=>Position)) @ slot 0
    //  Position (2 x uint128 на слот):
    //    base+0: credit | pendingFee
    //    base+1: lastLossFactor | lastAccrual
    //    base+2: debt(low128) | collateralBitmap(high128)
    //    base+3+: uint128[128] collateral, 2 на слот (i even -> low, odd -> high)
    // ------------------------------------------------------------------
    function _positionBaseSlot(address user) internal pure returns (uint256) {
        // position[id] -> keccak(id, 0); position[id][user] -> keccak(user, inner)
        bytes32 inner = keccak256(abi.encode(MARKET_ID, uint256(0)));
        return uint256(keccak256(abi.encode(user, inner)));
    }

    function _seedPosition() internal {
        uint256 base = _positionBaseSlot(BORROWER);

        // slot base+2: debt(low128) | collateralBitmap(high128)
        vm.store(
            address(MIDNIGHT),
            bytes32(base + 2),
            bytes32((uint256(BITMAP) << 128) | DEBT)
        );
        // slot base+3: collateral[0](low128) | collateral[1](high128)
        vm.store(
            address(MIDNIGHT),
            bytes32(base + 3),
            bytes32((COLL1 << 128) | COLL0)
        );

        // --- Самокалибровка: если getter вернул что записали, слот верный ---
        assertEq(MIDNIGHT.debt(MARKET_ID, BORROWER), uint128(DEBT), "calib: debt slot");
        assertEq(MIDNIGHT.collateralBitmap(MARKET_ID, BORROWER), BITMAP, "calib: bitmap slot");
        assertEq(MIDNIGHT.collateral(MARKET_ID, BORROWER, 0), uint128(COLL0), "calib: collateral[0]");
        assertEq(MIDNIGHT.collateral(MARKET_ID, BORROWER, 1), uint128(COLL1), "calib: collateral[1]");
    }

    /// @dev Фандит форк реальными токенами: Midnight должен ДЕРЖАТЬ залог,
    ///      чтобы выплатить seizedAssets receiver'у; ликвидатор должен держать
    ///      loanToken, чтобы вернуть repaidUnits.
    function _fund() internal {
        deal(WETH, address(MIDNIGHT), 100e18); // хватит на выплату сейза
        deal(USDC, address(liq), 10_000_000e6); // "выручка свопа" для repay
    }

    // --- Точная реплика целочисленной арифметики контракта ---
    function _mulDivDown(uint256 x, uint256 y, uint256 d) internal pure returns (uint256) {
        return (x * y) / d;
    }

    function _mulDivUp(uint256 x, uint256 y, uint256 d) internal pure returns (uint256) {
        return (x * y + (d - 1)) / d;
    }

    function _min(uint256 x, uint256 y) internal pure returns (uint256) {
        return x < y ? x : y;
    }

    /// @dev Ожидаемый lif по рамп-формуле контракта (post-maturity).
    function _expectedLif(uint256 dt) internal view returns (uint256) {
        uint256 mLif = maxLif(lltv0, cursor0);
        return _min(mLif, WAD + (mLif - WAD) * dt / TIME_TO_MAX_LIF);
    }

    /// @dev Ожидаемый repaidUnits при заданном seizedAssets и цене (точная реплика).
    function _expectedRepaid(uint256 seized, uint256 price0, uint256 dt) internal view returns (uint256) {
        uint256 lif = _expectedLif(dt);
        return _mulDivUp(_mulDivUp(seized, price0, ORACLE_PRICE_SCALE), WAD, lif);
    }

    // ==================================================================
    //  1. HAPPY PATH: post-maturity рамп на РЕАЛЬНОМ байткоде.
    //     Для нескольких Δ проверяем, что repaidUnits (возврат liquidate)
    //     точно совпадает с рамп-формулой lif(Δ), и долг уменьшился.
    // ==================================================================
    function test_PostMaturityRamp_MatchesFormula() public {
        uint256[4] memory deltas = [uint256(5 minutes), 30 minutes, 60 minutes, 2 hours];
        uint256 seized = 0.01e18; // сейзим 0.01 WETH

        for (uint256 k = 0; k < deltas.length; k++) {
            uint256 dt = deltas[k];
            _seedPosition();
            _fund();
            _freezeOracles();
            vm.warp(MATURITY + dt);

            uint256 expRepaid = _expectedRepaid(seized, price0Real, dt);

            uint256 debtBefore = MIDNIGHT.debt(MARKET_ID, BORROWER);

            vm.prank(address(this));
            (uint256 outSeized, uint256 outRepaid) = MIDNIGHT.liquidate(
                market,
                0, // collateralIndex = WETH
                seized, // seizedAssets > 0 (repaidUnits вычисляется)
                0,
                BORROWER,
                true, // postMaturityMode
                RECEIVER,
                address(liq), // callback
                ""
            );

            emit log_named_uint("dt_seconds", dt);
            emit log_named_uint("lif_1e18", _expectedLif(dt));
            emit log_named_uint("repaidUnits", outRepaid);

            assertEq(outSeized, seized, "seized returned");
            // ГЛАВНАЯ ПРОВЕРКА: рамп-формула подтверждена на реальном коде.
            assertEq(outRepaid, expRepaid, "ramp: repaidUnits != formula");
            // Долг уменьшился ровно на repaidUnits.
            assertEq(MIDNIGHT.debt(MARKET_ID, BORROWER), debtBefore - outRepaid, "debt decrease");

            // При Δ >= 60min lif должен быть на потолке maxLif => одинаковый repaid.
            if (dt >= 60 minutes) {
                assertEq(_expectedLif(dt), maxLif(lltv0, cursor0), "lif should cap at maxLif");
            }
        }
    }

    /// @dev Событие Liquidate: postMaturityMode=true, seized/repaid, badDebt=0.
    function test_PostMaturity_EmitsLiquidateEvent() public {
        _seedPosition();
        _fund();
        _freezeOracles();
        uint256 dt = 30 minutes;
        vm.warp(MATURITY + dt);

        uint256 seized = 0.01e18;
        uint256 expRepaid = _expectedRepaid(seized, price0Real, dt);

        vm.recordLogs();
        MIDNIGHT.liquidate(market, 0, seized, 0, BORROWER, true, RECEIVER, address(liq), "");
        Vm.Log[] memory logs = vm.getRecordedLogs();

        bool found;
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].emitter != address(MIDNIGHT)) continue;
            if (logs[i].topics.length < 4) continue;
            if (logs[i].topics[0] != LIQUIDATE_TOPIC0) continue;
            if (logs[i].topics[1] != MARKET_ID) continue;
            // topic2 = collateral (WETH), topic3 = borrower
            assertEq(address(uint160(uint256(logs[i].topics[2]))), WETH, "event collateral");
            assertEq(address(uint160(uint256(logs[i].topics[3]))), BORROWER, "event borrower");

            (
                , /* caller */
                uint256 evSeized,
                uint256 evRepaid,
                bool evPostMat,
                , /* receiver */
                , /* payer */
                uint256 evBadDebt,
                , /* lossFactor */
                /* continuousFeeCredit */
            ) = abi.decode(
                logs[i].data,
                (address, uint256, uint256, bool, address, address, uint256, uint256, uint256)
            );
            assertTrue(evPostMat, "event postMaturityMode must be true");
            assertEq(evSeized, seized, "event seizedAssets");
            assertEq(evRepaid, expRepaid, "event repaidUnits");
            assertEq(evBadDebt, 0, "event badDebt==0 (over-collateralized)");
            found = true;
        }
        assertTrue(found, "Liquidate event not found");
    }

    // ==================================================================
    //  4a. LIVENESS-грабля: реверт оракула ЛЮБОГО активированного коллатерала
    //      блокирует liquidate. Ревертим оракул idx1 (НЕ сейзимого) — доказывает,
    //      что достаточно падения любого оракула позиции.
    // ==================================================================
    function test_Liveness_AnyActivatedOracleRevert_Blocks() public {
        _seedPosition();
        _fund();
        _freezeOracles();
        vm.warp(MATURITY + 30 minutes);

        // Переопределяем оракул idx1 на реверт (idx1 читается ПЕРВЫМ: msb(0b11)=1).
        vm.mockCallRevert(ORACLE1, abi.encodeWithSelector(IOracle.price.selector), bytes("ORACLE_DOWN"));

        vm.expectRevert(bytes("ORACLE_DOWN"));
        MIDNIGHT.liquidate(market, 0, 0.01e18, 0, BORROWER, true, RECEIVER, address(liq), "");
    }

    // ==================================================================
    //  4b. LIVENESS-грабля: оракул СЕЙЗИМОГО коллатерала вернул 0.
    //      Документируем ФАКТИЧЕСКОЕ поведение обоих путей.
    //      (badDebt=0, т.к. idx1 покрывает долг живым оракулом.)
    // ==================================================================

    /// repaid-путь (repaidUnits>0, seizedAssets=0): деление на цену=0 -> Panic 0x12.
    function test_Liveness_SeizedOracleZero_RepaidPath_DivReverts() public {
        _seedPosition();
        _fund();
        _freezeOracles();
        vm.warp(MATURITY + 30 minutes);

        // idx1 остаётся живым (покрывает долг => badDebt=0), сейзимый idx0 -> 0.
        vm.mockCall(ORACLE0, abi.encodeWithSelector(IOracle.price.selector), abi.encode(uint256(0)));

        vm.expectRevert(stdError.divisionError); // Panic(0x12): division by zero
        MIDNIGHT.liquidate(market, 0, 0, 1e6 /* repaidUnits */, BORROWER, true, RECEIVER, address(liq), "");
    }

    /// seized-путь (seizedAssets>0, repaidUnits=0): repaidUnits=seized*0/... = 0.
    /// НЕ ревертит; залог сейзится, долг НЕ уменьшается (repaid=0). Документируем.
    function test_Liveness_SeizedOracleZero_SeizedPath_ZeroRepaid() public {
        _seedPosition();
        _fund();
        _freezeOracles();
        vm.warp(MATURITY + 30 minutes);

        uint256 debtBefore = MIDNIGHT.debt(MARKET_ID, BORROWER);
        uint256 seized = 0.01e18;

        vm.mockCall(ORACLE0, abi.encodeWithSelector(IOracle.price.selector), abi.encode(uint256(0)));

        (uint256 outSeized, uint256 outRepaid) =
            MIDNIGHT.liquidate(market, 0, seized, 0, BORROWER, true, RECEIVER, address(liq), "");

        assertEq(outRepaid, 0, "repaid must be 0 when seized-oracle price==0");
        assertEq(outSeized, seized, "collateral still seized");
        assertEq(MIDNIGHT.debt(MARKET_ID, BORROWER), debtBefore, "debt unchanged (repaid 0)");
    }

    // ==================================================================
    //  4c. LIVENESS-грабля: "liquidation locked" — НЕ ПОКРЫТА (ограничение).
    //      liquidationLocked хранится в TRANSIENT storage (LIQUIDATION_LOCK_SLOT)
    //      и выставляется только внутри take()-сеттлмента (callback продавца),
    //      сбрасываясь в конце транзакции. Прямой вызов liquidate его не видит
    //      (baseline == false). Воспроизведение требует оркестрации
    //      take-with-callback реэнтранси — вне рамок этого harness.
    //      Здесь фиксируем baseline и наличие гарда (Midnight.sol:659-663).
    // ==================================================================
    function test_Doc_LiquidationLocked_BaselineFalse() public view {
        assertFalse(MIDNIGHT.liquidationLocked(MARKET_ID, BORROWER), "baseline must be false");
    }

    // ==================================================================
    //  5. КОНТРОЛЬ РЕАЛИЗМА.
    // ==================================================================

    /// До maturity (block.timestamp == maturity, не >): postMode -> NotLiquidatable.
    function test_Control_BeforeMaturity_PostMode_Reverts() public {
        _seedPosition();
        _fund();
        _freezeOracles();
        vm.warp(MATURITY); // require block.timestamp > maturity => не выполнено

        vm.expectRevert(IMidnight.NotLiquidatable.selector);
        MIDNIGHT.liquidate(market, 0, 0.01e18, 0, BORROWER, true, RECEIVER, address(liq), "");
    }

    /// Normal-mode на здоровой позиции (debt << maxDebt) -> NotLiquidatable.
    function test_Control_NormalMode_HealthyPosition_Reverts() public {
        _seedPosition();
        _fund();
        _freezeOracles();
        vm.warp(MATURITY + 30 minutes); // время не важно для normal-mode

        vm.expectRevert(IMidnight.NotLiquidatable.selector);
        MIDNIGHT.liquidate(market, 0, 0.01e18, 0, BORROWER, false, RECEIVER, address(liq), "");
    }
}
