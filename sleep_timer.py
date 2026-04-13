"""
Sleep Timer – таймер сна с автоматическим выключением компьютера.

Позволяет задать время в минутах, после которого начинается проверка
активности пользователя. Если пользователь не взаимодействует с программой,
компьютер выключается. При наличии активности предлагается продлить таймер.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import sys
import subprocess
import threading
import time
import ctypes
import logging
from typing import Optional, Callable

DEFAULT_WINDOW_SIZE = "350x250"
IDLE_CHECK_SECONDS = 30
SHUTDOWN_DELAY_SECONDS = 30
LOG_FILE = "sleep_timer.log"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def is_admin() -> bool:
    """Проверить, запущено ли приложение с правами администратора."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except AttributeError:
        logger.warning("Платформа не Windows или не удалось проверить права.")
        return False


def run_as_admin() -> None:
    """Перезапустить текущий процесс с повышенными привилегиями."""
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            " ".join(sys.argv),
            None,
            1
        )
        sys.exit(0)
    except Exception as e:
        logger.exception("Не удалось перезапустить с правами администратора")
        messagebox.showerror(
            "Ошибка",
            f"Не удалось запросить права администратора:\n{e}"
        )
        sys.exit(1)


def shutdown_system() -> bool:
    """
    Выполнить команду выключения Windows.

    Returns:
        True, если команда успешно запущена, иначе False.
    """
    if sys.platform != "win32":
        logger.error("Попытка выключения не на Windows.")
        return False

    try:
        # Стандартная команда с задержкой
        cmd = ["shutdown", "/s", "/t", str(SHUTDOWN_DELAY_SECONDS)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        if result.returncode == 0:
            logger.info("Команда shutdown /s /t %d выполнена.", SHUTDOWN_DELAY_SECONDS)
            return True
        else:
            # Если стандартная команда не сработала, пробуем форсированное выключение
            logger.warning(
                "shutdown /s /t вернул код %d, пробуем форсированное выключение.",
                result.returncode
            )
            alt_cmd = ["shutdown", "/s", "/f", "/t", "0"]
            subprocess.run(alt_cmd, check=False, timeout=5)
            logger.info("Форсированное выключение инициировано.")
            return True

    except subprocess.TimeoutExpired:
        logger.error("Таймаут при выполнении команды shutdown.")
        return False
    except FileNotFoundError:
        logger.critical("Команда shutdown не найдена в системе.")
        return False
    except Exception as e:
        logger.exception("Непредвиденная ошибка при выключении.")
        return False

class TimerModel:
    """
    Модель таймера, управляющая обратным отсчётом и проверкой активности.
    Все методы, взаимодействующие с UI, выполняются через callback'и.
    """

    def __init__(
        self,
        on_tick: Callable[[int], None],
        on_finish: Callable[[], None],
        on_idle_check_start: Callable[[], None],
        on_idle_detected: Callable[[], None]
    ):
        self.on_tick = on_tick
        self.on_finish = on_finish
        self.on_idle_check_start = on_idle_check_start
        self.on_idle_detected = on_idle_detected

        self._running = False
        self._idle_check_running = False
        self._thread: Optional[threading.Thread] = None
        self._idle_thread: Optional[threading.Thread] = None
        self._last_activity = time.time()
        self._app_running = True

    def start(self, minutes: int) -> None:
        """Запустить основной таймер на указанное количество минут."""
        if self._running:
            logger.warning("Попытка запустить уже работающий таймер.")
            return

        self._running = True
        seconds = minutes * 60
        self._thread = threading.Thread(target=self._countdown, args=(seconds,))
        self._thread.daemon = True
        self._thread.start()
        logger.info("Таймер запущен на %d минут.", minutes)

    def cancel(self) -> None:
        """Остановить таймер и все проверки активности."""
        self._running = False
        self._idle_check_running = False
        logger.info("Таймер отменён пользователем.")

    def stop_app(self) -> None:
        """Корректно завершить все фоновые потоки при закрытии приложения."""
        self._app_running = False
        self.cancel()

    def update_activity(self) -> None:
        """Обновить время последней активности (движение мыши / клавиша)."""
        self._last_activity = time.time()

    def _countdown(self, total_seconds: int) -> None:
        start_time = time.time()
        remaining = total_seconds

        while remaining > 0 and self._running and self._app_running:
            elapsed = time.time() - start_time
            remaining = max(0, total_seconds - int(elapsed))
            self.on_tick(remaining)
            time.sleep(0.1)

        if self._running and self._app_running:
            logger.info("Основной таймер истёк, начинается проверка бездействия.")
            self._running = False
            self.on_finish()
            self._start_idle_check()

    def _start_idle_check(self) -> None:
        if not self._app_running:
            return

        self._idle_check_running = True
        self._last_activity = time.time()
        self.on_idle_check_start()

        self._idle_thread = threading.Thread(target=self._idle_check_loop)
        self._idle_thread.daemon = True
        self._idle_thread.start()

    def _idle_check_loop(self) -> None:
        start_time = time.time()
        active = False

        while self._idle_check_running and self._app_running:
            if time.time() - start_time >= IDLE_CHECK_SECONDS:
                break
            if time.time() - self._last_activity < 0.1:
                active = True
                break
            time.sleep(0.1)

        if not self._idle_check_running or not self._app_running:
            return

        self._idle_check_running = False
        if active:
            logger.info("Обнаружена активность во время проверки бездействия.")
            self.on_idle_detected()
        else:
            logger.info("Активность не обнаружена, инициируется выключение.")
            self._shutdown()

    def _shutdown(self) -> None:
        """Вызвать системное выключение и завершить приложение."""
        success = shutdown_system()
        if not success:
            messagebox.showerror(
                "Ошибка",
                "Не удалось выполнить команду выключения.\n"
                "Попробуйте запустить программу от имени администратора."
            )
            logger.error("Выключение не выполнено.")
        else:
            logger.info("Система выключается.")
        # Даём время на отображение сообщения и завершаем приложение
        time.sleep(1)
        sys.exit(0)

class SleepTimerGUI:
    """Графический интерфейс приложения."""

    def __init__(self, master: tk.Tk):
        self.master = master
        master.title("Таймер сна")
        master.geometry(DEFAULT_WINDOW_SIZE)
        master.resizable(False, False)

        self.style = ttk.Style()
        self.style.theme_use('clam')

        # Переменные интерфейса
        self.minutes_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Готов к работе")
        self.timer_label_text = tk.StringVar(value="00:00")

        self.choice_dialog: Optional[tk.Toplevel] = None
        self.dialog_timeout_id: Optional[str] = None
        self.dialog_seconds_left = IDLE_CHECK_SECONDS
        self.dialog_countdown_var = tk.StringVar()

        # Модель будет установлена позже через set_model
        self.model: Optional[TimerModel] = None

        self._build_ui()
        self._bind_events()

    def set_model(self, model: TimerModel) -> None:
        """Связать GUI с экземпляром модели."""
        self.model = model

    def _build_ui(self) -> None:
        """Построить все виджеты."""
        # Ввод времени
        input_frame = ttk.Frame(self.master, padding="10 10 10 0")
        input_frame.pack(fill='x')
        ttk.Label(input_frame, text="Введите время (минуты):").pack(side='left', padx=5)
        self.minutes_entry = ttk.Entry(input_frame, textvariable=self.minutes_var, width=10)
        self.minutes_entry.pack(side='left', padx=5)
        self.minutes_entry.focus()

        # Кнопки управления
        button_frame = ttk.Frame(self.master, padding="10")
        button_frame.pack(fill='x')
        self.start_btn = ttk.Button(button_frame, text="Запустить таймер", command=self._on_start)
        self.start_btn.pack(fill='x', pady=2)
        self.cancel_btn = ttk.Button(button_frame, text="Отменить", command=self._on_cancel, state='disabled')
        self.cancel_btn.pack(fill='x', pady=2)

        # Отображение таймера
        timer_frame = ttk.Frame(self.master, padding="10")
        timer_frame.pack(fill='x')
        self.timer_label = ttk.Label(timer_frame, textvariable=self.timer_label_text, font=("Helvetica", 36))
        self.timer_label.pack()

        # Строка состояния
        status_frame = ttk.Frame(self.master, padding="10")
        status_frame.pack(fill='x', side='bottom')
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var)
        self.status_label.pack()

    def _bind_events(self) -> None:
        """Привязать события мыши и клавиатуры к обновлению активности."""
        self.master.bind("<Motion>", self._on_activity)
        self.master.bind("<Key>", self._on_activity)
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_activity(self, event: Optional[tk.Event] = None) -> None:
        """Обработчик активности пользователя."""
        if self.model:
            self.model.update_activity()

    def _on_start(self) -> None:
        """Обработчик кнопки «Запустить таймер»."""
        try:
            minutes = int(self.minutes_var.get())
            if minutes <= 0:
                raise ValueError("Введите положительное число.")
        except ValueError as e:
            messagebox.showerror("Ошибка", f"Некорректный ввод: {e}")
            return

        self.start_btn.config(state='disabled')
        self.cancel_btn.config(state='normal')
        self.minutes_entry.config(state='disabled')
        self.status_var.set(f"Таймер запущен на {minutes} минут...")
        self.model.start(minutes)

    def _on_cancel(self) -> None:
        """Обработчик кнопки «Отменить»."""
        self.model.cancel()
        self._reset_ui()
        self.status_var.set("Таймер отменён.")
        self.minutes_var.set("")
        self._close_choice_dialog()

    def _reset_ui(self) -> None:
        """Вернуть интерфейс в исходное состояние."""
        self.start_btn.config(state='normal')
        self.cancel_btn.config(state='disabled')
        self.minutes_entry.config(state='normal')
        self.timer_label_text.set("00:00")

    def update_timer_display(self, seconds: int) -> None:
        """Обновить отображаемое время."""
        mins, secs = divmod(seconds, 60)
        self.timer_label_text.set(f"{mins:02d}:{secs:02d}")

    def set_status(self, text: str) -> None:
        """Установить текст строки состояния."""
        self.status_var.set(text)

    def show_choice_dialog(self) -> None:
        """Открыть диалоговое окно с предложением продлить таймер."""
        self._close_choice_dialog()

        dialog = tk.Toplevel(self.master)
        dialog.title("Вы не спите?")
        dialog.geometry("400x220")
        dialog.resizable(False, False)
        dialog.transient(self.master)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._on_dialog_choice("cancel"))

        self.choice_dialog = dialog

        msg = ("Обнаружена активность.\n\n"
               "Нажмите «Да», чтобы запустить таймер заново с тем же временем.\n"
               "Нажмите «Нет», чтобы ввести новое время.\n"
               "Нажмите «Отмена», чтобы отменить таймер.\n\n"
               "Если не ответить, компьютер выключится через:")

        ttk.Label(dialog, text=msg, justify='left', wraplength=380).pack(pady=10, padx=10)

        self.dialog_countdown_var.set(f"{IDLE_CHECK_SECONDS} секунд")
        countdown_label = ttk.Label(
            dialog, textvariable=self.dialog_countdown_var,
            font=("Helvetica", 12, "bold"), foreground="red"
        )
        countdown_label.pack(pady=5)

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=15, padx=20, fill='x')

        ttk.Button(btn_frame, text="Да", command=lambda: self._on_dialog_choice("yes"), width=10)\
            .pack(side='left', padx=5, expand=True)
        ttk.Button(btn_frame, text="Нет", command=lambda: self._on_dialog_choice("no"), width=10)\
            .pack(side='left', padx=5, expand=True)
        ttk.Button(btn_frame, text="Отмена", command=lambda: self._on_dialog_choice("cancel"), width=10)\
            .pack(side='left', padx=5, expand=True)

        self.dialog_seconds_left = IDLE_CHECK_SECONDS
        self._update_dialog_countdown()

    def _update_dialog_countdown(self) -> None:
        """Обновить таймер обратного отсчёта в диалоговом окне."""
        if not self.choice_dialog or not tk.Toplevel.winfo_exists(self.choice_dialog):
            return

        self.dialog_countdown_var.set(f"{self.dialog_seconds_left} секунд")
        if self.dialog_seconds_left > 0:
            self.dialog_seconds_left -= 1
            self.dialog_timeout_id = self.master.after(1000, self._update_dialog_countdown)
        else:
            self._close_choice_dialog()
            # Время истекло – выключаем компьютер
            logger.info("Время ответа в диалоге истекло, выключение.")
            shutdown_system()
            sys.exit(0)

    def _on_dialog_choice(self, choice: str) -> None:
        """Обработать выбор пользователя в диалоге."""
        self._close_choice_dialog()

        if choice == "yes":
            self.model.start(int(self.minutes_var.get()))
        elif choice == "no":
            self._reset_ui()
            self.minutes_var.set("")
            self.minutes_entry.focus()
            self.status_var.set("Введите новое время.")
        else:  # cancel
            self.model.cancel()
            self._reset_ui()
            self.minutes_var.set("")
            self.status_var.set("Таймер отменён.")

    def _close_choice_dialog(self) -> None:
        """Закрыть диалоговое окно и отменить его таймер."""
        if self.dialog_timeout_id:
            self.master.after_cancel(self.dialog_timeout_id)
            self.dialog_timeout_id = None
        if self.choice_dialog and tk.Toplevel.winfo_exists(self.choice_dialog):
            self.choice_dialog.destroy()
            self.choice_dialog = None

    def _on_close(self) -> None:
        """Обработчик закрытия главного окна."""
        logger.info("Приложение закрыто пользователем.")
        if self.model:
            self.model.stop_app()
        self.master.destroy()

class SleepTimerApp:
    """Главный класс приложения, создающий модель и GUI."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.gui = SleepTimerGUI(root)

        self.model = TimerModel(
            on_tick=self.gui.update_timer_display,
            on_finish=lambda: self.gui.set_status("Проверка на сон... (30 секунд)"),
            on_idle_check_start=lambda: self.gui.set_status("Проверка на сон... (30 секунд)"),
            on_idle_detected=self._on_idle_detected
        )
        self.gui.set_model(self.model)

    def _on_idle_detected(self) -> None:
        """Вызывается, когда обнаружена активность во время проверки."""
        self.gui.set_status("Активность обнаружена. Ожидание ответа...")
        self.gui.show_choice_dialog()


def main() -> None:
    """Главная функция запуска приложения."""
    if sys.platform != "win32":
        messagebox.showwarning(
            "Предупреждение",
            "Приложение предназначено для Windows.\n"
            "Автоматическое выключение недоступно."
        )

    # Запрос прав администратора
    if not is_admin():
        logger.info("Приложение запущено без прав администратора. Запрос повышения...")
        run_as_admin()
    else:
        logger.info("Приложение запущено с правами администратора.")

    root = tk.Tk()
    app = SleepTimerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()