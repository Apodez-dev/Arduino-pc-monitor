import time
import threading
import json
import urllib.request
import os
import sys
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
import psutil
import serial
import serial.tools.list_ports
import winreg

# Проверяем библиотеки для трея и PIL (картинки)
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# Подключаем NVML для точного мониторинга RTX 4070 Ti
try:
    import pynvml
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except Exception:
    NVML_AVAILABLE = False

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "ArduinoPCMonitor"

# Корректное определение папки для работы с конфигом как для .pyw, так и для скомпилированного .exe с пробелами в названии
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

def load_config():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    # Дефолтные настройки для самого первого запуска
    return {"start_minimized": False, "monitoring_active": False, "last_port": ""}

def save_config(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

class PCMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Arduino PC Monitor Bridge")
        self.root.geometry("340x380")
        self.root.resizable(False, False)

        self.config = load_config()
        self.arduino = None
        self.is_running = False
        self.tray_icon = None

        # Перехватываем закрытие окна (крестик), чтобы сворачивать в трей
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        # UI элементы
        lbl_title = ttk.Label(root, text="Arduino PC Monitor Bridge", font=("Arial", 10, "bold"))
        lbl_title.pack(pady=(10, 0))

        lbl_port = ttk.Label(root, text="Выберите COM-порт Arduino:")
        lbl_port.pack(pady=(10, 5))

        self.port_combobox = ttk.Combobox(root, width=25, state="readonly")
        self.port_combobox.pack(pady=5)
        self.refresh_ports()

        btn_refresh = ttk.Button(root, text="Обновить список портов", command=self.refresh_ports)
        btn_refresh.pack(pady=2)

        # Чекбокс автозагрузки (синхронизирован с реестром)
        real_autostart = self.check_autostart()
        self.autostart_var = tk.BooleanVar(value=real_autostart)
        self.chk_autostart = ttk.Checkbutton(
            root, text="Запускать вместе с Windows", 
            variable=self.autostart_var, command=self.toggle_autostart
        )
        self.chk_autostart.pack(pady=5)

        # Чекбокс запуска в свернутом виде
        self.minimized_var = tk.BooleanVar(value=self.config.get("start_minimized", False))
        self.chk_minimized = ttk.Checkbutton(
            root, text="Запускать в свернутом виде", 
            variable=self.minimized_var, command=self.save_settings
        )
        self.chk_minimized.pack(pady=5)

        # Проверяем состояние мониторинга из конфига
        should_monitor = self.config.get("monitoring_active", False)
        
        btn_text = "Остановить" if should_monitor else "Запустить мониторинг"
        self.btn_toggle = ttk.Button(root, text=btn_text, command=self.toggle_connection)
        self.btn_toggle.pack(pady=(5, 5))

        if should_monitor:
            saved_port = self.config.get("last_port", "")
            status_text = f"Статус: Подключено ({saved_port})" if saved_port else "Статус: Подключение..."
            self.lbl_status = ttk.Label(root, text=status_text, foreground="green")
            self.port_combobox.config(state="disabled")
        else:
            self.lbl_status = ttk.Label(root, text="Статус: Остановлено", foreground="gray")
        self.lbl_status.pack(pady=5)

        # Блок футера: ссылки слева, by Apodez справа
        footer_frame = ttk.Frame(root)
        footer_frame.pack(side="bottom", fill="x", padx=15, pady=10)

        links_frame = ttk.Frame(footer_frame)
        links_frame.pack(side="left")

        lbl_github = tk.Label(links_frame, text="GitHub", fg="blue", cursor="hand2", font=("Arial", 8, "underline"))
        lbl_github.pack(side="left", padx=(0, 5))
        lbl_github.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/Apodez-dev/Arduino-pc-monitor"))

        lbl_donate = tk.Label(links_frame, text="Поддержать", fg="blue", cursor="hand2", font=("Arial", 8, "underline"))
        lbl_donate.pack(side="left", padx=5)
        lbl_donate.bind("<Button-1>", lambda e: webbrowser.open("https://donatty.com/apodez"))

        lbl_author = ttk.Label(footer_frame, text="by Apodez", font=("Arial", 8, "italic"), foreground="gray")
        lbl_author.pack(side="right")

        # 1. Управление видимостью окна при старте
        if self.config.get("start_minimized", False) and should_monitor:
            self.root.withdraw()
        else:
            self.root.deiconify()

        # 2. Запускаем поток иконки трея
        if TRAY_AVAILABLE:
            threading.Thread(target=self.setup_tray, daemon=True).start()

        # 3. Автоматический запуск мониторинга в фоне, если он был активен в прошлый раз
        if should_monitor:
            saved_port = self.config.get("last_port", "")
            if saved_port and saved_port in self.port_combobox['values']:
                self.port_combobox.set(saved_port)
            
            selected_port = self.port_combobox.get()
            if selected_port and selected_port != "Выберите COM-порт..." and selected_port != "Порты не найдены":
                threading.Thread(target=self.auto_start_background, args=(selected_port,), daemon=True).start()
            else:
                self.stop_connection()

    def save_settings(self):
        self.config["start_minimized"] = self.minimized_var.get()
        save_config(self.config)

    def refresh_ports(self):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combobox['values'] = ports
        
        last_port = self.config.get("last_port", "")
        if last_port in ports:
            self.port_combobox.set(last_port)
        elif ports:
            if not os.path.exists(CONFIG_PATH) or not last_port:
                self.port_combobox.set("Выберите COM-порт...")
            else:
                self.port_combobox.current(0)
        else:
            self.port_combobox.set("Порты не найдены")

    def auto_start_background(self, port):
        """Автоматический запуск отправки данных в фоне при старте"""
        time.sleep(2) 
        try:
            self.arduino = serial.Serial(port, 9600, timeout=1)
            time.sleep(2)  
            self.is_running = True
            
            self.config["monitoring_active"] = True
            save_config(self.config)
            
            self.root.after(0, lambda: self.btn_toggle.config(text="Остановить"))
            self.root.after(0, lambda: self.port_combobox.config(state="disabled"))
            self.root.after(0, lambda: self.lbl_status.config(text=f"Статус: Подключено ({port})", foreground="green"))
            
            self.thread = threading.Thread(target=self.send_data_loop, daemon=True)
            self.thread.start()
        except Exception:
            self.is_running = False
            self.config["monitoring_active"] = False
            save_config(self.config)
            self.root.after(0, lambda: self.stop_connection())

    def toggle_connection(self):
        if not self.is_running:
            selected_port = self.port_combobox.get()
            if not selected_port or selected_port == "Выберите COM-порт..." or selected_port == "Порты не найдены":
                messagebox.showerror("Ошибка", "Выберите корректный COM-порт!")
                return
            
            try:
                self.arduino = serial.Serial(selected_port, 9600, timeout=1)
                time.sleep(2)  
                self.is_running = True
                self.btn_toggle.config(text="Остановить")
                self.port_combobox.config(state="disabled")
                self.lbl_status.config(text=f"Статус: Подключено ({selected_port})", foreground="green")
                
                # Сохраняем в конфиг активный статус и выбранный порт
                self.config["monitoring_active"] = True
                self.config["last_port"] = selected_port
                save_config(self.config)
                
                self.thread = threading.Thread(target=self.send_data_loop, daemon=True)
                self.thread.start()
            except Exception as e:
                messagebox.showerror("Ошибка подключения", str(e))
        else:
            self.stop_connection()

    def stop_connection(self):
        self.is_running = False
        if self.arduino and self.arduino.is_open:
            try:
                self.arduino.close()
            except Exception:
                pass
        self.btn_toggle.config(text="Запустить мониторинг")
        self.port_combobox.config(state="readonly")
        self.lbl_status.config(text="Статус: Остановлено", foreground="gray")
        self.update_tray_icon_color(False)

        # Сохраняем статус остановки в конфиг только при ручном нажатии «Остановить»
        self.config["monitoring_active"] = False
        save_config(self.config)

    def get_lhm_cpu_temp(self):
        """Чтение температуры CPU Package из LibreHardwareMonitor"""
        try:
            req = urllib.request.Request("http://localhost:8085/data.json", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=0.5) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                def search_node(node):
                    name = node.get("Text", "")
                    if "Temperature" in name or "Temperatures" in name:
                        for child in node.get("Children", []):
                            child_name = child.get("Text", "")
                            if child_name == "CPU Package":
                                val_str = child.get("Value", "0").replace(" °C", "").replace(",", ".")
                                try:
                                    return int(float(val_str))
                                except ValueError:
                                    pass
                    for child in node.get("Children", []):
                        res = search_node(child)
                        if res is not None:
                            return res
                    return None

                temp = search_node(data)
                
                if temp is None:
                    def search_fallback(node):
                        name = node.get("Text", "")
                        if "Temperature" in name or "Temperatures" in name:
                            for child in node.get("Children", []):
                                child_name = child.get("Text", "")
                                if "Core Max" in child_name or "Core Average" in child_name:
                                    val_str = child.get("Value", "0").replace(" °C", "").replace(",", ".")
                                    try:
                                        return int(float(val_str))
                                    except ValueError:
                                        pass
                        for child in node.get("Children", []):
                            res = search_fallback(child)
                            if res is not None:
                                return res
                        return None
                    temp = search_fallback(data)

                return temp
        except Exception:
            return None

    def send_data_loop(self):
        psutil.cpu_percent(interval=None)
        
        while self.is_running:
            try:
                cpu_load = int(psutil.cpu_percent(interval=None))
                cpu_temp_raw = self.get_lhm_cpu_temp()
                
                if cpu_temp_raw is not None:
                    cpu_temp = cpu_temp_raw
                    self.root.after(0, lambda: self.update_tray_icon_color(True))
                else:
                    cpu_temp = 40 + int(cpu_load * 0.48)
                    if cpu_temp > 95:
                        cpu_temp = 95
                    self.root.after(0, lambda: self.update_tray_icon_color(False))

                encoded_cpu_temp = 100 - cpu_temp

                gpu_temp = 0
                gpu_load = 0
                gpu_mem = 0
                
                if NVML_AVAILABLE:
                    try:
                        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                        gpu_temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                        gpu_load = util.gpu
                        
                        mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                        gpu_mem = int((mem_info.used / mem_info.total) * 100)
                    except Exception:
                        pass

                mb_temp = 35
                hdd_temp = 30
                ram_load = int(psutil.virtual_memory().percent)
                
                data_string = f"{encoded_cpu_temp};{gpu_temp};{mb_temp};{hdd_temp};{cpu_load};{gpu_load};{ram_load};{gpu_mem};0;0;E"
                
                if self.arduino and self.arduino.is_open:
                    self.arduino.write(data_string.encode("utf-8"))
                
                time.sleep(1)
            except Exception:
                break

    def create_tray_image(self, green=False):
        image = Image.new('RGB', (64, 64), color=(30, 30, 30))
        draw = ImageDraw.Draw(image)
        color = (0, 200, 100) if green else (220, 50, 50)
        draw.ellipse((16, 16, 48, 48), fill=color)
        return image

    def update_tray_icon_color(self, green):
        if not TRAY_AVAILABLE or not self.tray_icon:
            return
        self.tray_icon.icon = self.create_tray_image(green)

    def setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Открыть настройки", self.show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⭐ GitHub проекта", lambda: webbrowser.open("https://github.com/Apodez-dev/Arduino-pc-monitor")),
            pystray.MenuItem("☕ Поддержать автора", lambda: webbrowser.open("https://donatty.com/apodez")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self.exit_app)
        )
        self.tray_icon = pystray.Icon("PCMonitor", self.create_tray_image(False), "Arduino PC Monitor Bridge", menu)
        self.tray_icon.run()

    def hide_to_tray(self):
        self.root.withdraw()

    def show_window(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def exit_app(self, icon=None, item=None):
        # При выходе из трея сохраняем текущий статус работы
        self.config["monitoring_active"] = self.is_running
        current_port = self.port_combobox.get()
        if current_port and current_port not in ["Выберите COM-порт...", "Порты не найдены"]:
            self.config["last_port"] = current_port
        save_config(self.config)

        self.is_running = False
        if self.arduino and self.arduino.is_open:
            try:
                self.arduino.close()
            except Exception:
                pass
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.destroy)

    def check_autostart(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False

    def toggle_autostart(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_WRITE)
            if self.autostart_var.get():
                script_path = sys.executable.replace("python.exe", "pythonw.exe") + ' "' + os.path.abspath(sys.argv[0]) + '"'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, script_path)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось изменить автозагрузку: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = PCMonitorApp(root)
    root.mainloop()