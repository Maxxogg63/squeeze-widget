"""
apply_v142.py — применяет патч v1.4.2 к squeeze_widget.py
Запуск: python apply_v142.py
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
    "Версия 1.4.1 — дедупликация БИТ, колонка диапазона в окне БИТ",
    "Версия 1.4.2 — дедупликация по exit_ts, только последний выход",
    "header version"
)

# ===== 2. Changelog =====
apply_change(
    """    1.4.1 — дедупликация БИТ в истории (по symbol + fired_at)
            в окне БИТ добавлена колонка ДИАПАЗОН (low — high)

Запуск:""",
    """    1.4.1 — дедупликация БИТ в истории (по symbol + fired_at)
            в окне БИТ добавлена колонка ДИАПАЗОН (low — high)
    1.4.2 — дедупликация по (symbol, exit_ts)
            find_active_range возвращает ТОЛЬКО последний выход
            при прогреве записывается 1 запись на монету

Запуск:""",
    "changelog"
)

# ===== 3. Версия = 1.4.2 =====
apply_change(
    'WIDGET_VERSION = "1.4.1"',
    'WIDGET_VERSION = "1.4.2"',
    "WIDGET_VERSION"
)

# ===== 4. worker_log версия =====
apply_change(
    'worker_log("=== start v1.4.1 ===")',
    'worker_log("=== start v1.4.2 ===")',
    "worker_log version"
)

# ===== 5. _bit_history_header — добавляем exit_ts =====
apply_change(
    '''def _bit_history_header():
    return [
        "symbol", "detected_at", "fired_at", "duration_in_range_sec",
        "range_high", "range_low", "width_pct",
        "growth_24h", "pullback", "volume_24h",
        "exit_price",
        "max_after_1h", "max_after_6h", "max_after_24h",
        "pct_after_1h", "pct_after_6h", "pct_after_24h",
        "status",
    ]''',
    '''def _bit_history_header():
    return [
        "symbol", "detected_at", "fired_at", "exit_ts",
        "duration_in_range_sec",
        "range_high", "range_low", "width_pct",
        "growth_24h", "pullback", "volume_24h",
        "exit_price",
        "max_after_1h", "max_after_6h", "max_after_24h",
        "pct_after_1h", "pct_after_6h", "pct_after_24h",
        "status",
    ]''',
    "header with exit_ts"
)

# ===== 6. add_bit_history — новый параметр exit_ts + дедуп =====
apply_change(
    '''def add_bit_history(sym, state, exit_price):
    """Добавляет новую запись в историю БИТ."""
    now = time.time()
    fired_at_str = _ts(now)
    # v1.4.1: дедупликация — если (symbol, fired_at) уже есть, не добавляем
    with bit_history_lock:
        for existing in bit_history:
            if existing.get("symbol") == sym and \\
               existing.get("fired_at") == fired_at_str:
                worker_log(f"[v1.4.1] dedup skipped {sym} {fired_at_str}")
                return None
    width = (state["range_high"] / state["range_low"] - 1) * 100 \\
        if state["range_low"] > 0 else 0
    row = {
        "symbol": sym,
        "detected_at": _ts(state.get("started", now)),
        "fired_at": fired_at_str,''',
    '''def add_bit_history(sym, state, exit_price, exit_ts=None):
    """Добавляет новую запись в историю БИТ."""
    now = time.time()
    if exit_ts is None:
        exit_ts = now
    fired_at_str = _ts(now)
    # v1.4.2: дедупликация по (symbol, exit_ts с точностью до минуты)
    exit_key = int(exit_ts // 60)
    with bit_history_lock:
        for existing in bit_history:
            ex_key = int(float(existing.get("exit_ts", 0)) // 60)
            if existing.get("symbol") == sym and ex_key == exit_key:
                worker_log(f"[v1.4.2] dedup {sym} exit_key={exit_key}")
                return None
    width = (state["range_high"] / state["range_low"] - 1) * 100 \\
        if state["range_low"] > 0 else 0
    row = {
        "symbol": sym,
        "detected_at": _ts(state.get("started", now)),
        "fired_at": fired_at_str,
        "exit_ts": exit_ts,''',
    "add_bit_history with exit_ts"
)

# ===== 7. find_active_range — только последний выход =====
apply_change(
    '''        if not has_breakout_up and not has_breakout_down:
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
    
    return last_active_start, exits''',
    '''        if not has_breakout_up and not has_breakout_down:
            # этот диапазон активен (нет выхода)
            last_active_start = orig_i
        else:
            # был выход — v1.4.2: запоминаем ТОЛЬКО последний
            if has_breakout_up:
                exit_price = None
                exit_ts_val = None
                for c in subset[cfg["range_forming_min_candles"]:]:
                    if c["high"] > rh_initial * (1 + breakout_threshold):
                        exit_price = c["high"]
                        exit_ts_val = c["t"] / 1000.0
                        break
                exits = [{
                    "start_idx": orig_i,
                    "rh": rh_initial,
                    "rl": rl_initial,
                    "exit_price": exit_price,
                    "exit_ts": exit_ts_val,
                }]
    
    return last_active_start, exits''',
    "find_active_range only last exit"
)

# ===== 8. build_state — передаём exit_ts =====
apply_change(
    '''    # записываем прошлые БИТ в историю
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
            worker_log(f"[v1.4.0] bit_history error {sym}: {e}")''',
    '''    # записываем прошлый БИТ в историю (только последний — v1.4.2)
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
            add_bit_history(sym, fake_state, ex["exit_price"],
                            exit_ts=ex.get("exit_ts"))
        except Exception as e:
            worker_log(f"[v1.4.2] bit_history error {sym}: {e}")''',
    "build_state exit_ts"
)

# ===== 9. update_state — передаём exit_ts =====
apply_change(
    '''                if sv == "broken_up":
                    # БИТ СРАБОТАЛ
                    add_bit_history(sym, state, close)
                    return {"type": "bit_signal", "symbol": sym,
                            "state": state}''',
    '''                if sv == "broken_up":
                    # БИТ СРАБОТАЛ
                    add_bit_history(sym, state, close, exit_ts=now)
                    return {"type": "bit_signal", "symbol": sym,
                            "state": state}''',
    "update_state exit_ts"
)

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