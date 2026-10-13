"""
apply_v132.py — применяет патч v1.3.2 к squeeze_widget.py
Запуск: python apply_v132.py
"""
import io
import os
import sys

PATH = "squeeze_widget.py"

if not os.path.exists(PATH):
    print(f"ERROR: {PATH} not found")
    sys.exit(1)

# читаем файл
with io.open(PATH, "r", encoding="utf-8") as f:
    text = f.read()

original = text
changes = 0

# ===== ПРАВКА 1: заголовок =====
old = "Версия 1.3.1 — squeeze_filters.txt, фикс pullback, is_trend, окно БИТ"
new = "Версия 1.3.2 — алерт при появлении, фиксированные границы"
if old in text:
    text = text.replace(old, new, 1)
    changes += 1
    print("[OK] header version")
else:
    print("[SKIP] header version (already changed or different)")

# ===== ПРАВКА 2: changelog =====
old = """            отдельное окно "Отработанные БИТ"

Запуск:  python squeeze_widget.py"""
new = """            отдельное окно "Отработанные БИТ"
    1.3.2 — фикс: алерт при первом появлении активной монеты
            фикс: границы диапазона фиксированы (не сжимаются)

Запуск:  python squeeze_widget.py"""
if old in text:
    text = text.replace(old, new, 1)
    changes += 1
    print("[OK] changelog")
else:
    print("[SKIP] changelog")

# ===== ПРАВКА 3: фиксированные границы =====
old = """    if high < state["range_high"]:
        state["range_high"] = high
    if low > state["range_low"]:
        state["range_low"] = low

    state["candles_in_range"] += 1
    state["last_update_ts"] = now"""
new = """    # v1.3.2: границы НЕ сжимаем — фиксируем при создании
    state["candles_in_range"] += 1
    state["last_update_ts"] = now"""
if old in text:
    text = text.replace(old, new, 1)
    changes += 1
    print("[OK] fixed range bounds")
else:
    print("[FAIL] fixed range bounds — не нашёл блок")

# ===== ПРАВКА 4: алерт при появлении =====
old = """        new_state = build_state(sym, buf, candles_1h)
        if new_state:
            squeeze_state[sym] = new_state
            alerts_q.put({"type": "forming", "symbol": sym,
                          "state": new_state})
        return"""
new = """        new_state = build_state(sym, buf, candles_1h)
        if new_state:
            squeeze_state[sym] = new_state
            # v1.3.2: правильный тип события при первом появлении
            ev_type = "new_active" if new_state["status"] == "active" \\
                else "forming"
            alerts_q.put({"type": ev_type, "symbol": sym,
                          "state": new_state})
        return"""
if old in text:
    text = text.replace(old, new, 1)
    changes += 1
    print("[OK] alert on appearance")
else:
    print("[FAIL] alert on appearance — не нашёл блок")

# ===== ПРАВКА 5: версия в worker_log =====
old = 'worker_log("=== start v1.3.1 ===")'
new = 'worker_log("=== start v1.3.2 ===")'
if old in text:
    text = text.replace(old, new, 1)
    changes += 1
    print("[OK] version log")
else:
    print("[SKIP] version log")

# сохраняем
if text != original:
    # бэкап
    with io.open(PATH + ".bak", "w", encoding="utf-8") as f:
        f.write(original)
    with io.open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\nDONE: {changes} changes applied")
    print(f"BACKUP: {PATH}.bak")
else:
    print("\nNOTHING CHANGED")