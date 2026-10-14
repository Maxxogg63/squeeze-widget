"""
apply_replay_v2.py — фикс _save_all в squeeze_replay.py
Запуск: python apply_replay_v2.py
"""
import io
import os
import sys

PATH = "squeeze_replay.py"

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


# ===== Заменяем весь метод _save_all =====
old_method = '''    def _save_all(self):
        saved = 0
        for sym, fields in self.entries.items():
            my_high = fields["high"].get().strip()
            my_low = fields["low"].get().strip()
            my_start = fields["start"].get().strip()
            notes = fields["notes"].get().strip()
            not_bit = fields["not_bit"].get()

            # пропускаем пустые
            if not (my_high and my_low):
                continue

            state = fields["state"]
            if save_row(state, my_start, my_high, my_low,
                        notes, not_bit):
                saved += 1
                # очищаем поля
                fields["high"].delete(0, "end")
                fields["low"].delete(0, "end")
                fields["start"].delete(0, "end")
                fields["notes"].delete(0, "end")
                fields["not_bit"].deselect()

        self.footer_label.configure(
            text=f"сохранено: {saved} записей в calibration.csv")
        worker_log(f"save_all: {saved} rows")
        # v1: НЕ перерисовываем — сохраняем введённые данные'''

new_method = '''    def _save_all(self):
        """v2: сохраняем ВСЕ заполненные строки. Ошибка одной не рвёт цикл."""
        saved = 0
        errors = 0
        for sym, fields in list(self.entries.items()):
            try:
                my_high = fields["high"].get().strip()
                my_low = fields["low"].get().strip()
                my_start = fields["start"].get().strip()
                notes = fields["notes"].get().strip()
                not_bit = bool(fields["not_bit"].get())

                # пропускаем пустые
                if not (my_high and my_low):
                    continue

                state = fields["state"]
                ok = save_row(state, my_start, my_high, my_low,
                              notes, not_bit)
                if ok:
                    saved += 1
                    # v2: очищаем ТОЛЬКО после успешной записи
                    fields["high"].delete(0, "end")
                    fields["low"].delete(0, "end")
                    fields["start"].delete(0, "end")
                    fields["notes"].delete(0, "end")
                    try:
                        fields["not_bit"].set(False)
                    except Exception:
                        pass
                else:
                    errors += 1
                    worker_log(f"save_row returned False for {sym}")
            except Exception as e:
                errors += 1
                worker_log(f"save_all loop error {sym}: {e}")
                continue

        self.footer_label.configure(
            text=f"сохранено: {saved} записей · ошибок: {errors}")
        worker_log(f"save_all: saved={saved}, errors={errors}")'''

apply_change(old_method, new_method, "_save_all v2")

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