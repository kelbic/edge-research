#!/bin/bash
# Деплой MidnightLiquidator.sol на Base с ключа пользователя (foundry keystore).
# Требует: cast wallet import midnight-bot (см. README), файл пароля, газ на адресе.
# Usage: bot/deploy.sh
set -e
MIDNIGHT=0xAdedD8ab6dE832766Fedf0FaC4992E5C4D3EA18A     # ядро Midnight (Base)
ROUTER=0x2626664c2603336E57B271c5C0b26F421741e481       # Uniswap SwapRouter02 (Base)
ACCOUNT=${MN_ACCOUNT:-midnight-bot}
PASSFILE=${MN_PASSFILE:-$HOME/.midnight-bot/pw}
RPC=${MN_RPC:-https://mainnet.base.org}
CLONE=/home/claude-agent/midnight                       # форк-репо с интерфейсами

# компиляционная копия с импортами относительно clone
sed 's#../../midnight/src/#./#g' /home/claude-agent/edge-research/bot/MidnightLiquidator.sol \
    > "$CLONE/src/MidnightLiquidator.sol"
cd "$CLONE"
echo "Деплой MidnightLiquidator(midnight=$MIDNIGHT, router=$ROUTER) с аккаунта $ACCOUNT…"
forge create src/MidnightLiquidator.sol:MidnightLiquidator \
    --evm-version osaka \
    --constructor-args "$MIDNIGHT" "$ROUTER" \
    --account "$ACCOUNT" --password-file "$PASSFILE" \
    --rpc-url "$RPC" --broadcast
rm -f "$CLONE/src/MidnightLiquidator.sol"
echo "Готово. Скопируй 'Deployed to: 0x…' и экспортни: export MN_CONTRACT=0x…"
