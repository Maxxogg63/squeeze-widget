"""
apply_v140.py — применяет патч v1.4.0 к squeeze_widget.py
Запуск: python apply_v140.py
"""
import io
import os
import sys

PATH = "squeeze_widget.py"

if not os.path.exists(PATH):
    print(f"ERROR: {PATH} not found")
    sys.exit(1)

with io.open(PATH, "r", encoding="utf-8") as f:
    text = f.read()

original = text
changes = 0
fails = 0


def apply_change(old, new, label, count=1):
    global text, changes, fails
    if old in text:
        text = text.replace(old, new, count)
        changes += 1
        print(f"[OK] {label}")
    else:
        fails += 1
        print(f"[FAIL] {label}")


# ===== 1. Заголовок версии =====
apply_change(
    "Версия 1.3.5 — Alt+Tab и панель задач",
    "Версия 1.4.0 — поиск активного диапазона, порог выхода 5%",
    "header version"
)

# ===== 2. Changelog =====
apply_change(
    """    1.3.5 — окно появляется в Alt+Tab и панели задач

Запуск:""",
    """    1.3.5 — окно появляется в Alt+Tab и панели задач
    1.4.0 — ПОЛНАЯ ПЕРЕРАБОТКА: поиск последнего АКТИВНОГО диапазона
            (пропускает отработавшие БИТ), порог выхода 5%
            записи БИТ при прогреве, много выходов из одного диапазона

Запуск:""",
    "changelog"
)

# ===== 3. Версия = 1.4.0 =====
apply_change(
    'WIDGET_VERSION = "1.3.5"',
    'WIDGET_VERSION = "1.4.0"',
    "WIDGET_VERSION"
)

# ===== 4. worker_log версия =====
apply_change(
    'worker_log("=== start v1.3.5 ===")',
    'worker_log("=== start v1.4.0 ===")',
    "worker_log version"
)

# ===== 5. Добавляем breakout_threshold_pct в DEFAULTS =====
apply_change(
    """    # Касания и выносы
    "touch_gap_pct": 0.2,
    "breakout_exit_candles": 6,
    "breakout_exit_distance_pct": 1.0,
    "backstage_ttl_hours": 12,""",
    """    # Касания и выносы
    "touch_gap_pct": 0.2,
    "breakout_threshold_pct": 5.0,   # v1.4.0: порог выхода из диапазона (%)
    "breakout_exit_candles": 6,
    "breakout_exit_distance_pct": 1.0,
    "backstage_ttl_hours": 12,""",
    "breakout_threshold in DEFAULTS"
)

# ===== 6. Добавляем в FILTER_KEYS =====
apply_change(
    """    "trend_check_candles",
    "trend_check_max_new_highs",
]""",
    """    "trend_check_candles",
    "trend_check_max_new_highs",
    "breakout_threshold_pct",
]""",
    "breakout_threshold in FILTER_KEYS"
)

# ===== 7. Добавляем в squeeze_filters.txt =====
apply_change(
    """# Сколько new high (или new low) допустимо в этих свечах.
# Если больше — это тренд, не создаём диапазон.
# Больше -> мягче. Меньше -> строже.
# Дефолт: 3
trend_check_max_new_highs = 3
\"\"\"""",
    """# Сколько new high (или new low) допустимо в этих свечах.
# Если больше — это тренд, не создаём диапазон.
# Больше -> мягче. Меньше -> строже.
# Дефолт: 3
trend_check_max_new_highs = 3

# ------------------------------------------------------------
# ВЫХОД ИЗ ДИАПАЗОНА
# ------------------------------------------------------------

# На сколько % цена должна уйти за границу диапазона,
# чтобы это считалось "выходом" (а не шумом).
# 5 = цена должна пройти на 5% выше range_high или ниже range_low.
# Больше -> строже (только сильные выходы).
# Меньше -> мягче (ловит даже слабые выходы).
# Дефолт: 5
breakout_threshold_pct = 5
\"\"\"""",
    "breakout_threshold in filters.txt"
)

# ===== 8. Заменяем find_local_min_start на find_active_range =====
old_func = '''def find_local_min_start(candles_5m, t_high_ts):
    after_high = []
    for i, c in enumerate(candles_5m):
        if c["t"] >= t_high_ts:
            after_high.append((i, c))

    if len(after_high) < cfg["range_forming_min_candles"]:
        return None

    N = cfg["local_min_window"]
    candidates = []
    for k in range(N, len(after_high) - N):
        orig_i = after_high[k][0]
        low_k = after_high[k][1]["low"]
        is_local_min = True
        for j in range(k - N, k + N + 1):
            if j == k:
                continue
            if after_high[j][1]["low"] < low_k:
                is_local_min = False
                break
        if is_local_min:
            candidates.append((k, orig_i, low_k))

    if not candidates:
        return None

    for k, orig_i, low_k in candidates:
        broken = False
        for j in range(orig_i + 1, len(candles_5m)):
            if candles_5m[j]["low"] < low_k:
                broken = True
                break
        if not broken:
            return orig_i

    return None'''

new_func = '''def find_active_range(candles_5m, t_high_ts):
    """
    v1.4.0: Ищет ПОСЛЕДНИЙ АКТИВНЫЙ диапазон.
    Пропускает диапазоны, из которых уже был выход (>5%).
    
    Возвращает (start_idx, end_idx, exits_list) или None.
    exits_list — список выходов вверх (для записи в историю БИТ).
    """
    # отбираем свечи после t_high
    after_high = []
    for i, c in enumerate(candles_5m):
        if c["t"] >= t_high_ts:
            after_high.append((i, c))

    if len(after_high) < cfg["range_forming_min_candles"]:
        return None

    breakout_threshold = cfg.get("breakout_threshold_pct", 5.0) / 100.0

    # ищем ПОСЛЕДНИЙ активный диапазон.
    # идём от конца к началу, ищем последний "спокойный" блок.
    # стратегия: найти последний локальный минимум,
    # после которого нет выхода > 5% вверх.
    
    N = cfg["local_min_window"]
    exits = []   # все выходы вверх, найденные в процессе
    
    # идём по свечам, ищем локальные минимумы
    last_active_start = None
    
    for k in range(N, len(after_high) - N):
        orig_i = after_high[k][0]
        low_k = after_high[k][1]["low"]
        
        # проверяем, локальный ли минимум
        is_local_min = True
        for j in range(k - N, k + N + 1):
            if j == k:
                continue
            if after_high[j][1]["low"] < low_k:
                is_local_min = False
                break
        if not is_local_min:
            continue
        
        # проверяем: был ли выход вверх после этого минимума?
        # считаем range_high = max(high) от orig_i до конца
        subset = list(candles_5m)[orig_i:]
        if len(subset) < cfg["range_forming_min_candles"]:
            continue
        
        # ищем первый "выход" вверх: high > range_high * (1 + threshold)
        # но range_high — это максимум за какой период?
        # логика: смотрим, как цена ведёт себя после orig_i.
        # если после orig_i цена сделала "новый хай" и ушла выше на 5% —
        # это выход, а не диапазон.
        
        # Считаем "зафиксированный" range_high на первых N свечах после orig_i
        first_n = subset[:cfg["range_forming_min_candles"]]
        rh_initial = max(c["high"] for c in first_n)
        rl_initial = min(c["low"] for c in first_n)
        
        # теперь смотрим, что было ПОСЛЕ первых N свечей
        has_breakout_up = False
        has_breakout_down = False
        for c in subset[cfg["range_forming_min_candles"]:]:
            if c["high"] > rh_initial * (1 + breakout_threshold):
                has_breakout_up = True
                break
            if c["low"] < rl_initial * (1 - breakout_threshold):
                has_breakout_down = True
                break
        
        if not has_breakout_up and not has_breakout_down:
            # этот диапазон активен (нет выхода)
            last_active_start = orig_i
        else:
            # был выход — записываем в историю если вверх
            if has_breakout_up:
                # найдём цену выхода
                exit_price = None
                for c in subset[cfg["range_forming_min_candles"]:]:
                    if c["high"] > rh_initial * (1 + breakout_threshold):
                        exit_price = c["high"]
                        break
                exits.append({
                    "start_idx": orig_i,
                    "rh": rh_initial,
                    "rl": rl_initial,
                    "exit_price": exit_price,
                    "exit_ts": c["t"] / 1000.0,
                })
    
    return last_active_start, exits'''

apply_change(old_func, new_func, "find_active_range (replace find_local_min_start)")

# ===== 9. Обновляем build_state =====
old_build = '''    start_idx = find_local_min_start(candles_5m, g1h["t_high"])

    if start_idx is None:
        n = cfg["range_forming_min_candles"]
        if len(candles_5m) < n:
            return None
        start_idx = len(candles_5m) - n

    subset = list(candles_5m)[start_idx:]
    if len(subset) < cfg["range_forming_min_candles"]:
        return None

    rh = max(c["high"] for c in subset)
    rl = min(c["low"] for c in subset)
    if rl <= 0:
        return None
    width = (rh / rl - 1) * 100'''

new_build = '''    # v1.4.0: ищем ПОСЛЕДНИЙ АКТИВНЫЙ диапазон
    active_start, past_exits = find_active_range(candles_5m, g1h["t_high"])

    # записываем прошлые БИТ в историю
    for ex in past_exits:
        try:
            fake_state = {
                "range_high": ex["rh"],
                "range_low": ex["rl"],
                "started": ex["start_idx"] and
                          candles_5m[ex["start_idx"]]["t"] / 1000.0 or time.time(),
                "growth_24h": g1h["growth_24h"],
                "pullback": pullback,
                "volume_24h": volume,
                "candles_in_range": 0,
            }
            add_bit_history(sym, fake_state, ex["exit_price"])
        except Exception as e:
            worker_log(f"[v1.4.0] bit_history error {sym}: {e}")

    if active_start is None:
        # нет активного диапазона
        return None

    start_idx = active_start

    subset = list(candles_5m)[start_idx:]
    if len(subset) < cfg["range_forming_min_candles"]:
        return None

    rh = max(c["high"] for c in subset)
    rl = min(c["low"] for c in subset)
    if rl <= 0:
        return None
    width = (rh / rl - 1) * 100'''

apply_change(old_build, new_build, "build_state uses find_active_range")

# ===== 10. update_state — порог выхода 5% =====
apply_change(
    '''    broke_up = high > state["range_high"]
    broke_down = low < state["range_low"]''',
    '''    # v1.4.0: порог выхода 5%
    breakout_th = cfg.get("breakout_threshold_pct", 5.0) / 100.0
    broke_up = high > state["range_high"] * (1 + breakout_th)
    broke_down = low < state["range_low"] * (1 - breakout_th)''',
    "update_state breakout 5%"
)

# ===== 11. filter_debug — добавить fail_trend обратно (не удаляем) =====

# ===== сохраняем =====
if text != original:
    with io.open(PATH + ".bak", "w", encoding="utf-8") as f:
        f.write(original)
    with io.open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\\nDONE: {changes} changes applied, {fails} failed")
    print(f"BACKUP: {PATH}.bak")
else:
    print("\\nNOTHING CHANGED")