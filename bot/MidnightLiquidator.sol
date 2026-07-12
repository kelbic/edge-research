// SPDX-License-Identifier: GPL-2.0-or-later
// ============================================================================
//  MidnightLiquidator — боевой callback-контракт ликвидатора Midnight (Base).
//  Часть live-signing бота (Направление 8/B). АТОМАРНЫЙ путь:
//    liquidate(seize залог) -> onLiquidate(своп залог->loan через Uniswap) -> repay.
//
//  БЕЗОПАСНОСТЬ (не «дисциплина», а защита капитала):
//   - onlyOwner на входе (только hot-key бота инициирует ликвидацию);
//   - minOut передаётся ботом = repaidUnits + minProfit ⇒ своп РЕВЕРТИТ, если
//     выручка не покрывает долг + мин.прибыль (гард против убыточного филла на
//     тонком exit — урок SVR Gate 1: оракульная маржа ≠ realized);
//   - callback принимает вызовы ТОЛЬКО от Midnight и только в рамках нашего же
//     liquidate (reentrancy-флаг);
//   - профит (loan − repaid) копится в контракте, выводится owner'ом.
//
//  СТАТУС: НЕ задеплоен. Требует ревью + деплой с ключа пользователя + фандинг газа.
//  Финальная валидация — fork-replay против ПЕРВОЙ реальной позиции (M-T3-стиль),
//  ДО mainnet-деплоя. Артефакт-копия в edge-research; каноничная сборка — в форк-
//  окружении midnight (нужны интерфейсы IMidnight/ICallbacks/ConstantsLib).
// ============================================================================
pragma solidity 0.8.34;

import {IMidnight, Market} from "../../midnight/src/interfaces/IMidnight.sol";
import {ILiquidateCallback} from "../../midnight/src/interfaces/ICallbacks.sol";
import {CALLBACK_SUCCESS} from "../../midnight/src/libraries/ConstantsLib.sol";

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @dev Uniswap SwapRouter02 (Base 0x2626664c2603336E57B271c5C0b26F421741e481).
interface ISwapRouter02 {
    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }
    function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);
}

contract MidnightLiquidator is ILiquidateCallback {
    IMidnight public immutable MIDNIGHT;
    ISwapRouter02 public immutable ROUTER;
    address public owner;
    bool private _inLiquidation;

    error NotOwner();
    error NotMidnight();
    error NotInLiquidation();
    error ProfitGuard(uint256 got, uint256 needed);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address midnight, address router) {
        MIDNIGHT = IMidnight(midnight);
        ROUTER = ISwapRouter02(router);
        owner = msg.sender;
    }

    function setOwner(address newOwner) external onlyOwner {
        owner = newOwner;
    }

    /// @notice Инициировать post-maturity ликвидацию. Бот считает repaidUnits, путь
    /// свопа и minLoanOut (= repaidUnits + minProfit) офчейн (M-T2 + realized-net гард).
    /// Market тянется on-chain из id (toMarket) ⇒ бот шлёт только id+скаляры (простой
    /// calldata, нечего рассинхронизировать с реальным стейтом рынка).
    /// @param id           id рынка (bytes32).
    /// @param collIndex    индекс сейзимого коллатерала.
    /// @param repaidUnits  сколько долга гасим (post-maturity RCF off ⇒ до всей позиции).
    /// @param borrower     заёмщик.
    /// @param swapPath     Uniswap-путь packed (collateral fee ... loanToken).
    /// @param minLoanOut   мин. выручка свопа в loan-wei (гард: ≥ repaidUnits + minProfit).
    function runLiquidation(
        bytes32 id,
        uint256 collIndex,
        uint256 repaidUnits,
        address borrower,
        bytes calldata swapPath,
        uint256 minLoanOut
    ) external onlyOwner returns (uint256 seized, uint256 repaid) {
        Market memory market = MIDNIGHT.toMarket(id);
        _inLiquidation = true;
        bytes memory data = abi.encode(swapPath, minLoanOut);
        // seizedAssets=0 ⇒ Midnight сам считает seize из repaidUnits·lif(t);
        // receiver=this ⇒ залог приходит нам; callback=this ⇒ onLiquidate у нас.
        (seized, repaid) = MIDNIGHT.liquidate(
            market, collIndex, 0, repaidUnits, borrower, true, address(this), address(this), data
        );
        _inLiquidation = false;
    }

    /// @dev Вызывается Midnight ВНУТРИ liquidate после передачи залога нам, ДО repay.
    function onLiquidate(
        address, /*caller*/
        bytes32, /*id*/
        Market memory market,
        uint256 collateralIndex,
        uint256 seizedAssets,
        uint256 repaidUnits,
        address, /*borrower*/
        address, /*receiver*/
        bytes memory data,
        uint256 /*badDebt*/
    ) external returns (bytes32) {
        if (msg.sender != address(MIDNIGHT)) revert NotMidnight();
        if (!_inLiquidation) revert NotInLiquidation();
        (bytes memory swapPath, uint256 minLoanOut) = abi.decode(data, (bytes, uint256));

        address collToken = market.collateralParams[collateralIndex].token;
        address loanToken = market.loanToken;

        // своп всего сейзнутого залога -> loanToken, с гардом minLoanOut
        IERC20(collToken).approve(address(ROUTER), seizedAssets);
        uint256 out = ROUTER.exactInput(ISwapRouter02.ExactInputParams({
            path: swapPath,
            recipient: address(this),
            amountIn: seizedAssets,
            amountOutMinimum: minLoanOut
        }));
        // явный профит-гард (сверх minLoanOut в свопе): выручка должна покрыть долг
        if (out < repaidUnits) revert ProfitGuard(out, repaidUnits);

        // разрешить Midnight стянуть погашение (safeTransferFrom(loanToken, this, ...))
        IERC20(loanToken).approve(address(MIDNIGHT), repaidUnits);
        return CALLBACK_SUCCESS;
    }

    /// @notice Вывести накопленный профит/любой токен owner'у.
    function sweep(address token, uint256 amount) external onlyOwner {
        IERC20(token).transfer(owner, amount);
    }
}
