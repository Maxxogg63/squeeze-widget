"""
apply_v141.py — применяет патч v1.4.1 к squeeze_widget.py
Запуск: python apply_v141.py
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
    "Версия 1.4.0 — поиск активного диапазона, порог выхода 5%",
    "Версия 1.4.1 — дедупликация БИТ, колонка диапазона в окне БИТ",
    "header version"
)

# ===== 2. Changelog =====
apply_change(
    """    1.4.0 — ПОЛНАЯ ПЕРЕРАБОТКА: поиск последнего АКТИВНОГО диапазона
            (пропускает отработавшие БИТ), порог выхода 5%
            записи БИТ при прогреве, много выходов из одного диапазона

Запуск:""",
    """    1.4.0 — ПОЛНАЯ ПЕРЕРАБОТКА: поиск последнего АКТИВНОГО диапазона
            (пропускает отработавшие БИТ), порог выхода 5%
            записи БИТ при прогреве, много выходов из одного диапазона
    1.4.1 — дедупликация БИТ в истории (по symbol + fired_at)
            в окне БИТ добавлена колонка ДИАПАЗОН (low — high)

Запуск:""",
    "changelog"
)

# ===== 3. Версия = 1.4.1 =====
apply_change(
    'WIDGET_VERSION = "1.4.0"',
    'WIDGET_VERSION = "1.4.1"',
    "WIDGET_VERSION"
)

# ===== 4. worker_log версия =====
apply_change(
    'worker_log("=== start v1.4.0 ===")',
    'worker_log("=== start v1.4.1 ===")',
    "worker_log version"
)

# ===== 5. Дедупликация в add_bit_history =====
apply_change(
    """def add_bit_history(sym, state, exit_price):
    \"\"\"Добавляет новую запись в историю БИТ.\"\"\"
    now = time.time()
    width = (state["range_high"] / state["range_low"] - 1) * 100 \\
        if state["range_low"] > 0 else 0
    row = {
        "symbol": sym,
        "detected_at": _ts(state.get("started", now)),
        "fired_at": _ts(now),""",
    """def add_bit_history(sym, state, exit_price):
    \"\"\"Добавляет новую запись в историю БИТ.\"\"\"
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
        "fired_at": fired_at_str,""",
    "dedup in add_bit_history"
)

# ===== 6. Заменить COLUMNS в BitHistoryWindow =====
apply_change(
    """    COLUMNS = [
        ("ТИКЕР",       90),
        ("ОБНАРУЖЕН",   140),
        ("СРАБОТАЛ",    140),
        ("В ДИАП",      80),
        ("ШИРИНА",      70),
        ("РОСТ",        70),
        ("ОТКАТ",       70),
        ("ЦЕНА ВЫХ",    100),
        ("+1ч",         80),
        ("+6ч",         80),
        ("+24ч",        80),
    ]""",
    """    COLUMNS = [
        ("ТИКЕР",       80),
        ("ОБНАРУЖЕН",   130),
        ("СРАБОТАЛ",    130),
        ("В ДИАП",      70),
        ("ДИАПАЗОН",    180),   # v1.4.1: low — high
        ("ШИРИНА",      65),
        ("РОСТ",        60),
        ("ОТКАТ",       60),
        ("ЦЕНА ВЫХ",    90),
        ("+1ч",         60),
        ("+6ч",         60),
        ("+24ч",        60),
    ]""",
    "BitHistoryWindow COLUMNS"
)

# ===== 7. Заменить values в _build_row =====
apply_change(
    """        detected = row.get("detected_at", "")
        fired = row.get("fired_at", "")
        duration_sec = row.get("duration_in_range_sec", 0)
        width = row.get("width_pct", 0)
        growth = row.get("growth_24h", 0)
        pullback = row.get("pullback", 0)
        exit_price = row.get("exit_price", 0)
        pct_1h = row.get("pct_after_1h", 0)
        pct_6h = row.get("pct_after_6h", 0)
        pct_24h = row.get("pct_after_24h", 0)

        values = [
            (ticker, GOLD, "bold"),
            (detected[:16] if detected else "—", MINT, "normal"),
            (fired[:16] if fired else "—", MINT, "normal"),
            (fmt_duration(duration_sec), MINT, "normal"),
            (f"{width:.2f}%" if width else "—", PEACH, "bold"),
            (f"{growth:.1f}%" if growth else "—", SAND, "normal"),
            (f"{pullback:.1f}%" if pullback else "—", SAND, "normal"),
            (f"{exit_price:.6f}" if exit_price else "—", MINT, "normal"),
            (self._pct_str(pct_1h), self._pct_color(pct_1h), "bold"),
            (self._pct_str(pct_6h), self._pct_color(pct_6h), "bold"),
            (self._pct_str(pct_24h), self._pct_color(pct_24h), "bold"),
        ]""",
    """        detected = row.get("detected_at", "")
        fired = row.get("fired_at", "")
        duration_sec = row.get("duration_in_range_sec", 0)
        range_high = row.get("range_high", 0)
        range_low = row.get("range_low", 0)
        width = row.get("width_pct", 0)
        growth = row.get("growth_24h", 0)
        pullback = row.get("pullback", 0)
        exit_price = row.get("exit_price", 0)
        pct_1h = row.get("pct_after_1h", 0)
        pct_6h = row.get("pct_after_6h", 0)
        pct_24h = row.get("pct_after_24h", 0)

        # v1.4.1: диапазон в одной колонке
        if range_low and range_high:
            rng_str = f"{range_low:.4f} — {range_high:.4f}"
        else:
            rng_str = "—"

        values = [
            (ticker, GOLD, "bold"),
            (detected[:16] if detected else "—", MINT, "normal"),
            (fired[:16] if fired else "—", MINT, "normal"),
            (fmt_duration(duration_sec), MINT, "normal"),
            (rng_str, MINT, "normal"),    # v1.4.1
            (f"{width:.2f}%" if width else "—", PEACH, "bold"),
            (f"{growth:.1f}%" if growth else "—", SAND, "normal"),
            (f"{pullback:.1f}%" if pullback else "—", SAND, "normal"),
            (f"{exit_price:.6f}" if exit_price else "—", MINT, "normal"),
            (self._pct_str(pct_1h), self._pct_color(pct_1h), "bold"),
            (self._pct_str(pct_6h), self._pct_color(pct_6h), "bold"),
            (self._pct_str(pct_24h), self._pct_color(pct_24h), "bold"),
        ]""",
    "BitHistoryWindow row values"
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