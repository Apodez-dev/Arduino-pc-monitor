import time
import threading
import json
import urllib.request
import os
import sys
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

class PCMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Arduino PC Monitor Bridge")
        self.root.geometry("340x260")
        self.root.resizable(False, False)

        self.arduino = None
        self.is_running = False
        self.tray_icon = None

        # Перехватываем закрытие окна (крестик), чтобы сворачивать в трей
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        # UI элементы
        lbl_port = ttk.Label(root, text="Выберите COM-порт Arduino:")
        lbl_port.pack(pady=(15, 5))

        self.port_combobox = ttk.Combobox(root, width=25, state="readonly")
        self.port_combobox.pack(pady=5)
        self.refresh_ports()

        btn_refresh = ttk.Button(root, text="Обновить список портов", command=self.refresh_ports)
        btn_refresh.pack(pady=2)

        # Чекбокс автозагрузки
        self.autostart_var = tk.BooleanVar(value=self.check_autostart())
        self.chk_autostart = ttk.Checkbutton(
            root, text="Запускать вместе с Windows", 
            variable=self.autostart_var, command=self.toggle_autostart
        )
        self.chk_autostart.pack(pady=8)

        self.btn_toggle = ttk.Button(root, text="Запустить мониторинг", command=self.toggle_connection)
        self.btn_toggle.pack(pady=(5, 5))

        self.lbl_status = ttk.Label(root, text="Статус: Не подключено", foreground="gray")
        self.lbl_status.pack(pady=5)

        # 1. Сразу скрываем окно при запуске (чтобы не мозолило глаза)
        self.root.withdraw()

        # 2. Запускаем поток иконки трея
        if TRAY_AVAILABLE:
            threading.Thread(target=self.setup_tray, daemon=True).start()

        # 3. Автоматически запускаем мониторинг в фоне при старте системы
        selected_port = self.port_combobox.get()
        if selected_port and selected_port != "Порты не найдены":
            threading.Thread(target=self.auto_start_background, args=(selected_port,), daemon=True).start()

    def refresh_ports(self):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combobox['values'] = ports
        if ports:
            if "COM8" in ports:
                self.port_combobox.set("COM8")
            else:
                self.port_combobox.current(0)
        else:
            self.port_combobox.set("Порты не найдены")

    def auto_start_background(self, port):
        """Автоматический запуск отправки данных в фоне без открытия окна"""
        time.sleep(3) # Пауза на инициализацию системы и LHM после старта ПК
        try:
            self.arduino = serial.Serial(port, 9600, timeout=1)
            time.sleep(2)  
            self.is_running = True
            
            # Обновляем текст статуса в UI (на случай если пользователь откроет окно)
            self.root.after(0, lambda: self.btn_toggle.config(text="Остановить"))
            self.root.after(0, lambda: self.port_combobox.config(state="disabled"))
            self.root.after(0, lambda: self.lbl_status.config(text=f"Статус: Подключено ({port})", foreground="green"))
            
            # Запускаем цикл отправки данных
            self.thread = threading.Thread(target=self.send_data_loop, daemon=True)
            self.thread.start()
        except Exception:
            pass

    def toggle_connection(self):
        if not self.is_running:
            selected_port = self.port_combobox.get()
            if not selected_port or selected_port == "Порты не найдены":
                messagebox.showerror("Ошибка", "Выберите корректный COM-порт!")
                return
            
            try:
                self.arduino = serial.Serial(selected_port, 9600, timeout=1)
                time.sleep(2)  
                self.is_running = True
                self.btn_toggle.config(text="Остановить")
                self.port_combobox.config(state="disabled")
                self.lbl_status.config(text=f"Статус: Подключено ({selected_port})", foreground="green")
                
                self.thread = threading.Thread(target=self.send_data_loop, daemon=True)
                self.thread.start()
            except Exception as e:
                messagebox.showerror("Ошибка подключения", str(e))
        else:
            self.stop_connection()

    def stop_connection(self):
        self.is_running = False
        if self.arduino and self.arduino.is_open:
            self.arduino.close()
        self.btn_toggle.config(text="Запустить мониторинг")
        self.port_combobox.config(state="readonly")
        self.lbl_status.config(text="Статус: Остановлено", foreground="gray")

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
                cpu_temp = self.get_lhm_cpu_temp()
                
                if cpu_temp is None:
                    cpu_temp = 40 + int(cpu_load * 0.48)
                    if cpu_temp > 95:
                        cpu_temp = 95

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
        
        self.stop_connection()

    def create_tray_image(self):
        image = Image.new('RGB', (64, 64), color=(30, 30, 30))
        draw = ImageDraw.Draw(image)
        draw.ellipse((16, 16, 48, 48), fill=(0, 160, 255))
        return image

    def setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Открыть настройки", self.show_window, default=True),
            pystray.MenuItem("Выход", self.exit_app)
        )
        self.tray_icon = pystray.Icon("PCMonitor", self.create_tray_image(), "PC Monitor Bridge", menu)
        self.tray_icon.run()

    def hide_to_tray(self):
        self.root.withdraw()

    def show_window(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def exit_app(self, icon=None, item=None):
        self.stop_connection()
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