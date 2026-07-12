"""T4 (Направление 6, ePBS/Glamsterdam): офлайн-пересчёт газ-модели типовых tx
под repricing-EIP форка. Pre-registration: `docs/epbs_gate0_preregistration.md`
(коммит e2a5dc38…), план: `docs/epbs_t4t5_execution_plan.md` §1. Никакой сети,
stdlib-only, чистые функции + PARAMS-конфиг (числа EIP могут меняться до заморозки).

Три режима расчёта:
  today — действующий mainnet (интринсик 21k, floor EIP-7623 10/40 gas/байт,
          EIP-2929/2930-цены стейт-доступа);
  sfi   — SFI-ветвь Glamsterdam: EIP-7976 (floor 64 gas/байт uniform)
          + EIP-7981 (data-цена access-list) + EIP-8037 (state creation);
  cfi   — sfi + CFI-кандидаты, уже гоняемые на devnet-6/7: EIP-2780
          (декомпозиция интринсика) + EIP-8038 (state-access repricing).

Несущие числа, перепроверенные по raw.githubusercontent.com/ethereum/EIPs (все
[P: дата фетча 2026-07-12]):
  EIP-7976: TOTAL_COST_FLOOR_PER_TOKEN 16 => floor 64 gas/байт (zero==nonzero);
            tx с gas limit < intrinsic + floor невалидна (headroom-правило).
  EIP-7981: access-list data cost 64 gas/байт => 1280/адрес, 2048/ключ поверх
            2930-цен; итог: листинг ключа (3948) на 1848 дороже холодного доступа
            (2100), полный чистый штраф с учётом остаточного warm-доступа +1948.
  EIP-8037: CPSB 1530; новый слот 64*1530 = 97 920 (~4.9x от 20k); новый аккаунт
            120*1530 = 183 600 (7.34x от 25k). Дельта на новый слот +77 920.
  EIP-2780: TX_BASE_COST 12 000; вызов контракта без value: интринсик
            12 000 + COLD_ACCOUNT_ACCESS 3000 = 15 000 (было 21 000); эта же
            декомпозированная база подставляется в floor-формулу 7623/7976.
  EIP-8038: COLD_ACCOUNT_ACCESS 2600->3000, COLD_STORAGE_ACCESS 2100->3000,
            STORAGE_WRITE 2800->10 000, WARM_ACCESS 100, AL-цены 2400/1900->3000/3000.

Расхождение с планом: план §1 цитирует «+1,848 к холодному» — это листинг-vs-cold
без остаточного warm (3948-2100); полный штраф по параметрам EIP = +1948/ключ.
Обе величины считает `al_rule_7981`.

[S]-допущения (модельные, не из первоисточников): доля нулевых байт calldata 0.5
(якорит today-стоимость 20KiB-паттерна = 533k как в Gate 0); стейт-профили
паттернов (счётчики холодных доступов/записей) — синтетические оценки Aave-style
ликвидации и свопа, вынесены в PATTERNS.

Usage:
    python3 -m analysis.epbs_gas_model report   # таблица дельт + правила monad-liquidator
"""
from __future__ import annotations

import sys

# --------------------------------------------------------------------- PARAMS
# ВСЕ параметры EIP — здесь, не в функциях: числа могут меняться до заморозки.
PARAMS = {
    # действующий mainnet (после Pectra; EIP-7623 floor активен)
    "base": {
        "intrinsic_base": 21_000,
        "standard_token_cost": 4,      # 4/16 gas за zero/nonzero байт (token = z + 4*nz)
        "floor_per_token": 10,         # EIP-7623: 10/40 gas за zero/nonzero байт
        "al_address_cost": 2_400,      # EIP-2930
        "al_key_cost": 1_900,          # EIP-2930
        "cold_account_access": 2_600,  # EIP-2929
        "cold_storage_access": 2_100,  # EIP-2929
        "warm_access": 100,            # EIP-2929
        "sstore_update_cold": 5_000,   # SSTORE в существующий слот, 1-е изменение, cold
        "storage_set": 20_000,         # SSTORE 0->x (без cold-доступа)
        "new_account": 25_000,         # GAS_NEW_ACCOUNT (без cold-доступа)
    },
    # [P: EIP-7976, фетч 2026-07-12]
    "eip7976": {"total_cost_floor_per_token": 16},   # floor = 16*4 = 64 gas/байт uniform
    # [P: EIP-7981, фетч 2026-07-12]
    "eip7981": {"al_data_gas_per_byte": 64, "al_address_bytes": 20, "al_key_bytes": 32},
    # [P: EIP-8037, фетч 2026-07-12]
    "eip8037": {"cpsb": 1_530, "state_bytes_per_storage_set": 64,
                "state_bytes_per_new_account": 120},
    # [P: EIP-2780, фетч 2026-07-12]
    "eip2780": {"tx_base_cost": 12_000, "tx_value_cost": 4_244, "transfer_log_cost": 1_756},
    # [P: EIP-8038, фетч 2026-07-12]
    "eip8038": {"cold_account_access": 3_000, "cold_storage_access": 3_000,
                "storage_write": 10_000, "warm_access": 100,
                "al_address_cost": 3_000, "al_key_cost": 3_000},
}

REGIMES = ("today", "sfi", "cfi")


# ------------------------------------------------------------- чистые функции

def regime_params(params: dict, regime: str) -> dict:
    """Эффективные скаляры газ-модели для режима today/sfi/cfi.

    sfi = 7976+7981+8037 поверх base; cfi = sfi + 2780 + 8038. Составные цены:
    sstore_new_slot_cold = cold-доступ (+ STORAGE_WRITE в cfi по таблице 8038)
    + state-gas GAS_STORAGE_SET (8037); суммарная цена tx = сумма обоих
    газ-измерений 8037, поэтому здесь измерения складываются."""
    if regime not in REGIMES:
        raise ValueError(f"неизвестный режим {regime!r}")
    b = params["base"]
    e76, e81, e37 = params["eip7976"], params["eip7981"], params["eip8037"]
    e27, e38 = params["eip2780"], params["eip8038"]
    sfi, cfi = regime in ("sfi", "cfi"), regime == "cfi"
    rp = {
        # EIP-2780: вызов контракта без value = TX_BASE + COLD_ACCOUNT (15k)
        "intrinsic_base": (e27["tx_base_cost"] + e38["cold_account_access"])
                          if cfi else b["intrinsic_base"],
        "standard_token_cost": b["standard_token_cost"],
        "floor_per_token": e76["total_cost_floor_per_token"] if sfi else b["floor_per_token"],
        "floor_uniform": sfi,   # 7976: floor_tokens = 4*байт (zero==nonzero)
        "cold_account": e38["cold_account_access"] if cfi else b["cold_account_access"],
        "cold_storage": e38["cold_storage_access"] if cfi else b["cold_storage_access"],
        "warm": e38["warm_access"] if cfi else b["warm_access"],
        "al_address_cost": e38["al_address_cost"] if cfi else b["al_address_cost"],
        "al_key_cost": e38["al_key_cost"] if cfi else b["al_key_cost"],
        "al_data_per_byte": e81["al_data_gas_per_byte"] if sfi else 0,
    }
    # SSTORE в существующий слот (1-е изменение, cold): 5000 -> 3000+10000=13000 (8038)
    rp["sstore_update_cold"] = (rp["cold_storage"] + e38["storage_write"]) if cfi \
        else b["sstore_update_cold"]
    # SSTORE в новый слот (0->x, cold): 22 100 -> 100 020 (sfi) -> 110 920 (cfi)
    set_cost = e37["state_bytes_per_storage_set"] * e37["cpsb"] if sfi else b["storage_set"]
    rp["sstore_new_slot_cold"] = (rp["cold_storage"]
                                  + (e38["storage_write"] if cfi else 0) + set_cost)
    # новый аккаунт (CALL с value в несуществующий): 27 600 -> 186 200 -> 186 600
    acct_cost = e37["state_bytes_per_new_account"] * e37["cpsb"] if sfi else b["new_account"]
    rp["new_account_cold"] = rp["cold_account"] + acct_cost
    return rp


def floor_7976(calldata_bytes: int, params: dict) -> dict:
    """EIP-7976: floor 64 gas/байт uniform (zero == nonzero) + headroom-правило.

    floor_gas = TOTAL_COST_FLOOR_PER_TOKEN(16) * 4 * байт = 64/байт
    [P: EIP-7976, фетч 2026-07-12]. Headroom: tx с gas limit ниже
    intrinsic + floor_gas НЕВАЛИДНА — лимит обязан покрывать floor даже если
    фактический gasUsed ниже (исполнение floor не отменяет)."""
    per_byte = params["eip7976"]["total_cost_floor_per_token"] * 4
    floor_gas = per_byte * calldata_bytes
    return {
        "floor_per_byte": per_byte,
        "floor_gas": floor_gas,
        "min_gas_limit": params["base"]["intrinsic_base"] + floor_gas,
    }


def surcharge_8037(new_slots: int, new_accounts: int, params: dict) -> dict:
    """EIP-8037: state-creation надбавка. Новый слот 20k -> 97 920 (~4.9x),
    новый аккаунт 25k -> 183 600 (7.34x) [P: EIP-8037, фетч 2026-07-12].
    delta_vs_today — сколько ДОБАВИТСЯ к сегодняшней цене tx (на слот +77 920)."""
    e37, b = params["eip8037"], params["base"]
    slot_new = e37["state_bytes_per_storage_set"] * e37["cpsb"]        # 97 920
    acct_new = e37["state_bytes_per_new_account"] * e37["cpsb"]        # 183 600
    return {
        "slot_cost": slot_new, "slot_cost_old": b["storage_set"],
        "slot_multiple": slot_new / b["storage_set"],
        "account_cost": acct_new, "account_cost_old": b["new_account"],
        "account_multiple": acct_new / b["new_account"],
        "surcharge_total": new_slots * slot_new + new_accounts * acct_new,
        "delta_vs_today": (new_slots * (slot_new - b["storage_set"])
                           + new_accounts * (acct_new - b["new_account"])),
    }


def al_rule_7981(params: dict, regime: str = "sfi") -> dict:
    """EIP-7981: access-list как оптимизация мёртв [P: EIP-7981, фетч 2026-07-12].

    Листинг ключа = 2930-цена + 64*32 данные = 3948, что на 1848 дороже холодного
    доступа (2100) — число плана; полный чистый штраф (листинг + остаточный warm
    - сэкономленный cold) = +1948/ключ, +1180/адрес. В cfi (8038: AL-цены и cold
    по 3000) штраф ключа растёт до +2148. Правило: ДРОП access-list."""
    rp = regime_params(params, regime)
    e81 = params["eip7981"]
    key_listing = rp["al_key_cost"] + rp["al_data_per_byte"] * e81["al_key_bytes"]
    addr_listing = rp["al_address_cost"] + rp["al_data_per_byte"] * e81["al_address_bytes"]
    return {
        "key_listing_cost": key_listing,
        "key_listing_vs_cold": key_listing - rp["cold_storage"],          # 1848 (план)
        "key_net_penalty": key_listing + rp["warm"] - rp["cold_storage"],  # 1948
        "addr_listing_cost": addr_listing,
        "addr_listing_vs_cold": addr_listing - rp["cold_account"],
        "addr_net_penalty": addr_listing + rp["warm"] - rp["cold_account"],
        "verdict": "drop_al" if key_listing + rp["warm"] > rp["cold_storage"] else "keep_al",
    }


def _state_ops_cost(profile: dict, rp: dict) -> int:
    """Стоимость стейт-опов exec-части по ценам режима (профиль — [S]-допущение)."""
    return (profile.get("cold_accounts", 0) * rp["cold_account"]
            + profile.get("cold_slots", 0) * rp["cold_storage"]
            + profile.get("storage_updates", 0) * rp["sstore_update_cold"]
            + profile.get("new_slots", 0) * rp["sstore_new_slot_cold"]
            + profile.get("new_accounts", 0) * rp["new_account_cold"])


def tx_cost(pattern: dict, params: dict, regime: str = "today") -> dict:
    """Полная стоимость tx-паттерна в заданном режиме.

    Формула (по EIP-7623/7976/7981):
      total = intrinsic + al_data + max(std_calldata + exec + al_storage, floor)
    exec переоценивается по режиму: не-стейтовая часть exec_gas фиксирована,
    стейт-опы профиля репрайсятся (8037/8038); AL греет перечисленные доступы
    (экономия cold-warm), листинг оплачивается интринсиком. min_gas_limit —
    headroom-правило 7976/7981 (gasUsed при execution_gas_used=0)."""
    rp = regime_params(params, regime)
    zb = int(round(pattern["calldata_bytes"] * pattern.get("zero_frac", 0.5)))
    nz = pattern["calldata_bytes"] - zb
    std = rp["standard_token_cost"] * (zb + 4 * nz)                     # 4/16 за байт
    floor = (rp["floor_per_token"] * 4 * (zb + nz) if rp["floor_uniform"]
             else rp["floor_per_token"] * (zb + 4 * nz))                # 64 vs 10/40
    profile = pattern.get("state_profile", {})
    exec_gas = (pattern["exec_gas"]
                - _state_ops_cost(profile, regime_params(params, "today"))
                + _state_ops_cost(profile, rp))
    al, al_storage, al_data = pattern.get("al"), 0, 0
    if al:
        e81 = params["eip7981"]
        a, k = al["addresses"], al["keys"]
        al_storage = a * rp["al_address_cost"] + k * rp["al_key_cost"]
        al_data = rp["al_data_per_byte"] * (a * e81["al_address_bytes"]
                                            + k * e81["al_key_bytes"])
        # перечисленные доступы становятся warm вместо cold внутри exec
        exec_gas -= (a * (rp["cold_account"] - rp["warm"])
                     + k * (rp["cold_storage"] - rp["warm"]))
    inner = std + exec_gas + al_storage
    return {
        "total": rp["intrinsic_base"] + al_data + max(inner, floor),
        "intrinsic": rp["intrinsic_base"] + al_data,
        "calldata_std": std, "exec_gas": exec_gas,
        "floor_gas": floor, "floor_binds": floor > inner,
        "min_gas_limit": rp["intrinsic_base"] + al_data + max(std + al_storage, floor),
    }


def deltas(pattern: dict, params: dict) -> dict:
    """Стоимости по трём режимам + дельты (%) против today."""
    c = {r: tx_cost(pattern, params, r) for r in REGIMES}
    base = c["today"]["total"]
    return {
        "today": base, "sfi": c["sfi"]["total"], "cfi": c["cfi"]["total"],
        "d_sfi_pct": 100.0 * (c["sfi"]["total"] - base) / base,
        "d_cfi_pct": 100.0 * (c["cfi"]["total"] - base) / base,
        "floor_binds_sfi": c["sfi"]["floor_binds"],
        "min_gas_limit_sfi": c["sfi"]["min_gas_limit"],
    }


# ----------------------------------------------------- библиотека tx-паттернов
# exec_gas — типовой exec по СЕГОДНЯШНИМ ценам; state_profile — [S]-оценка
# стейт-опов внутри exec (без tx.to-тача, он в интринсике). zero_frac=0.5 —
# [S]-допущение Gate 0 (ABI-паддинг), якорит today(20KiB)=533k из pre-registration.
LIQ_PROFILE = {"cold_accounts": 8, "cold_slots": 25, "storage_updates": 10,
               "new_slots": 0, "new_accounts": 0}
SWAP_PROFILE = {"cold_accounts": 4, "cold_slots": 12, "storage_updates": 6,
                "new_slots": 0, "new_accounts": 0}

PATTERNS = [
    {"name": "liq_direct_800B", "desc": "ликвидация ~300k exec, calldata 800B (прямой путь)",
     "calldata_bytes": 800, "zero_frac": 0.5, "exec_gas": 300_000,
     "state_profile": LIQ_PROFILE},
    {"name": "swap_800B", "desc": "своп, компактный роутинг (800B)",
     "calldata_bytes": 800, "zero_frac": 0.5, "exec_gas": 300_000,
     "state_profile": SWAP_PROFILE},
    {"name": "swap_5KiB", "desc": "своп, агрегаторный путь 5KiB",
     "calldata_bytes": 5 * 1024, "zero_frac": 0.5, "exec_gas": 300_000,
     "state_profile": SWAP_PROFILE},
    {"name": "swap_20KiB", "desc": "своп, толстый агрегаторный путь 20KiB (KyberSwap/1inch)",
     "calldata_bytes": 20 * 1024, "zero_frac": 0.5, "exec_gas": 300_000,
     "state_profile": SWAP_PROFILE},
    # exec_gas = 300k ликвидации + 22 100 сегодняшней цены нового слота (cold+SET):
    # профильные стейт-опы входят в exec_gas по today-ценам и репрайсятся по режиму
    {"name": "liq_new_token_slot", "desc": "ликвидация + эксекьютор впервые получает токен X",
     "calldata_bytes": 800, "zero_frac": 0.5, "exec_gas": 322_100,
     "state_profile": dict(LIQ_PROFILE, new_slots=1)},
    {"name": "liq_with_al", "desc": "ликвидация с access-list (2 адреса + 4 ключа)",
     "calldata_bytes": 800, "zero_frac": 0.5, "exec_gas": 300_000,
     "state_profile": LIQ_PROFILE, "al": {"addresses": 2, "keys": 4}},
]


# ----------------------------------------------------------------- report

def report() -> None:
    print("=== T4: дельты газ-стоимости tx-паттернов под Glamsterdam ===")
    print("[P]-источники: EIP-7976/7981/8037/2780/8038, raw ethereum/EIPs, фетч 2026-07-12;")
    print("[S]: zero_frac=0.5, стейт-профили exec — модельные (см. PATTERNS).")
    print("Ветви: SFI = 7976+7981+8037; CFI = SFI + 2780 + 8038 (тестируются на devnet-6/7).\n")
    hdr = (f"{'паттерн':<20}{'today':>10}{'SFI':>11}{'dSFI%':>8}{'CFI':>11}{'dCFI%':>8}"
           f"{'floorSFI':>9}{'minLimSFI':>11}")
    print(hdr)
    print("-" * len(hdr))
    for p in PATTERNS:
        d = deltas(p, PARAMS)
        print(f"{p['name']:<20}{d['today']:>10,}{d['sfi']:>11,}{d['d_sfi_pct']:>+8.1f}"
              f"{d['cfi']:>11,}{d['d_cfi_pct']:>+8.1f}"
              f"{'YES' if d['floor_binds_sfi'] else 'no':>9}{d['min_gas_limit_sfi']:>11,}")

    print("\n--- сверка с ожиданиями Gate 0 (pre-registered, H5) ---")
    d_direct = deltas(PATTERNS[0], PARAMS)
    d20 = deltas(PATTERNS[3], PARAMS)
    s37 = surcharge_8037(1, 0, PARAMS)
    print(f"  прямой путь ~0%:  dSFI(liq_direct) = {d_direct['d_sfi_pct']:+.2f}%")
    print(f"  20KiB ~ +150%:    dSFI(swap_20KiB) = {d20['d_sfi_pct']:+.1f}% "
          f"(today {d20['today']:,} -> {d20['sfi']:,})")
    print(f"  новый слот:       +{s37['delta_vs_today']:,} gas "
          f"(20k -> {s37['slot_cost']:,}, x{s37['slot_multiple']:.2f})")

    al = al_rule_7981(PARAMS, "sfi")
    al_cfi = al_rule_7981(PARAMS, "cfi")
    d_al = deltas(PATTERNS[5], PARAMS)
    d_noal = deltas(PATTERNS[0], PARAMS)
    keep_al_cost = d_al["sfi"] - d_noal["sfi"]
    print("\n--- свод операционных правил для monad-liquidator (mainnet-USDe трек; "
          "пересчитать ДО активации; Base не трогать до OP-stack-форка) ---")
    print(f" (i)   РОУТИНГ: толстые агрегаторные calldata-пути на L1 исключить из выбора"
          f" маршрута:\n       20KiB {d20['d_sfi_pct']:+.0f}% (floor биндится),"
          f" 5KiB {deltas(PATTERNS[2], PARAMS)['d_sfi_pct']:+.1f}%,"
          f" компактный 800B {deltas(PATTERNS[1], PARAMS)['d_sfi_pct']:+.1f}%"
          f" — предпочесть компактный роутинг.")
    print(f" (ii)  ПРЕ-СИД: балансы/слоты эксекьютора по целевым токенам засеять заранее:"
          f"\n       первый приём токена = +{s37['delta_vs_today']:,} gas/слот в SFI"
          f" (дельта паттерна {deltas(PATTERNS[4], PARAMS)['d_sfi_pct']:+.1f}%)."
          f" Операционная дисциплина, не капитал.")
    print(f" (iii) ДРОП ACCESS-LIST: листинг ключа дороже холодного доступа на"
          f" {al['key_listing_vs_cold']:,}\n       (полный штраф {al['key_net_penalty']:,}"
          f"/ключ, {al['addr_net_penalty']:,}/адрес; в CFI {al_cfi['key_net_penalty']:,}/ключ)."
          f" Для AL 2+4: +{keep_al_cost:,} gas ({100 * keep_al_cost / d_noal['sfi']:+.1f}%).")
    print(f" (iv)  HEADROOM: gas limit tx обязан покрывать intrinsic + 64*байт calldata"
          f" даже если\n       ожидаемый gasUsed ниже: для 20KiB minLimit ="
          f" {d20['min_gas_limit_sfi']:,} (сегодня хватало ~{d20['today']:,}).")
    print("\nCFI-ветвь (2780+8038, пока Considered): прямой путь ДОРОЖАЕТ"
          f" {d_direct['d_cfi_pct']:+.1f}% за счёт репрайсинга стейт-доступа —"
          " если S7 покажет вход 8038 в SFI, пересчёт становится основным.")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"report": report}.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
