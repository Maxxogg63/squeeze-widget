"""
apply_v134.py — применяет патч v1.3.4 к squeeze_widget.py
Запуск: python apply_v134.py
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
    "Версия 1.3.3 — метка «возможно тренд», версия в UI",
    "Версия 1.3.4 — крупная версия, фикс мерцания, копирование тикера",
    "header version"
)

# ===== 2. Changelog =====
apply_change(
    """    1.3.3 — is_trend больше не отсеивает, а помечает монету
            метка «возможно тренд» в UI
            номер версии внизу окна

Запуск:""",
    """    1.3.3 — is_trend больше не отсеивает, а помечает монету
            метка «возможно тренд» в UI
            номер версии внизу окна
    1.3.4 — версия крупнее и золотая
            фикс мерцания (хэш-сравнение, перерисовка раз в минуту)
            ЛКМ по тикеру копирует полный символ (GTCUSDT) в буфер

Запуск:""",
    "changelog"
)

# ===== 3. Версия = 1.3.4 =====
apply_change(
    'WIDGET_VERSION = "1.3.3"',
    'WIDGET_VERSION = "1.3.4"',
    "WIDGET_VERSION"
)

# ===== 4. worker_log версия =====
apply_change(
    'worker_log("=== start v1.3.3 ===")',
    'worker_log("=== start v1.3.4 ===")',
    "worker_log version"
)

# ===== 5. Версия в UI — крупнее и золотая =====
apply_change(
    """        self.version_lbl = ctk.CTkLabel(
            self.main, text=f"v{WIDGET_VERSION}",
            font=("Segoe UI", 8),
            text_color=GREY, anchor="e"
        )""",
    """        self.version_lbl = ctk.CTkLabel(
            self.main, text=f"v{WIDGET_VERSION}",
            font=("Segoe UI", 11, "bold"),
            text_color=GOLD, anchor="e"
        )""",
    "version label bigger + gold"
)

# ===== 6. Добавить _render_hash в __init__ =====
apply_change(
    """        self.active_popups = []
        self.bit_window = None   # ссылка на окно БИТ
        self.poll()""",
    """        self.active_popups = []
        self.bit_window = None   # ссылка на окно БИТ
        self._last_render_hash = None   # v1.3.4: для фикса мерцания
        self.poll()""",
    "init render_hash"
)

# ===== 7. Копирование тикера — метод _copy_ticker =====
apply_change(
    """    def _close_popup(self, popup):
        try:
            popup.destroy()
        except Exception:
            pass
        if popup in self.active_popups:
            self.active_popups.remove(popup)

    def poll(self):""",
    """    def _close_popup(self, popup):
        try:
            popup.destroy()
        except Exception:
            pass
        if popup in self.active_popups:
            self.active_popups.remove(popup)

    def _copy_ticker(self, symbol):
        \"\"\"v1.3.4: копирует полный символ в буфер обмена.\"\"\"
        try:
            self.clipboard_clear()
            self.clipboard_append(symbol)
            worker_log(f"[copy] {symbol}")
        except Exception as e:
            worker_log(f"[copy] error: {e}")

    def _calc_render_hash(self):
        \"\"\"v1.3.4: отпечаток текущего состояния для фикса мерцания.\"\"\"
        now = time.time()
        parts = []
        for sym, st in sorted(squeeze_state.items()):
            sv = st.get("status", "forming")
            if sv == "cancelled":
                continue
            # время в минутах — чтобы раз в минуту перерисовывать
            mins_in_range = int((now - st.get("started", now)) / 60)
            parts.append(f"{sym}:{sv}:{mins_in_range}:{st.get('touch_count', 0)}")
        return hash(tuple(parts))

    def poll(self):""",
    "copy_ticker method + render_hash"
)

# ===== 8. Тикер кликабельный =====
apply_change(
    """        l = ctk.CTkLabel(row, text=ticker, font=fnt, text_color=fg,
                         width=self.COLUMNS[0][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))

        dur = now - st.get("started", now)""",
    """        l = ctk.CTkLabel(row, text=ticker, font=fnt, text_color=fg,
                         width=self.COLUMNS[0][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))
        # v1.3.4: ЛКМ по тикеру — копировать полный символ
        l.bind("<Button-1>", lambda e, s=sym: self._copy_ticker(s))

        dur = now - st.get("started", now)""",
    "ticker clickable"
)

# ===== 9. poll() — фикс мерцания через хэш =====
apply_change(
    """        if changed or now - self.last_draw > 0.5:
            self._render()
            self.last_draw = now
        self.after(30, self.poll)""",
    """        # v1.3.4: перерисовка только если состояние изменилось
        new_hash = self._calc_render_hash()
        need_render = (
            changed
            or new_hash != self._last_render_hash
        )
        if need_render:
            self._render()
            self._last_render_hash = new_hash
            self.last_draw = now
        self.after(1000, self.poll)   # раз в секунду проверяем""",
    "poll render_hash fix"
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