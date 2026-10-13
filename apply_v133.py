"""
apply_v133.py — применяет патч v1.3.3 к squeeze_widget.py
Запуск: python apply_v133.py
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


# ===== 1. Заголовок =====
apply_change(
    "Версия 1.3.2 — алерт при появлении, фиксированные границы",
    "Версия 1.3.3 — метка «возможно тренд», версия в UI",
    "header version"
)

# ===== 2. Changelog =====
apply_change(
    """    1.3.2 — фикс: алерт при первом появлении активной монеты
            фикс: границы диапазона фиксированы (не сжимаются)

Запуск:""",
    """    1.3.2 — фикс: алерт при первом появлении активной монеты
            фикс: границы диапазона фиксированы (не сжимаются)
    1.3.3 — is_trend больше не отсеивает, а помечает монету
            метка «возможно тренд» в UI
            номер версии внизу окна

Запуск:""",
    "changelog"
)

# ===== 3. Убираем is_trend reject =====
apply_change(
    """    # 1. ТРЕНД vs ДИАПАЗОН
    if is_trend(candles_5m):
        status["fail_trend"] += 1
        if sym in DEBUG_SYMBOLS:
            worker_log(f"DEBUG {sym}: REJECT at is_trend")
        return None

    g1h = calc_growth_1h(candles_1h)""",
    """    # v1.3.3: НЕ отсеиваем тренд, только помечаем
    is_trending_flag = is_trend(candles_5m)

    g1h = calc_growth_1h(candles_1h)""",
    "remove is_trend reject"
)

# ===== 4. Добавляем is_trending в state =====
apply_change(
    """        "volume_24h": volume,
        "last_update_ts": now,
    }

    if st_status == "active":
        log_squeeze(sym, state)""",
    """        "volume_24h": volume,
        "last_update_ts": now,
        "is_trending": is_trending_flag,
    }

    if st_status == "active":
        log_squeeze(sym, state)""",
    "add is_trending to state"
)

# ===== 5. WIDGET_VERSION константа =====
apply_change(
    'DEBUG_SYMBOLS = ("GTCUSDT", "OGNUSDT")',
    'DEBUG_SYMBOLS = ("GTCUSDT", "OGNUSDT")\n\n# версия виджета — отображается внизу окна\nWIDGET_VERSION = "1.3.3"',
    "WIDGET_VERSION constant"
)

# ===== 6. Добавляем label версии в UI =====
# после того как добавлен table_frame, добавляем строку версии
apply_change(
    """        self.row_widgets = []

    def _build_menu(self):""",
    """        self.row_widgets = []
        # v1.3.3: номер версии внизу окна
        self.version_lbl = ctk.CTkLabel(
            self.main, text=f"v{WIDGET_VERSION}",
            font=("Segoe UI", 8),
            text_color=GREY, anchor="e"
        )
        self.version_lbl.pack(fill="x", padx=14, pady=(0, 4))

    def _build_menu(self):""",
    "version label in UI"
)

# ===== 7. Метка «возможно тренд» в строке =====
# добавляем после объёма
apply_change(
    """        l = ctk.CTkLabel(row, text=vt,
                         font=("Segoe UI", 11), text_color=PEACH,
                         width=self.COLUMNS[5][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))

        self.row_widgets.append(row)""",
    """        l = ctk.CTkLabel(row, text=vt,
                         font=("Segoe UI", 11), text_color=PEACH,
                         width=self.COLUMNS[5][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))

        # v1.3.3: метка «возможно тренд» для трендовых монет
        if st.get("is_trending", False):
            trend_lbl = ctk.CTkLabel(
                row, text="возможно тренд",
                font=("Segoe UI", 9, "italic"),
                text_color=GREY, anchor="w"
            )
            trend_lbl.pack(side="left", padx=(8, 0))

        self.row_widgets.append(row)""",
    "trend label in row"
)

# ===== 8. Версия в worker_log =====
apply_change(
    'worker_log("=== start v1.3.2 ===")',
    'worker_log("=== start v1.3.3 ===")',
    "worker_log version"
)

# ===== сохраняем =====
if text != original:
    with io.open(PATH + ".bak", "w", encoding="utf-8") as f:
        f.write(original)
    with io.open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\nDONE: {changes} changes applied, {fails} failed")
    print(f"BACKUP: {PATH}.bak")
else:
    print("\nNOTHING CHANGED — возможно, патч уже применён")