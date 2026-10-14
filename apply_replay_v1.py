"""
apply_replay_v1.py — фикс мерцания в squeeze_replay.py
Запуск: python apply_replay_v1.py
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


# ===== 1. Флаг _rendered в __init__ =====
apply_change(
    '''        self.row_widgets = []
        self.entries = {}   # symbol -> {start, high, low, notes}
        self.last_draw = 0''',
    '''        self.row_widgets = []
        self.entries = {}   # symbol -> {start, high, low, notes}
        self.last_draw = 0
        self._rendered = False   # v1: рендер один раз''',
    "flag _rendered init"
)

# ===== 2. _rescan сбрасывает флаг =====
apply_change(
    '''    def _rescan(self):
        def _run():
            status["text"] = "сканирование..."
            scan_all()
            status["text"] = f"найдено: {len(scan_result)}"
        threading.Thread(target=_run, daemon=True).start()''',
    '''    def _rescan(self):
        def _run():
            status["text"] = "сканирование..."
            scan_all()
            status["text"] = f"найдено: {len(scan_result)}"
            self._rendered = False   # v1: перерисуем
        threading.Thread(target=_run, daemon=True).start()''',
    "_rescan reset flag"
)

# ===== 3. poll — рендер один раз =====
apply_change(
    '''    def poll(self):
        now = time.time()
        if now - self.last_draw > 1.0:
            self.status_label.configure(text=status.get("text", ""))
            self._render()
            self.last_draw = now
        self.after(500, self.poll)''',
    '''    def poll(self):
        # v1: только статус-строка обновляется
        self.status_label.configure(text=status.get("text", ""))

        # рендер — один раз, после прогрева
        if not self._rendered and status["warmup_finished"]:
            self._render()
            self._rendered = True

        # при 0 монет — покажем "Нет активных"
        if self._rendered and not scan_result and not self.entries:
            # уже отрендерили "Нет активных"
            pass

        self.after(500, self.poll)''',
    "poll one-shot render"
)

# ===== 4. После сохранения — НЕ рендерим =====
apply_change(
    '''        self.footer_label.configure(
            text=f"сохранено: {saved} записей в calibration.csv")
        worker_log(f"save_all: {saved} rows")''',
    '''        self.footer_label.configure(
            text=f"сохранено: {saved} записей в calibration.csv")
        worker_log(f"save_all: {saved} rows")
        # v1: НЕ перерисовываем — сохраняем введённые данные''',
    "save_all no re-render"
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