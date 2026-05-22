import os
import sys
import json
import winreg
import ctypes
from ctypes import wintypes
import socket
import subprocess
import re
import threading
import time
import hashlib
import logging
import struct
import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime as dt
import zipfile
import platform
import urllib.request
import urllib.error
import platform
import glob
import base64

try:
    from pywinauto import Application, Desktop
    from pywinauto.keyboard import send_keys
except ImportError:
    Application = Desktop = send_keys = None

# Win32 UI Automation helpers without external dependencies
USER32 = ctypes.windll.user32
KERNEL32 = ctypes.windll.kernel32
BM_CLICK = 0x00F5
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1
BST_CHECKED = 1
BST_UNCHECKED = 0

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)


def get_window_text(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def get_class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def enum_windows():
    windows = []

    @EnumWindowsProc
    def _enum_proc(hwnd, lparam):
        if USER32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    USER32.EnumWindows(_enum_proc, 0)
    return windows


def enum_child_windows(parent):
    children = []

    @EnumChildProc
    def _enum_child_proc(hwnd, lparam):
        children.append(hwnd)
        return True

    USER32.EnumChildWindows(parent, _enum_child_proc, 0)
    return children


def enum_all_child_windows(parent):
    all_children = []

    def _walk(hwnd):
        for child in enum_child_windows(hwnd):
            all_children.append(child)
            _walk(child)

    _walk(parent)
    return all_children


def normalize_control_text(text):
    if not text:
        return ''
    cleaned = text.replace('&', '').replace('…', '').replace('...', '')
    cleaned = re.sub(r'[\s\t\r\n]+', ' ', cleaned)
    return cleaned.strip().lower()


def find_window_by_title(patterns, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        for hwnd in enum_windows():
            text = normalize_control_text(get_window_text(hwnd))
            if not text:
                continue
            for p in patterns:
                if normalize_control_text(p) in text:
                    return hwnd
        time.sleep(0.5)
    return None


def find_child_by_text(hwnd, patterns, class_name=None, recursive=True):
    for child in enum_child_windows(hwnd):
        try:
            if class_name and get_class_name(child) != class_name:
                continue
            text = normalize_control_text(get_window_text(child))
            if text:
                for p in patterns:
                    if normalize_control_text(p) in text:
                        return child
        except Exception:
            continue
        if recursive:
            found = find_child_by_text(child, patterns, class_name, recursive)
            if found:
                return found
    return None


def click_button(hwnd):
    try:
        USER32.SendMessageW(hwnd, BM_CLICK, 0, 0)
        return True
    except Exception:
        return False


def get_checkbox_state(hwnd):
    try:
        return USER32.SendMessageW(hwnd, BM_GETCHECK, 0, 0)
    except Exception:
        return BST_UNCHECKED


def set_checkbox(hwnd, checked):
    try:
        state = BST_CHECKED if checked else BST_UNCHECKED
        USER32.SendMessageW(hwnd, BM_SETCHECK, state, 0)
        return True
    except Exception:
        return False


# GUI Libraries
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

# Обробка зображень
from PIL import Image, ImageTk, ImageEnhance

# Системна інформація
import psutil
import hashlib

# Інформація про CPU
try:
    import cpuinfo
except ImportError:
    cpuinfo = None

# Інформація про GPU
try:
    import wmi
except ImportError:
    wmi = None
import ctypes
from ctypes import wintypes

# ══════════════════════════════════════════════════════════════════
# БЛОК: СИСТЕМНІ ХАРАКТЕРИСТИКИ
# ══════════════════════════════════════════════════════════════════


# Константи
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400


# Перевірка типу фізичних дисків через DeviceIoControl / STORAGE_PROPERTY_QUERY
class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [
        ("PropertyId", wintypes.DWORD),
        ("QueryType", wintypes.DWORD),
        ("AdditionalParameters", wintypes.BYTE * 1)
    ]


class STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("DeviceType", wintypes.BYTE),
        ("DeviceTypeModifier", wintypes.BYTE),
        ("RemovableMedia", wintypes.BOOLEAN),
        ("CommandQueueing", wintypes.BOOLEAN),
        ("VendorIdOffset", wintypes.DWORD),
        ("ProductIdOffset", wintypes.DWORD),
        ("ProductRevisionOffset", wintypes.DWORD),
        ("SerialNumberOffset", wintypes.DWORD),
        ("BusType", wintypes.DWORD),
        ("RawPropertiesLength", wintypes.DWORD),
        ("RawDeviceProperties", wintypes.BYTE * 1)
    ]


# Метод 1: визначення типу диска через IOCTL_STORAGE_QUERY_PROPERTY
# Повертає тип та шину для кожного PhysicalDrive
def get_disk_type_ioctl():
    disk_info = {}

    for i in range(10):  # PhysicalDrive0-9
        path = f"\\\\.\\PhysicalDrive{i}"

        handle = ctypes.windll.kernel32.CreateFileW(
            path,
            0,
            0,
            None,
            3,
            0,
            None
        )

        if handle == -1:
            continue

        query = STORAGE_PROPERTY_QUERY()
        query.PropertyId = 0
        query.QueryType = 0

        buffer = ctypes.create_string_buffer(1024)
        returned = wintypes.DWORD()

        success = ctypes.windll.kernel32.DeviceIoControl(
            handle,
            IOCTL_STORAGE_QUERY_PROPERTY,
            ctypes.byref(query),
            ctypes.sizeof(query),
            buffer,
            ctypes.sizeof(buffer),
            ctypes.byref(returned),
            None
        )

        if success:
            descriptor = STORAGE_DEVICE_DESCRIPTOR.from_buffer_copy(buffer)

            bus = descriptor.BusType

            # 🔥 ГОЛОВНЕ
            if bus == 17:
                dtype = "SSD"
            elif bus in [3, 11]:
                dtype = "Unknown"
            elif bus == 7:
                dtype = "USB"
            else:
                dtype = "Unknown"

            disk_info[f"PhysicalDrive{i}"] = {
                "type": dtype,
                "bus": bus,
                "source": "IOCTL"
            }

        ctypes.windll.kernel32.CloseHandle(handle)

    return disk_info


# Метод 2: розширене визначення диска через PRO IOCTL і перевірку ATA/NVMe
# Використовується для додаткової точності при визначенні SSD/HDD
def detect_disk_type_pro():
    disk_info = {}

    import ctypes
    from ctypes import wintypes

    IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
    IOCTL_STORAGE_PROTOCOL_COMMAND = 0x4D004

    class STORAGE_PROPERTY_QUERY(ctypes.Structure):
        _fields_ = [
            ("PropertyId", wintypes.DWORD),
            ("QueryType", wintypes.DWORD),
            ("AdditionalParameters", wintypes.BYTE * 1)
        ]

    class STORAGE_PROTOCOL_SPECIFIC_DATA(ctypes.Structure):
        _fields_ = [
            ("ProtocolType", wintypes.DWORD),
            ("DataType", wintypes.DWORD),
            ("ProtocolDataRequestValue", wintypes.DWORD),
            ("ProtocolDataRequestSubValue", wintypes.DWORD),
            ("ProtocolDataOffset", wintypes.DWORD),
            ("ProtocolDataLength", wintypes.DWORD),
            ("FixedProtocolReturnData", wintypes.DWORD),
            ("ProtocolDataRequestSubValue2", wintypes.DWORD),
            ("ProtocolDataRequestSubValue3", wintypes.DWORD),
            ("ProtocolDataRequestSubValue4", wintypes.DWORD),
        ]

    for i in range(10):
        path = f"\\\\.\\PhysicalDrive{i}"

        handle = ctypes.windll.kernel32.CreateFileW(
            path,
            0,
            0,
            None,
            3,
            0,
            None
        )

        if handle == -1:
            continue

        # ===== 1. BUS TYPE =====
        query = STORAGE_PROPERTY_QUERY()
        query.PropertyId = 0
        query.QueryType = 0

        buffer = ctypes.create_string_buffer(1024)
        returned = wintypes.DWORD()

        ok = ctypes.windll.kernel32.DeviceIoControl(
            handle,
            IOCTL_STORAGE_QUERY_PROPERTY,
            ctypes.byref(query),
            ctypes.sizeof(query),
            buffer,
            ctypes.sizeof(buffer),
            ctypes.byref(returned),
            None
        )

        disk_type = "Unknown"
        bus = -1

        if ok:
            bus = int.from_bytes(buffer[28:32], "little")

        # ===== 2. NVMe DETECT =====
        if bus == 17:
            disk_type = "SSD"

        # ===== 3. SATA → перевірка через ATA =====
        elif bus in [3, 11]:
            try:
                ata_buffer = ctypes.create_string_buffer(512)

                ok2 = ctypes.windll.kernel32.DeviceIoControl(
                    handle,
                    0x0007c088,  # IOCTL_ATA_PASS_THROUGH
                    None,
                    0,
                    ata_buffer,
                    512,
                    ctypes.byref(returned),
                    None
                )

                if ok2:
                    rotation = int.from_bytes(ata_buffer[434:436], "little")
                    if rotation == 1:
                        disk_type = "SSD"
                    elif rotation > 1:
                        disk_type = "HDD"
                    else:
                        disk_type = "Unknown"
                else:
                    disk_type = "Unknown"

            except:
                disk_type = "Unknown"

        elif bus == 7:
            disk_type = "USB"

        disk_info[f"PhysicalDrive{i}"] = {
            "type": disk_type,
            "bus": bus,
            "source": "PRO_IOCTL"
        }

        ctypes.windll.kernel32.CloseHandle(handle)

    return disk_info


# ══════════════════════════════════════════════════════════════════
# НАЛАШТУВАННЯ ЛОГУВАННЯ (МІНІМАЛЬНЕ)
# ══════════════════════════════════════════════════════════════════

log_messages = []


class MemoryHandler(logging.Handler):
    """Зберігає всі логи в пам'яті"""

    def emit(self, record):
        log_messages.append(self.format(record))


logger = logging.getLogger("UAC_Scanner")
logger.setLevel(logging.WARNING)  # ТІЛЬКИ WARNING, не DEBUG!
handler = MemoryHandler()
formatter = logging.Formatter('%(levelname)s: %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


# ══════════════════════════════════════════════════════════════════
# ПОКРАЩЕНЕ ЗАПУСК З SPLASH ЕКРАНОМ
# ══════════════════════════════════════════════════════════════════

class SplashScreen:
    """Splash екран при запуску"""

    def __init__(self, parent=None):
        self.splash = tk.Toplevel(parent)
        self.splash.geometry("600x400")
        self.splash.configure(bg="#11161c")
        self.splash.resizable(False, False)
        self.splash.overrideredirect(True)

        # Центрування
        screen_width = self.splash.winfo_screenwidth()
        screen_height = self.splash.winfo_screenheight()
        x = (screen_width - 600) // 2
        y = (screen_height - 400) // 2
        self.splash.geometry(f"+{x}+{y}")

        # Вміст
        frame = tk.Frame(self.splash, bg="#11161c")
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="🔍", font=("Arial", 60), bg="#11161c", fg="#79c0ff").pack(pady=20)
        tk.Label(frame, text="UAC-0010 SCANNER v15.0", font=("Segoe UI", 20, "bold"),
                 bg="#11161c", fg="#79c0ff").pack()
        tk.Label(frame, text="Pterodo Detection Engine", font=("Segoe UI", 12),
                 bg="#11161c", fg="#c9d1d9").pack()

        self.status = tk.Label(frame, text="⏳ Ініціалізація...", font=("Segoe UI", 10),
                               bg="#11161c", fg="#3fb950")
        self.status.pack(pady=20)

        # Прогресс бар
        self.progress = ttk.Progressbar(frame, length=400, mode='indeterminate')
        self.progress.pack(pady=10)
        self.progress.start()

        self.splash.update()

    def update_status(self, text):
        self.status.config(text=text)
        self.splash.update()

    def close(self):
        try:
            self.splash.destroy()
        except:
            pass


def request_admin_privileges():
    """
    Запросити права адміністратора якщо вони не є
    Повторно запускає программу з адміном
    """
    try:
        if platform.system() != "Windows":
            return True  # Linux/Mac - не потребують

        if ctypes.windll.shell32.IsUserAnAdmin():
            return True  # Уже адмін

        # Запускаємо себе з адміном
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )

        sys.exit(0)

    except Exception as e:
        logger.warning(f"Не вдалося запросити адмін: {e}")
        return False


# ══════════════════════════════════════════════════════════════════
# v15.0 ДЕТЕКЦІЯ ДИСКА - 7 МЕТОДІВ (МАКСИМАЛЬНА ТОЧНІСТЬ)
# ══════════════════════════════════════════════════════════════════

# Блок детекції типу дисків: комбінує декілька методів для SSD/HDD/NVMe/USB
class AdvancedDiskDetectorV15:
    """v15.0: ДЕТЕКЦІЯ ДИСКА З 7 МЕТОДІВ"""

    NVME_PATTERNS = [
        r"nvme", r"970 evo", r"980 pro", r"sn850", r"sn860", r"sn870", r"sn880",
        r"samsung 980", r"samsung 990", r"sk hynix", r"crucial p", r"crucial p5",
        r"kingston a", r"kingston a2", r"western digital sn", r"intel 660p",
        r"intel 670p", r"intel 760p", r"adata xpg", r"transcend 110q", r"patriot p400",
        r"corsair mp40", r"corsair mp60", r"wd black sn", r"wd red sn", r"seagate barracuda rc",
    ]

    SSD_SATA_PATTERNS = [
        r"ssd", r"solid state", r"samsung 860", r"samsung 870", r"samsung 830", r"samsung evo",
        r"wd blue", r"wd blue 3d", r"wd green", r"wd red", r"wd black", r"crucial mx",
        r"crucial bx", r"kingston sa", r"kingston a", r"intel 660p", r"intel 670p",
        r"intel 760p", r"adata su", r"adata xpg", r"transcend", r"sk hynix gold",
        r"micron mx", r"sandisk", r"sandisk ultra", r"sandisk extreme", r"toshiba tr200", r"toshiba rc",
    ]

    HDD_PATTERNS = [
        r"^st\d", r"^wd ", r"barracuda", r"seagate", r"toshiba", r"hitachi",
        r"wd black", r"wd purple", r"wd red pro", r"wd gold", r"wd blue", r"caviar",
        r"maxtor", r"fujitsu", r"easystore", r"mybook", r"caviar green", r"caviar blue",
        r"caviar black", r"st1000", r"st2000", r"st4000", r"st8000",
    ]

    @staticmethod
    def detect_disk_type_wmi_advanced():
        """Метод 1: WMI (НОРМАЛЬНИЙ через MSFT_PhysicalDisk)"""
        disk_info = {}

        if platform.system() != "Windows":
            return disk_info

        try:
            # Головне: новий namespace
            c = wmi.WMI(namespace="root\\Microsoft\\Windows\\Storage")

            for disk in c.MSFT_PhysicalDisk():
                try:
                    model_name = str(disk.FriendlyName).strip()

                    bus = int(disk.BusType) if disk.BusType is not None else -1
                    media = int(disk.MediaType) if disk.MediaType is not None else -1
                    spindle = disk.SpindleSpeed

                    disk_type = "Unknown"

                    # 🔥 1. NVMe (найважливіше)
                    if bus == 17:
                        disk_type = "SSD"

                    # 🔥 2. SATA SSD / HDD
                    elif bus in [11, 3]:  # SATA / ATA
                        if spindle and int(spindle) > 0:
                            disk_type = "HDD"
                        else:
                            disk_type = "SSD"

                    # 🔥 3. USB
                    elif bus == 7:
                        disk_type = "USB"

                    # 🔥 4. Fallback через MediaType
                    else:
                        if media == 3:
                            disk_type = "HDD"
                        elif media == 4:
                            disk_type = "SSD"

                    disk_info[model_name] = {
                        'type': disk_type,
                        'source': 'MSFT_PhysicalDisk',
                        'bus': bus,
                        'media': media,
                    }

                    logger.info(f"MSFT: {model_name} → {disk_type} (Bus={bus}, Media={media})")

                except Exception as e:
                    logger.debug(f"MSFT disk error: {e}")

        except Exception as e:
            logger.debug(f"MSFT_PhysicalDisk недоступний: {e}")

        return disk_info

    @staticmethod
    def detect_disk_type_wmic():
        """Метод 2: WMIC"""
        disk_info = {}
        if platform.system() != "Windows":
            return disk_info
        try:
            result = subprocess.run(
                ["wmic", "logicaldisk", "get", "name,size,drivetype"],
                capture_output=True, text=True, timeout=10,
                creationflags=0x08000000
            )
            if result and result.stdout:
                lines = result.stdout.split('\n')
                for line in lines[1:]:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 2:
                            drive = parts[0]
                            try:
                                size_bytes = int(parts[1]) if len(parts) > 1 else 0
                                drive_type = int(parts[2]) if len(parts) > 2 else 0

                                if size_bytes > 0:
                                    size_gb = size_bytes / (1024 ** 3)
                                    dtype = "HDD" if drive_type == 3 else "SSD" if drive_type == 2 else "Unknown"
                                    disk_info[drive] = {
                                        'type': dtype,
                                        'source': 'WMIC',
                                        'size_gb': round(size_gb, 1),
                                    }
                                    logger.info(f"WMIC: {drive} ({size_gb:.1f}GB) → {dtype}")
                            except:
                                pass
        except Exception as e:
            logger.debug(f"WMIC помилка: {e}")
        return disk_info

    @staticmethod
    def detect_disk_type_psutil():
        """Метод 3: psutil (для дисків)"""
        disk_info = {}
        try:
            for partition in psutil.disk_partitions():
                if 'cdrom' in partition.opts or partition.fstype == '':
                    continue
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    size_gb = usage.total / (1024 ** 3)
                    # Спроба визначити тип з розміру та частоти доступу
                    disk_info[partition.device] = {
                        'type': 'Unknown',
                        'source': 'psutil',
                        'size_gb': round(size_gb, 1),
                        'fstype': partition.fstype,
                    }
                    logger.info(f"psutil: {partition.device} ({size_gb:.1f}GB)")
                except:
                    pass
        except Exception as e:
            logger.debug(f"psutil помилка: {e}")
        return disk_info

    @staticmethod
    def detect_disk_type_registry_scsi():
        """Метод 4: Registry SCSI"""
        disk_info = {}
        if platform.system() != "Windows":
            return disk_info
        try:
            scsi_path = r"SYSTEM\CurrentControlSet\Enum\SCSI"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, scsi_path) as key:
                idx = 0
                while True:
                    try:
                        device_name = winreg.EnumKey(key, idx)
                        device_path = f"{scsi_path}\\{device_name}"
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, device_path) as device_key:
                            sub_idx = 0
                            while True:
                                try:
                                    sub_device = winreg.EnumKey(device_key, sub_idx)
                                    sub_path = f"{device_path}\\{sub_device}"
                                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path) as sub_key:
                                        try:
                                            friendly_name, _ = winreg.QueryValueEx(sub_key, "FriendlyName")
                                            friendly_str = str(friendly_name).strip()
                                            if "@cdrom" not in friendly_str.lower():
                                                disk_type = AdvancedDiskDetectorV15.detect_disk_type(friendly_str)
                                                disk_info[friendly_str] = {
                                                    'type': disk_type,
                                                    'source': 'Registry SCSI'
                                                }
                                                logger.info(f"Registry SCSI: {friendly_str} → {disk_type}")
                                        except:
                                            pass
                                    sub_idx += 1
                                except WindowsError:
                                    break
                        idx += 1
                    except WindowsError:
                        break
        except Exception as e:
            logger.debug(f"Registry SCSI помилка: {e}")
        return disk_info

    @staticmethod
    def detect_disk_type_registry_ide():
        """Метод 5: Registry IDE"""
        disk_info = {}
        if platform.system() != "Windows":
            return disk_info
        try:
            ide_path = r"SYSTEM\CurrentControlSet\Enum\IDE"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ide_path) as key:
                idx = 0
                while True:
                    try:
                        device_name = winreg.EnumKey(key, idx)
                        device_path = f"{ide_path}\\{device_name}"
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, device_path) as device_key:
                            sub_idx = 0
                            while True:
                                try:
                                    sub_device = winreg.EnumKey(device_key, sub_idx)
                                    sub_path = f"{device_path}\\{sub_device}"
                                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path) as sub_key:
                                        try:
                                            friendly_name, _ = winreg.QueryValueEx(sub_key, "FriendlyName")
                                            friendly_str = str(friendly_name).strip()
                                            disk_type = AdvancedDiskDetectorV15.detect_disk_type(friendly_str)
                                            disk_info[friendly_str] = {
                                                'type': disk_type,
                                                'source': 'Registry IDE'
                                            }
                                            logger.info(f"Registry IDE: {friendly_str} → {disk_type}")
                                        except:
                                            pass
                                    sub_idx += 1
                                except WindowsError:
                                    break
                        idx += 1
                    except WindowsError:
                        break
        except Exception as e:
            logger.debug(f"Registry IDE помилка: {e}")
        return disk_info

    @staticmethod
    def detect_disk_type_registry_pci():
        """Метод 6: Registry PCI (для NVMe)"""
        disk_info = {}
        if platform.system() != "Windows":
            return disk_info
        try:
            pci_path = r"SYSTEM\CurrentControlSet\Enum\PCI"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, pci_path) as key:
                idx = 0
                while True:
                    try:
                        pci_device = winreg.EnumKey(key, idx)
                        if 'nvme' in pci_device.lower() or '0108' in pci_device:
                            pci_device_path = f"{pci_path}\\{pci_device}"
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, pci_device_path) as pci_key:
                                try:
                                    friendly_name, _ = winreg.QueryValueEx(pci_key, "FriendlyName")
                                    friendly_str = str(friendly_name).strip()
                                    disk_info[friendly_str] = {
                                        'type': 'SSD',
                                        'source': 'Registry PCI/NVMe'
                                    }
                                    logger.info(f"Registry PCI: {friendly_str} → SSD")
                                except:
                                    pass
                        idx += 1
                    except WindowsError:
                        break
        except Exception as e:
            logger.debug(f"Registry PCI помилка: {e}")
        return disk_info

    @staticmethod
    def detect_disk_type_smart():
        """Метод 7: SMART дані (через PowerShell обхід)"""
        disk_info = {}
        if platform.system() != "Windows":
            return disk_info
        try:
            # Обхід PowerShell - використовуємо CMD та WMI
            result = subprocess.run(
                ["wmic", "diskdrive", "get", "model,interfacetype"],
                capture_output=True, text=True, timeout=10,
                creationflags=0x08000000
            )
            if result and result.stdout:
                lines = result.stdout.split('\n')
                for line in lines[1:]:
                    if line.strip():
                        parts = line.rsplit(' ', 1)
                        if len(parts) == 2:
                            model = parts[0].strip()
                            iface = parts[1].strip()
                            if 'nvme' in iface.lower():
                                disk_info[model] = {
                                    'type': 'SSD',
                                    'source': 'SMART/WMI',
                                    'interface': iface
                                }
                                logger.info(f"SMART: {model} → SSD")
        except Exception as e:
            logger.debug(f"SMART помилка: {e}")
        return disk_info

    @staticmethod
    def detect_disk_type(model_name):
        """Розпізнати тип диска за назвою"""
        if not model_name:
            return "Unknown"
        model_lower = model_name.lower()

        for pattern in AdvancedDiskDetectorV15.NVME_PATTERNS:
            if re.search(pattern, model_lower):
                return "SSD"

        for pattern in AdvancedDiskDetectorV15.SSD_SATA_PATTERNS:
            if re.search(pattern, model_lower):
                return "SSD"

        for pattern in AdvancedDiskDetectorV15.HDD_PATTERNS:
            if re.search(pattern, model_lower):
                return "HDD"

        if "ssd" in model_lower:
            return "SSD"

        return "Unknown"

    @staticmethod
    def get_all_disk_models():
        pro_info = detect_disk_type_pro()
        logger.info(f"✓ PRO IOCTL: {len(pro_info)} дисків")
        ioctl_info = get_disk_type_ioctl()
        logger.info(f"✓ IOCTL: {len(ioctl_info)} дисків")
        """Отримати ВСІ моделі дисків з 7 методів"""
        logger.info("═" * 70)
        logger.info("Запуск комплексної детекції дисків v15.0 (7 методів)...")
        logger.info("═" * 70)

        wmi_info = AdvancedDiskDetectorV15.detect_disk_type_wmi_advanced()
        logger.info(f"✓ WMI: {len(wmi_info)} дисків")

        wmic_info = AdvancedDiskDetectorV15.detect_disk_type_wmic()
        logger.info(f"✓ WMIC: {len(wmic_info)} дисків")

        psutil_info = AdvancedDiskDetectorV15.detect_disk_type_psutil()
        logger.info(f"✓ psutil: {len(psutil_info)} дисків")

        registry_scsi_info = AdvancedDiskDetectorV15.detect_disk_type_registry_scsi()
        logger.info(f"✓ Registry SCSI: {len(registry_scsi_info)} дисків")

        registry_ide_info = AdvancedDiskDetectorV15.detect_disk_type_registry_ide()
        logger.info(f"✓ Registry IDE: {len(registry_ide_info)} дисків")

        registry_pci_info = AdvancedDiskDetectorV15.detect_disk_type_registry_pci()
        logger.info(f"✓ Registry PCI: {len(registry_pci_info)} дисків")

        smart_info = AdvancedDiskDetectorV15.detect_disk_type_smart()
        logger.info(f"✓ SMART/WMI: {len(smart_info)} дисків")

        # Мердж з пріоритетом
        merged = {}

        # SSD - найвищий пріоритет
        for source_dict in [pro_info, ioctl_info, wmi_info, registry_pci_info, smart_info, wmic_info,
                            registry_scsi_info, registry_ide_info,
                            psutil_info]:
            for k, v in source_dict.items():
                if v.get('type') == 'SSD' and k not in merged:
                    merged[k] = v

        # HDD
        for source_dict in [pro_info, ioctl_info, wmi_info, registry_scsi_info, psutil_info]:
            for k, v in source_dict.items():
                if v.get('type') == 'HDD' and k not in merged:
                    merged[k] = v

        # HDD
        for source_dict in [pro_info, ioctl_info, wmi_info, registry_scsi_info, psutil_info]:
            for k, v in source_dict.items():
                if v.get('type') == 'HDD' and k not in merged:
                    merged[k] = v

        # Unknown останні
        for source_dict in [pro_info, ioctl_info, wmi_info, wmic_info, psutil_info, registry_scsi_info,
                            registry_ide_info]:
            for k, v in source_dict.items():
                if k not in merged:
                    merged[k] = v

        logger.info(f"\n✓ ВСЬОГО УНІКАЛЬНИХ ДИСКІВ: {len(merged)}")
        for name, info in merged.items():
            logger.info(f"  • {name}: {info.get('type')} ({info.get('source')})")
        logger.info("═" * 70 + "\n")

        return merged

    @staticmethod
    def map_drives_to_types():
        """Замапити C:, D:, E: на типи дисків"""
        drive_types = {}
        try:
            disk_models = AdvancedDiskDetectorV15.get_all_disk_models()

            for partition in psutil.disk_partitions():
                if 'cdrom' in partition.opts or partition.fstype == '':
                    continue

                drive_letter = partition.device
                disk_type = "Unknown"

                try:
                    if wmi:
                        w = wmi.WMI()
                        for disk in w.Win32_LogicalDisk():
                            if disk.DeviceID == drive_letter:
                                for phys_disk in w.Win32_DiskDrive():
                                    phys_model = str(phys_disk.Model).strip() if phys_disk.Model else ""
                                    for model_name, model_info in disk_models.items():
                                        if phys_model.lower() in model_name.lower() or model_name.lower() in phys_model.lower():
                                            disk_type = model_info.get('type', 'Unknown')
                                            logger.info(f"WMI Map: {drive_letter} ({phys_model}) → {disk_type}")
                                            break
                                    if disk_type != "Unknown":
                                        break
                except Exception as e:
                    logger.debug(f"WMI mapping error: {e}")

                # Fallback
                if disk_type == "Unknown":
                    for model_name, model_info in disk_models.items():
                        detected_type = model_info.get('type', 'Unknown')
                        if detected_type != "Unknown":
                            disk_type = detected_type
                            logger.info(f"Fallback: {drive_letter} → {disk_type} (from {model_name})")
                            break

                drive_types[drive_letter] = disk_type
                logger.info(f"✓ Final: {drive_letter} = {disk_type}")

        except Exception as e:
            logger.error(f"Error mapping drives: {e}")

        return drive_types


# ══════════════════════════════════════════════════════════════════
# БЛОК: ВИЯВЛЕННЯ PTERODO
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
# v19: ТОЧНІЙ PTERODO DETECTION ENGINE
# ══════════════════════════════════════════════════════════

# Клас для точного виявлення PTERODO за доменами, файлами, макросами та процесами
class PterodoDetectionEnginev19:
    """v19 - ТІЛЬКИ РЕАЛЬНІ PTERODO МАРКЕРИ, БЕЗ FALSE POSITIVES"""

    # ТОЧНІ PTERODO ДОМЕНИ (НЕ просто .ru!)
    PTERODO_C2_EXACT = [
        "reposant.ru",
        "repsan.ru",
        "inter93.repsan.ru",
        "interbase95.reposant.ru",
        "zapto.org",
        "ddns.net",
        "validgo.ru",
        "splin-upd",
        "splin-upd.site",
        "splin-upd1.site",
        "torrent-supd",
        "torrent-supd.space",
        "torrent-stel.space",
        "bitsadmin.ddns.net",
        "dataoffice.zapto.org",
        "updates-spreadwork.pw",
    ]

    # PTERODO POWERSHELL ФУНКЦІЇ
    PTERODO_FUNCTIONS = [
        "operateResponse",
        "Base64Encode",
        "resolveDNS",
        "BuildRandomString",
        "GetVolumelnformation",
        "UploadFile",
        "InDatabase",
        "GetHash",
        "AddToDatabase",
        "SendFilesFromDirectory",
        "BuildUploadWebRequest",
        "mainLoop",
    ]

    # PTERODO ФАЙЛОВІ МАРКЕРИ
    PTERODO_FILE_PATTERNS = [
        "offspring",
        "ssdt.ini",
        "config.bin",
        "check_upd",
        "torrent_temp",
        "proxy_checker",
    ]

    # PTERODO ХЕШІ
    KNOWN_PTERODO_HASHES = {
        "cef405879cfd148a5fcb905d7c4b0f03ee00e10b2e88038f9cd7f4fc295ae45c",
        "e35d185fdda5332db8018aec3f260dcbd9f2e69a9ce0a28197f3b60b28711af5",
        "ef865dc51fc91096f8cd25661e95bbe0597f49716d31d914fbccf6370a2a56fb",
        "f013b00c70953fc29cf43ca3aee4510d0f8ff48347010cf43966854222f465a0",
    }

    @staticmethod
    def is_pterodo_domain(text):
        """Перевірити чи текст містить Pterodo домен (ТОЧНО)"""
        text_lower = str(text).lower()

        # ТОЧНИЙ пошук Pterodo доменів
        for c2 in PterodoDetectionEnginev19.PTERODO_C2_EXACT:
            if c2.lower() in text_lower:
                return True, c2

        return False, None

    @staticmethod
    def find_pterodo_markers(text):
        """Пошук широкого набору Pterodo-маркерів у тексті"""
        text_lower = str(text).lower()
        markers = []

        for c2 in PterodoDetectionEnginev19.PTERODO_C2_EXACT:
            if c2.lower() in text_lower:
                markers.append(c2)

        for func in PterodoDetectionEnginev19.PTERODO_FUNCTIONS:
            if func.lower() in text_lower and func.lower() not in text_lower.replace(func.lower(), ""):
                markers.append(func)

        for pattern in PterodoDetectionEnginev19.PTERODO_FILE_PATTERNS:
            if pattern in text_lower:
                markers.append(pattern)

        return list(dict.fromkeys(markers))

    @staticmethod
    def scan_registry_pterodo_only():
        """СК��НУВАННЯ РЕЄСТРУ - ТІЛЬКИ PTERODO"""
        findings = []
        if platform.system() != "Windows":
            return findings

        print("\n[v19] 🔍 СКАНУВАННЯ РЕЄСТРУ НА PTERODO...")

        def scan_registry_recursive(hive, hive_name, path=""):
            """Рекурсивно сканує реєстр"""
            count = 0
            try:
                if path:
                    key_path = path
                else:
                    key_path = ""

                with winreg.OpenKey(hive, key_path) as key:
                    # Сканування значень
                    idx = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, idx)
                            value_str = str(value)

                            # ПЕРЕВІРКА НА PTERODO
                            is_pterodo, marker = PterodoDetectionEnginev19.is_pterodo_domain(value_str)
                            if is_pterodo:
                                full_path = f"{key_path}\\{name}" if key_path else name
                                print(f"[ALERT] 🔴 PTERODO РЕЄСТР: {hive_name}\\{full_path} = {marker}")
                                findings.append({
                                    "type": "🔴 PTERODO МАРКЕР У РЕЄСТРУ",
                                    "location": f"{hive_name}\\{full_path}",
                                    "value": value_str[:200],
                                    "marker": marker,
                                    "risk": "КРИТИЧНО",
                                    "score": 100,
                                })
                                count += 1

                            idx += 1
                        except WindowsError:
                            break

                    # Рекурсія
                    idx = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(key, idx)
                            new_path = f"{key_path}\\{subkey_name}" if key_path else subkey_name

                            try:
                                sub_count = scan_registry_recursive(hive, hive_name, new_path)
                                count += sub_count
                            except:
                                pass

                            idx += 1
                        except WindowsError:
                            break

            except Exception as e:
                logger.debug(f"Registry error: {e}")

            return count

        # Сканування HKCU
        print("[v19] HKCU...")
        hkcu_count = scan_registry_recursive(winreg.HKEY_CURRENT_USER, "HKCU")
        print(f"[v19] HKCU: {hkcu_count} результатів")

        # Сканування HKLM
        print("[v19] HKLM...")
        hklm_count = scan_registry_recursive(winreg.HKEY_LOCAL_MACHINE, "HKLM")
        print(f"[v19] HKLM: {hklm_count} результатів")

        print(f"[v19] Всього: {len(findings)}")
        return findings

    @staticmethod
    def scan_files_pterodo():
        """СКАНУВАННЯ ФАЙЛІВ НА PTERODO"""
        findings = []

        print("\n[v19] 🔍 СКАНУВАННЯ ФАЙЛІВ НА PTERODO...")

        scan_dirs = [
            os.path.expanduser("~\\AppData\\Local\\Temp"),
            os.path.expanduser("~\\AppData\\Roaming"),
            os.path.expanduser("~\\AppData\\Local"),
            os.path.expanduser("~\\Documents"),
            os.path.expanduser("~\\Desktop"),
            os.path.expanduser("~\\Downloads"),
            r"C:\\Windows\\Temp",
            r"C:\\ProgramData",
        ]

        suspicious_exts = ('.ps1', '.bat', '.cmd', '.vbs', '.js', '.json', '.txt', '.xml', '.ini', '.conf', '.log')
        file_count = 0

        for scan_dir in scan_dirs:
            if not os.path.exists(scan_dir):
                continue

            print(f"[v19] Папка: {scan_dir}")

            try:
                for root, dirs, files in os.walk(scan_dir):
                    dirs[:] = [d for d in dirs if d not in ['$Recycle.Bin', 'System Volume Information', 'Windows']]

                    for file in files:
                        file_path = os.path.join(root, file)
                        file_count += 1
                        file_lower = file.lower()
                        suspect_name = any(
                            pattern in file_lower for pattern in PterodoDetectionEnginev19.PTERODO_FILE_PATTERNS)
                        suspect_ext = os.path.splitext(file_lower)[1] in suspicious_exts

                        try:
                            with open(file_path, 'rb') as f:
                                content = f.read()

                            try:
                                content_str = content.decode('utf-8', errors='ignore')
                            except:
                                content_str = str(content)

                            is_pterodo, marker = PterodoDetectionEnginev19.is_pterodo_domain(content_str)
                            if is_pterodo:
                                print(f"[ALERT] 🔴 PTERODO ФАЙЛ: {file_path}")
                                findings.append({
                                    "type": "🔴 PTERODO У ФАЙЛІ",
                                    "file_path": file_path,
                                    "marker": marker,
                                    "risk": "КРИТИЧНО",
                                    "score": 100,
                                })
                                continue

                            if suspect_name:
                                print(f"[ALERT] 🔴 PTERODO СУМНІВНИЙ ФАЙЛ: {file_path}")
                                findings.append({
                                    "type": "🔴 Сумнівний PTERODO файл",
                                    "file_path": file_path,
                                    "marker": file_lower,
                                    "risk": "ВИСОКИЙ",
                                    "score": 80,
                                })
                                continue

                            if suspect_ext:
                                markers = PterodoDetectionEnginev19.find_pterodo_markers(content_str)
                                if markers:
                                    print(f"[ALERT] 🔴 PTERODO СКРИПТ: {file_path}")
                                    findings.append({
                                        "type": "🔴 PTERODO скрипт",
                                        "file_path": file_path,
                                        "marker": ", ".join(markers),
                                        "risk": "КРИТИЧНО",
                                        "score": 90,
                                    })
                                    continue

                            try:
                                file_hash = hashlib.sha256(content).hexdigest()
                                if file_hash in PterodoDetectionEnginev19.KNOWN_PTERODO_HASHES:
                                    print(f"[ALERT] 🔴 PTERODO ХЕШ: {file_path}")
                                    findings.append({
                                        "type": "🔴 ИЗВЕСТНИЙ PTERODO ХЕШ",
                                        "file_path": file_path,
                                        "hash": file_hash,
                                        "risk": "КРИТИЧНО",
                                        "score": 100,
                                    })
                            except:
                                pass

                        except Exception as e:
                            logger.debug(f"File error: {e}")

            except Exception as e:
                logger.debug(f"Walk error: {e}")

        print(f"[v19] Файли: {file_count} сканів, {len(findings)} результатів")
        return findings

    @staticmethod
    def scan_documents_pterodo():
        """СКАНУВАННЯ ДОКУМЕНТІВ НА PTERODO"""
        findings = []

        print("\n[v19] 🔍 СКАНУВАННЯ ДОКУМЕНТІВ...")

        doc_dirs = [
            os.path.expanduser("~\\Documents"),
            os.path.expanduser("~\\Desktop"),
            os.path.expanduser("~\\Downloads"),
        ]

        for doc_dir in doc_dirs:
            if not os.path.exists(doc_dir):
                continue

            try:
                for root, dirs, files in os.walk(doc_dir):
                    dirs[:] = [d for d in dirs if d not in ['$Recycle.Bin']]

                    for file in files:
                        if file.lower().endswith(
                                ('.docm', '.xlsm', '.pptm', '.doc', '.xls', '.ppt', '.ps1', '.bat', '.cmd', '.vbs',
                                 '.js')):
                            file_path = os.path.join(root, file)
                            file_lower = file.lower()

                            try:
                                if file_lower.endswith(('.docm', '.xlsm', '.pptm')):
                                    try:
                                        with zipfile.ZipFile(file_path, 'r') as zf:
                                            for name in zf.namelist():
                                                try:
                                                    content = zf.read(name).decode('utf-8', errors='ignore')
                                                except:
                                                    continue
                                                is_pterodo, marker = PterodoDetectionEnginev19.is_pterodo_domain(
                                                    content)
                                                if is_pterodo:
                                                    print(f"[ALERT] 🔴 PTERODO МАКРОС: {file_path}")
                                                    findings.append({
                                                        "type": "🔴 PTERODO МАКРОС",
                                                        "file_path": file_path,
                                                        "marker": marker,
                                                        "risk": "КРИТИЧНО",
                                                        "score": 100,
                                                    })
                                                    break
                                    except:
                                        pass

                                with open(file_path, 'rb') as f:
                                    content = f.read().decode('utf-8', errors='ignore')

                                is_pterodo, marker = PterodoDetectionEnginev19.is_pterodo_domain(content)
                                if is_pterodo:
                                    print(f"[ALERT] 🔴 PTERODO ДОКУМЕНТ: {file_path}")
                                    findings.append({
                                        "type": "🔴 PTERODO ДОКУМЕНТ",
                                        "file_path": file_path,
                                        "marker": marker,
                                        "risk": "КРИТИЧНО",
                                        "score": 100,
                                    })
                                    continue

                                markers = PterodoDetectionEnginev19.find_pterodo_markers(content)
                                if markers:
                                    print(f"[ALERT] 🔴 PTERODO ДОКУМЕНТ: {file_path}")
                                    findings.append({
                                        "type": "🔴 PTERODO ДОКУМЕНТ",
                                        "file_path": file_path,
                                        "marker": ", ".join(markers),
                                        "risk": "ВИСОКИЙ",
                                        "score": 85,
                                    })
                            except Exception as e:
                                logger.debug(f"Doc error: {e}")

            except Exception as e:
                logger.debug(f"Doc walk error: {e}")

        print(f"[v19] Документи: {len(findings)} результатів")
        return findings

    @staticmethod
    def scan_processes_pterodo():
        """СКАНУВАННЯ ПРОЦЕСІВ"""
        findings = []

        print("\n[v19] 🔍 СКАНУВАННЯ ПРОЦЕСІВ...")

        try:
            for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
                try:
                    proc_name = proc.name().lower()
                    proc_exe = proc.exe().lower() if hasattr(proc, 'exe') else ""
                    cmdline = ' '.join(proc.cmdline()).lower() if proc.cmdline() else ""

                    suspicious_names = [
                        "offspring", "updater_d", "svn_client", "proxy_checker",
                        "ipt_checker", "torrent_client", "check_upd", "torrent_temp",
                    ]

                    found = False
                    for pterodo_proc in suspicious_names:
                        if pterodo_proc in proc_name or pterodo_proc in proc_exe or pterodo_proc in cmdline:
                            found = True
                            break

                    if not found:
                        for c2 in PterodoDetectionEnginev19.PTERODO_C2_EXACT:
                            if c2.lower() in cmdline:
                                found = True
                                break

                    if found and "system32" not in proc_exe:
                        print(f"[ALERT] 🔴 PTERODO ПРОЦЕС: {proc_name} (PID {proc.pid})")
                        findings.append({
                            "type": "🔴 PTERODO ПРОЦЕС",
                            "process": proc_name,
                            "pid": proc.pid,
                            "cmdline": cmdline[:200],
                            "exec_path": proc_exe,
                            "risk": "КРИТИЧНО",
                            "score": 100,
                        })
                except:
                    pass
        except Exception as e:
            logger.debug(f"Process error: {e}")

        print(f"[v19] Процеси: {len(findings)} результатів")
        return findings


# Клас для розширеного PTERODO-сканування файлів, процесів, реєстру та мережі
class PterodoDetectionEnginev14Extended:
    """Розширене Pterodo детектування v14 БЕЗ ПОМИЛКОВИХ ПОЗИТИВІВ"""

    PTERODO_KNOWN_C2 = [
        "splin-upd.site", "splin-upd1.site", "torrent-supd.space", "torrent-stel.space",
        "dataoffice.zapto.org", "bitsadmin.ddns.net", "updates-spreadwork.pw",
    ]

    PTERODO_PROCESS_SIGNATURES = [
        r"offspring", r"updater_d", r"svn_client", r"proxy_checker",
    ]

    PTERODO_FILE_PATTERNS = [
        r"offspring.*", r"~\.drv", r"ssdt\.ini", r"config\.bin",
    ]

    @staticmethod
    def scan_pterodo_processes_extended():
        """Розширене сканування процесів БЕЗ FALSE POSITIVES"""
        findings = []
        try:
            for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
                try:
                    proc_name = proc.name().lower()
                    proc_exe = proc.exe() if hasattr(proc, 'exe') else ""
                    cmdline = ' '.join(proc.cmdline()) if proc.cmdline() else ""

                    pterodo_real_names = [
                        'offspring', 'updater_d', 'svn_client', 'proxy_checker',
                        'ipt_checker', 'torrent_client', 'offspring.exe'
                    ]

                    for pterodo_name in pterodo_real_names:
                        if pterodo_name in proc_name and proc_exe and "system32" not in proc_exe.lower():
                            if os.path.exists(proc_exe):
                                findings.append({
                                    "type": "Pterodo реальний процес",
                                    "process": proc_name,
                                    "pid": proc.pid,
                                    "full_path": proc_exe,
                                    "cmdline": cmdline[:150],
                                    "risk": "КРИТИЧНО - Pterodo процес",
                                    "score": 95,
                                })
                                logger.info(f"РЕАЛЬНИЙ PTERODO: {proc_name} ({proc_exe})")
                except:
                    pass
        except Exception as e:
            logger.error(f"Error scanning pterodo processes: {e}")
        return findings

    @staticmethod
    def scan_pterodo_files_advanced():
        """Розширене сканування файлів БЕЗ FALSE POSITIVES"""
        findings = []
        pterodo_paths = [
            os.path.expanduser("~\\AppData\\Local\\Temp"),
            os.path.expanduser("~\\AppData\\Roaming"),
        ]

        try:
            for scan_path in pterodo_paths:
                if not os.path.exists(scan_path):
                    continue

                try:
                    for root, dirs, files in os.walk(scan_path, topdown=True):
                        dirs[:] = [d for d in dirs if d not in ['AppData', '$Recycle.Bin']]

                        for file in files:
                            file_lower = file.lower()

                            pterodo_files = [
                                'offspring', 'ssdt.ini', 'config.bin', 'offsprings',
                                'check_upd', 'torrent_temp'
                            ]

                            for pterodo_file in pterodo_files:
                                if pterodo_file in file_lower:
                                    file_path = os.path.join(root, file)
                                    try:
                                        file_stat = os.stat(file_path)
                                        findings.append({
                                            "type": "Pterodo файл",
                                            "filename": file,
                                            "full_path": file_path,
                                            "size_kb": round(file_stat.st_size / 1024, 2),
                                            "created": dt.fromtimestamp(file_stat.st_ctime).isoformat(),
                                            "modified": dt.fromtimestamp(file_stat.st_mtime).isoformat(),
                                            "risk": "КРИТИЧНО - Pterodo файл",
                                            "score": 85,
                                        })
                                        logger.info(f"PTERODO ФАЙЛ: {file_path}")
                                    except:
                                        pass
                except Exception as e:
                    logger.debug(f"Error scanning directory {scan_path}: {e}")
        except Exception as e:
            logger.error(f"Error in scan_pterodo_files_advanced: {e}")

        return findings

    @staticmethod
    def scan_pterodo_registry_deep():
        """ГЛИБОКЕ сканування реєстру БЕЗ FALSE POSITIVES"""
        findings = []
        if platform.system() != "Windows":
            return []

        try:
            registry_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunServices"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunServicesOnce"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
            ]

            for hive, path in registry_paths:
                try:
                    with winreg.OpenKey(hive, path) as key:
                        idx = 0
                        while True:
                            try:
                                name, value, _ = winreg.EnumValue(key, idx)
                                value_str = str(value).lower()

                                pterodo_c2_domains = [
                                    'splin-upd', 'torrent-supd', 'dataoffice.zapto',
                                    'bitsadmin.ddns', 'offspring', 'svn_client',
                                    'proxy_checker', 'check_upd', 'torrent_temp', 'updater_d'
                                ]

                                for c2_domain in pterodo_c2_domains:
                                    if c2_domain in value_str:
                                        findings.append({
                                            "type": "Pterodo C2 у реєстрі",
                                            "registry_hive": str(hive),
                                            "registry_path": f"{path}\\{name}",
                                            "value_name": name,
                                            "value_content": str(value)[:200],
                                            "full_value": str(value),
                                            "risk": "КРИТИЧНО - Pterodo C2",
                                            "score": 90,
                                        })
                                        logger.info(f"PTERODO C2: {path}\\{name}")
                                        break

                                idx += 1
                            except WindowsError:
                                break
                except Exception as e:
                    logger.debug(f"Error scanning registry path {path}: {e}")
        except Exception as e:
            logger.error(f"Error in scan_pterodo_registry_deep: {e}")

        return findings

    @staticmethod
    def scan_pterodo_network_extended():
        """Розширене сканування мережі БЕЗ FALSE POSITIVES"""
        findings = []
        try:
            connections = psutil.net_connections(kind='inet')

            for conn in connections:
                try:
                    if not conn.raddr:
                        continue

                    remote_ip = conn.raddr[0]
                    remote_port = conn.raddr[1]
                    local_ip = conn.laddr[0] if conn.laddr else "Unknown"

                    try:
                        proc = psutil.Process(conn.pid)
                        proc_name = proc.name()
                        proc_exe = proc.exe() if hasattr(proc, 'exe') else ""
                    except:
                        proc_name = f"PID {conn.pid}"
                        proc_exe = "Unknown"

                    for c2 in PterodoDetectionEnginev14Extended.PTERODO_KNOWN_C2:
                        try:
                            hostname = socket.gethostbyaddr(remote_ip)[0]
                            if c2.lower() in hostname.lower():
                                findings.append({
                                    "type": "Pterodo C2 з'єднання",
                                    "process": proc_name,
                                    "process_path": proc_exe,
                                    "pid": conn.pid,
                                    "local_ip": local_ip,
                                    "remote_ip": remote_ip,
                                    "remote_port": remote_port,
                                    "hostname": hostname,
                                    "c2_server": c2,
                                    "connection_state": conn.status,
                                    "risk": "КРИТИЧНО - Pterodo C2",
                                    "score": 100
                                })
                                logger.info(f"PTERODO C2: {proc_name} → {remote_ip}:{remote_port}")
                        except:
                            pass

                except Exception as e:
                    logger.debug(f"Error analyzing connection: {e}")

        except Exception as e:
            logger.error(f"Error in scan_pterodo_network_extended: {e}")

        return findings


# ══════════════════════════════════════════════════════════════════
# ОРИГІНАЛЬНА PTERODO DETECTION ENGINE v13 (ЗБЕРЕЖЕНА)
# ══════════════════════════════════════════════════════════════════

# Клас з класичними PTERODO-маркерами v13: документи, процеси, мережа, реєстр
class PterodoDetectionEnginev13:
    """
    Оригінальні ознаки Pterodo:
    1. Офісні документи з макросами + приховані файли
    2. Виконувані дублікати (картинки/документи → .exe)
    3. C2 з'єднання на DNS порт 53 та підозрілі хости
    4. Російські домени в реєстрі
    5. Масування під svchost.exe з аномальним сеттингом
    """

    RUSSIAN_C2_INDICATORS = [
        r"\.ru\b", r"\.рф\b", r"[а-яё]+",
        "185.220.", "91.243.", "195.154.", "203.0.113.",
    ]

    SUSPICIOUS_PORTS = [53, 80, 443, 8080, 4444, 5555, 6666, 9999]
    OFFICE_MACRO_EXTENSIONS = [".docm", ".xlsm", ".pptm", ".docb", ".xlsb"]
    EXECUTABLE_DOUBLES = [".exe", ".com", ".scr", ".bat", ".cmd", ".ps1"]

    @staticmethod
    def scan_office_documents_with_macros():
        """Сканування офісних документів з макросами"""
        findings = []
        try:
            scan_paths = [
                os.path.expanduser("~\\Documents"),
                os.path.expanduser("~\\Desktop"),
                os.path.expanduser("~\\Downloads"),
                "C:\\Users",
            ]
            for root_path in scan_paths:
                if not os.path.exists(root_path):
                    continue
                try:
                    for root, dirs, files in os.walk(root_path):
                        dirs[:] = [d for d in dirs if d not in ['AppData', 'Application Data', '$Recycle.Bin']]
                        for file in files:
                            file_lower = file.lower()
                            if any(file_lower.endswith(ext) for ext in
                                   PterodoDetectionEnginev13.OFFICE_MACRO_EXTENSIONS):
                                file_path = os.path.join(root, file)
                                try:
                                    file_stat = os.stat(file_path)
                                    file_size = file_stat.st_size
                                    if file_size > 50 * 1024 * 1024:
                                        findings.append({
                                            "type": "Офісний документ з макросами (АНОМАЛЬНИЙ РОЗМІР)",
                                            "full_path": file_path,
                                            "filename": file,
                                            "extension": os.path.splitext(file)[1],
                                            "size_mb": round(file_size / (1024 * 1024), 2),
                                            "created": dt.fromtimestamp(file_stat.st_ctime).isoformat(),
                                            "modified": dt.fromtimestamp(file_stat.st_mtime).isoformat(),
                                            "risk": "КРИТИЧНО - Аномальний розмір (можливі макроси)",
                                            "score": 70
                                        })
                                    else:
                                        findings.append({
                                            "type": "Офісний документ з макросами",
                                            "full_path": file_path,
                                            "filename": file,
                                            "extension": os.path.splitext(file)[1],
                                            "size_mb": round(file_size / (1024 * 1024), 2),
                                            "created": dt.fromtimestamp(file_stat.st_ctime).isoformat(),
                                            "modified": dt.fromtimestamp(file_stat.st_mtime).isoformat(),
                                            "risk": "ВИСОКА - Потенційно заражений макросами",
                                            "score": 50
                                        })
                                    logger.info(f"OFFICE MACRO: {file_path}")
                                except Exception as e:
                                    logger.debug(f"Error scanning {file_path}: {e}")
                except Exception as e:
                    logger.debug(f"Error walking {root_path}: {e}")
        except Exception as e:
            logger.error(f"Error in scan_office_documents_with_macros: {e}")
        return findings

    @staticmethod
    def scan_hidden_files_on_removable_media():
        """Сканування приховани файлів на USB флешках"""
        findings = []
        try:
            for partition in psutil.disk_partitions():
                if 'removable' not in partition.opts.lower():
                    continue
                drive = partition.device
                try:
                    result = subprocess.run(
                        ["attrib", "/D", "/S", drive + "\\"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        creationflags=0x08000000
                    )
                    if result.stdout:
                        for line in result.stdout.split('\n'):
                            if 'H' in line or line.startswith(' A  H'):
                                parts = line.split()
                                if len(parts) > 1:
                                    file_path = ' '.join(parts[1:])
                                    if any(file_path.lower().endswith(ext) for ext in
                                           ['.jpg', '.png', '.doc', '.xls', '.txt', '.pdf']):
                                        findings.append({
                                            "type": "Приховатий оригінальний файл на USB (PTERODO МАРКЕР)",
                                            "full_path": file_path,
                                            "drive": drive,
                                            "risk": "КРИТИЧНО - Pterodo маркер прихованого файлу",
                                            "score": 95,
                                            "details": "Оригінальний файл прихований - маркер Pterodo"
                                        })
                                        logger.info(f"HIDDEN USB FILE: {file_path} on {drive}")
                except Exception as e:
                    logger.debug(f"Error scanning {drive}: {e}")
        except Exception as e:
            logger.error(f"Error in scan_hidden_files_on_removable_media: {e}")
        return findings

    @staticmethod
    def scan_executable_doubles():
        """Пошук виконуваних дублікатів (картинок/документів як .exe)"""
        findings = []
        try:
            scan_paths = [
                os.path.expanduser("~\\Documents"),
                os.path.expanduser("~\\Desktop"),
                os.path.expanduser("~\\Downloads"),
                "C:\\Users",
                "C:\\Temp",
                "C:\\Program Files",
            ]
            for root_path in scan_paths:
                if not os.path.exists(root_path):
                    continue
                try:
                    exe_files = {}
                    for root, dirs, files in os.walk(root_path):
                        dirs[:] = [d for d in dirs if d not in ['AppData', 'Application Data', '$Recycle.Bin']]
                        for file in files:
                            if file.lower().endswith('.exe'):
                                file_lower = file.lower()
                                base_name = file_lower[:-4]
                                if base_name not in exe_files:
                                    exe_files[base_name] = []
                                exe_files[base_name].append(os.path.join(root, file))

                    for root, dirs, files in os.walk(root_path):
                        dirs[:] = [d for d in dirs if d not in ['AppData', 'Application Data', '$Recycle.Bin']]
                        for file in files:
                            file_lower = file.lower()
                            if any(file_lower.endswith(ext) for ext in
                                   ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.doc', '.docx', '.xls', '.xlsx', '.txt',
                                    '.pdf']):
                                base_name = file_lower
                                if base_name in exe_files:
                                    for exe_path in exe_files[base_name]:
                                        orig_path = os.path.join(root, file)
                                        findings.append({
                                            "type": "Виконуваний дублікат (PTERODO МАРКЕР)",
                                            "original_file": orig_path,
                                            "original_extension": os.path.splitext(file)[1],
                                            "executable_copy": exe_path,
                                            "executable_size": os.path.getsize(exe_path) if os.path.exists(
                                                exe_path) else 0,
                                            "original_size": os.path.getsize(orig_path) if os.path.exists(
                                                orig_path) else 0,
                                            "risk": "КРИТИЧНО - Pterodo поведінка (дублікат як .exe)",
                                            "score": 100,
                                            "details": "Оригінальний файл замасковано як .exe - типовий маркер Pterodo"
                                        })
                                        logger.info(f"EXECUTABLE DOUBLE: {orig_path} → {exe_path}")
                except Exception as e:
                    logger.debug(f"Error scanning {root_path}: {e}")
        except Exception as e:
            logger.error(f"Error in scan_executable_doubles: {e}")
        return findings

    @staticmethod
    def analyze_network_connections():
        """Аналіз мережевих з'єднань на предмет C2"""
        findings = []
        try:
            connections = psutil.net_connections(kind='inet')
            for conn in connections:
                try:
                    if not conn.raddr:
                        continue
                    remote_ip = conn.raddr[0]
                    remote_port = conn.raddr[1]
                    local_ip = conn.laddr[0] if conn.laddr else "Unknown"
                    try:
                        proc = psutil.Process(conn.pid)
                        proc_name = proc.name()
                        proc_exe = proc.exe() if hasattr(proc, 'exe') else ""
                    except:
                        proc_name = f"PID {conn.pid}"
                        proc_exe = ""

                    if proc_name.lower() == "svchost.exe" and remote_port == 53:
                        try:
                            hostname = socket.gethostbyaddr(remote_ip)[0]
                        except:
                            hostname = "Unknown"
                        is_russian = any(re.search(pattern, hostname.lower()) for pattern in
                                         PterodoDetectionEnginev13.RUSSIAN_C2_INDICATORS)
                        is_suspicious_ip = any(remote_ip.startswith(prefix) for prefix in
                                               ["185.220.", "91.243.", "195.154.", "203.0.113."])

                        if is_russian or is_suspicious_ip:
                            findings.append({
                                "type": "C2 з'єднання DNS (PORT 53)",
                                "process": proc_name,
                                "process_path": proc_exe,
                                "pid": conn.pid,
                                "local_ip": local_ip,
                                "remote_ip": remote_ip,
                                "remote_port": remote_port,
                                "hostname": hostname,
                                "connection_state": conn.status,
                                "risk": "КРИТИЧНО - Pterodo C2 маркер (DNS 53)",
                                "score": 100,
                                "details": f"svchost DNS до {hostname} - критичний маркер Pterodo"
                            })
                            logger.info(f"C2 DNS: {proc_name} → {remote_ip}:53 ({hostname})")

                    elif remote_port in [443, 80] and conn.status == 'ESTABLISHED':
                        if proc_name.lower() in ["svchost.exe", "lsass.exe", "csrss.exe"]:
                            try:
                                hostname = socket.gethostbyaddr(remote_ip)[0]
                                is_russian = any(re.search(pattern, hostname.lower()) for pattern in
                                                 PterodoDetectionEnginev13.RUSSIAN_C2_INDICATORS)
                                if is_russian:
                                    findings.append({
                                        "type": "Аномальне з'єднання системного процесу",
                                        "process": proc_name,
                                        "process_path": proc_exe,
                                        "pid": conn.pid,
                                        "local_ip": local_ip,
                                        "remote_ip": remote_ip,
                                        "remote_port": remote_port,
                                        "hostname": hostname,
                                        "connection_state": conn.status,
                                        "risk": "ВИСОКА - Pterodo маскування (системний процес)",
                                        "score": 85,
                                        "details": f"Системний процес з'єднаний до російського хоста - маркер Pterodo"
                                    })
                                    logger.info(f"ANOMALOUS NET: {proc_name} → {remote_ip}:{remote_port} ({hostname})")
                            except:
                                pass
                except Exception as e:
                    logger.debug(f"Error analyzing connection: {e}")
        except Exception as e:
            logger.error(f"Error in analyze_network_connections: {e}")
        return findings

    @staticmethod
    def scan_registry_for_c2_domains():
        """Сканування реєстру на російські домени і C2 посилання"""
        findings = []
        if platform.system() != "Windows":
            return []
        try:
            registry_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\16.0\Common\Internet"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\16.0\Common\Identity"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\14.0\Common\Internet"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\14.0\Common\Identity"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\12.0\Common\Internet"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\12.0\Common\Identity"),
                (winreg.HKEY_LOCAL_MACHINE, r"System\CurrentControlSet\Services"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2"),
            ]
            for hive, path in registry_paths:
                try:
                    with winreg.OpenKey(hive, path) as key:
                        idx = 0
                        while True:
                            try:
                                name, value, _ = winreg.EnumValue(key, idx)
                                value_str = str(value).lower()
                                for pattern in PterodoDetectionEnginev13.RUSSIAN_C2_INDICATORS:
                                    if re.search(pattern, value_str):
                                        findings.append({
                                            "type": "C2 домен у реєстрі",
                                            "registry_hive": str(hive),
                                            "registry_path": f"{path}\\{name}",
                                            "value_name": name,
                                            "value_content": str(value)[:300],
                                            "full_value": str(value),
                                            "pattern_matched": pattern,
                                            "risk": "КРИТИЧНО - Pterodo конфіг (C2 домен)",
                                            "score": 90,
                                            "details": f"Російський домен/C2 у реєстрі: {pattern}"
                                        })
                                        logger.info(f"C2 REGISTRY: {path}\\{name} = {value[:100]}")
                                        break

                                if ".ru/" in value_str or ".рф/" in value_str or "http" in value_str:
                                    if any(susp in value_str for susp in ["cmd", "script", "vbs", "bat"]):
                                        findings.append({
                                            "type": "Підозрілий скрипт у реєстрі",
                                            "registry_hive": str(hive),
                                            "registry_path": f"{path}\\{name}",
                                            "value_name": name,
                                            "value_content": str(value)[:300],
                                            "full_value": str(value),
                                            "risk": "КРИТИЧНО - Pterodo payload (скрипт)",
                                            "score": 95,
                                            "details": "Скрипт з російським доменом у реєстрі"
                                        })
                                        logger.info(f"SCRIPT REGISTRY: {path}\\{name}")

                                idx += 1
                            except WindowsError:
                                break
                except Exception as e:
                    logger.debug(f"Error scanning registry {path}: {e}")
        except Exception as e:
            logger.error(f"Error in scan_registry_for_c2_domains: {e}")
        return findings


# ══════════════════════════════════════════════════════════════════
# ОРИГІНАЛЬНІ КЛАСИ (ЗБЕРЕЖЕНІ ПОВНІСТЮ)
# ══════════════════════════════════════════════════════════════════

class PterodoScoringSystemV12_1:
    """Збережена з v12.1 - БЕЗ ЗМІН"""

    def __init__(self):
        self.critical_threshold = 90
        self.high_threshold = 70
        self.medium_threshold = 50
        self.system_processes = [
            "svchost.exe", "explorer.exe", "runtime.exe", "service.exe",
            "update.exe", "securityhealth.exe", "lsass.exe", "csrss.exe",
            "smss.exe", "services.exe", "spoolsv.exe", "lsm.exe", "winlogon.exe",
        ]
        self.trusted_vendors = [
            "Microsoft", "Google", "Adobe", "NVIDIA", "Intel", "Apple",
            "Mozilla", "Canonical", "Valve", "GitHub", "JetBrains", "Python", "Open Source",
        ]
        self.legitimate_dev_tools = {
            "python.exe": "Python interpreter", "python3.exe": "Python 3",
            "python3.9.exe": "Python 3.9", "python3.10.exe": "Python 3.10",
            "python3.11.exe": "Python 3.11", "python3.12.exe": "Python 3.12",
            "python.dll": "Python library", "node.exe": "Node.js", "npm.exe": "npm",
            "npx.exe": "npx", "yarn.exe": "Yarn", "pnpm.exe": "pnpm",
            "ruby.exe": "Ruby", "gem.exe": "Ruby Gem", "bundler.exe": "Bundler",
            "java.exe": "Java", "javaw.exe": "Java windowed", "javac.exe": "Java compiler",
            "go.exe": "Go", "rustc.exe": "Rust compiler", "cargo.exe": "Cargo",
            "full-line-inference.exe": "AI inference engine", "ollama.exe": "Ollama",
            "ollama": "Ollama service", "localai.exe": "LocalAI", "docker.exe": "Docker",
            "dockerd.exe": "Docker daemon", "git.exe": "Git", "git-bash.exe": "Git Bash",
            "cmake.exe": "CMake", "make.exe": "Make", "gradle.exe": "Gradle",
            "maven.exe": "Maven", "msbuild.exe": "MSBuild", "vscode.exe": "Visual Studio Code",
            "idea64.exe": "IntelliJ IDEA", "pycharm.exe": "PyCharm", "clion.exe": "CLion",
            "npm": "NPM", "python": "Python", "php.exe": "PHP",
        }
        self.legitimate_installers = ["setup.exe", "installer.exe", "install.exe", "uninstall.exe"]

    def is_whitelisted_dev_tool(self, filename):
        filename_lower = filename.lower()
        if filename_lower in self.legitimate_dev_tools:
            return True, f"Легітимний інструмент розробника: {self.legitimate_dev_tools[filename_lower]}"
        for legit_name, description in self.legitimate_dev_tools.items():
            if legit_name.lower() in filename_lower or filename_lower in legit_name.lower():
                if any(keyword in filename_lower for keyword in
                       ["python", "node", "ruby", "java", "go", "rust", "git", "docker", "inference"]):
                    return True, f"Легітимно: {description}"
        return False, ""

    def calculate_path_score(self, filepath, filename):
        score = 0
        filepath_lower = filepath.lower()
        is_legit, legit_reason = self.is_whitelisted_dev_tool(filename)
        if is_legit:
            return 0, legit_reason
        if any(x in filepath_lower for x in ["\\appdata\\", "\\temp\\", "%temp%", "%appdata%"]):
            score += 30
            return score, "Виконуваний файл у каталозі AppData або Temp"
        return 0, ""

    def calculate_signature_score(self, is_signed, publisher="", filename=""):
        score = 0
        reason = ""
        is_legit, _ = self.is_whitelisted_dev_tool(filename)
        if is_legit:
            return 0, "Інструмент розробника"
        if not is_signed:
            score += 25
            reason = "Файл непідписаний"
        elif publisher and not any(vendor.lower() in publisher.lower() for vendor in self.trusted_vendors):
            score += 15
            reason = f"Невідомий видавець: {publisher}"
        return score, reason

    def calculate_persistence_score(self, has_autorun, has_scheduled_task):
        score = 0
        reason = ""
        if has_autorun:
            score += 20
            reason = "Виявлено запис автозапуску"
        elif has_scheduled_task:
            score += 20
            reason = "Виявлено заплановане завдання"
        return score, reason

    def calculate_entropy_score(self, entropy_value):
        score = 0
        reason = ""
        if entropy_value > 7.2:
            score += 15
            reason = f"Висока ентропія {entropy_value:.2f} - ймовірно упакована"
        return score, reason

    def calculate_masquerading_score(self, filename, filepath):
        score = 0
        reason = ""
        filename_lower = filename.lower()
        filepath_lower = filepath.lower()
        for sys_proc in self.system_processes:
            if filename_lower == sys_proc.lower():
                if "system32" not in filepath_lower:
                    score += 15
                    reason = f"Процес маскування під {sys_proc} з несистемного каталогу"
                break
        return score, reason

    def calculate_parent_process_score(self, parent_name, parent_path):
        score = 0
        reason = ""
        if parent_path and any(x in parent_path.lower() for x in ["\\appdata\\", "\\temp\\"]):
            score += 10
            reason = f"Підозрілий батьківський процес від {parent_path}"
        return score, reason

    def calculate_network_score(self, has_suspicious_connections):
        score = 0
        reason = ""
        if has_suspicious_connections:
            score += 20
            reason = "Підозрілі мережеві з'єднання (можливе виявлення маяків C2)"
        return score, reason

    def apply_correlation_rule(self, scores_breakdown):
        has_appdata = scores_breakdown.get("path_reason", "").find("AppData") != -1 or \
                      scores_breakdown.get("path_reason", "").find("Temp") != -1
        has_unsigned = scores_breakdown.get("signature_reason", "").find("непідписаний") != -1
        has_persistence = scores_breakdown.get("persistence_reason", "") != ""
        if "Інструмент розробника" in str(scores_breakdown.get("signature_reason", "")):
            return 0, ""
        if has_appdata and has_unsigned and has_persistence:
            return 90, "Кореляція: AppData + Непідписаний + Автозапуск = КРИТИЧНИЙ"
        return 0, ""

    def is_false_positive_installer(self, filename, has_persistence):
        filename_lower = filename.lower()
        if filename_lower in self.legitimate_installers:
            if not has_persistence:
                return True, "Легальний інсталятор без наполегливості"
        return False, ""

    def calculate_total_score(self, indicators_dict):
        total_score = 0
        all_indicators = []
        scores_breakdown = {}
        filename = indicators_dict.get("filename", "")
        filepath = indicators_dict.get("filepath", "")
        is_legit, legit_reason = self.is_whitelisted_dev_tool(filename)
        if is_legit:
            return None, None, [legit_reason]

        path_score, path_reason = self.calculate_path_score(filepath, filename)
        total_score += path_score
        if path_reason:
            all_indicators.append(f"[+30] {path_reason}" if path_score > 0 else path_reason)
            scores_breakdown["path_reason"] = path_reason

        sig_score, sig_reason = self.calculate_signature_score(
            indicators_dict.get("is_signed", False),
            indicators_dict.get("publisher", ""),
            filename
        )
        total_score += sig_score
        if sig_reason:
            all_indicators.append(f"[+25] {sig_reason}" if sig_score > 0 else sig_reason)
            scores_breakdown["signature_reason"] = sig_reason

        persist_score, persist_reason = self.calculate_persistence_score(
            indicators_dict.get("has_autorun", False),
            indicators_dict.get("has_scheduled_task", False)
        )
        total_score += persist_score
        if persist_reason:
            all_indicators.append(f"[+20] {persist_reason}")
            scores_breakdown["persistence_reason"] = persist_reason

        entropy_score, entropy_reason = self.calculate_entropy_score(
            indicators_dict.get("entropy", 0)
        )
        total_score += entropy_score
        if entropy_reason:
            all_indicators.append(f"[+15] {entropy_reason}")

        masq_score, masq_reason = self.calculate_masquerading_score(filename, filepath)
        total_score += masq_score
        if masq_reason:
            all_indicators.append(f"[+15] {masq_reason}")

        parent_score, parent_reason = self.calculate_parent_process_score(
            indicators_dict.get("parent_name", ""),
            indicators_dict.get("parent_path", "")
        )
        total_score += parent_score
        if parent_reason:
            all_indicators.append(f"[+10] {parent_reason}")

        net_score, net_reason = self.calculate_network_score(
            indicators_dict.get("has_suspicious_connections", False)
        )
        total_score += net_score
        if net_reason:
            all_indicators.append(f"[+20] {net_reason}")

        corr_bonus, corr_reason = self.apply_correlation_rule(scores_breakdown)
        if corr_bonus > 0:
            total_score = max(total_score, corr_bonus)
            all_indicators.insert(0, f"[КОРЕЛЯЦІЯ] {corr_reason}")

        is_fp, fp_reason = self.is_false_positive_installer(
            filename,
            indicators_dict.get("has_autorun", False) or indicators_dict.get("has_scheduled_task", False)
        )
        if is_fp:
            return None, None, [fp_reason]

        if total_score >= self.critical_threshold:
            severity = "КРИТИЧНИЙ"
        elif total_score >= self.high_threshold:
            severity = "ВИСОКИЙ"
        elif total_score >= self.medium_threshold:
            severity = "СЕРЕДНІЙ"
        else:
            return None, None, all_indicators

        return total_score, severity, all_indicators


class EntropyDetector:
    """Детектор ентропії файлу"""

    @staticmethod
    def calculate_entropy(data):
        if not data:
            return 0.0
        entropy = 0.0
        for i in range(256):
            p_x = float(data.count(bytes([i]))) / len(data)
            if p_x > 0:
                entropy += - p_x * (math.log(p_x) / math.log(2))
        return entropy

    def check_file_entropy(self, filepath):
        try:
            with open(filepath, 'rb') as f:
                data = f.read(100000)
            entropy = self.calculate_entropy(data)
            is_packed = False
            analysis = ""
            if entropy < 3.0:
                analysis = "Низька ентропія — звичайний код"
            elif entropy < 5.0:
                analysis = "Середня ентропія — норма"
            elif entropy < 7.0:
                analysis = "Висока ентропія — можливо стиснуто/зашифровано"
                is_packed = True
            else:
                analysis = "Дуже висока ентропія — ймовірно упаковане шкідливе ПЗ"
                is_packed = True
            return {
                "entropy_score": round(entropy, 2),
                "is_packed": is_packed,
                "analysis": analysis
            }
        except Exception as e:
            logger.error(f"Помилка розрахунку ентропії: {e}")
            return {
                "entropy_score": 0,
                "is_packed": False,
                "analysis": f"Помилка: {e}"
            }


class DigitalSignatureChecker:
    """Перевіра цифрових підписів файлів"""

    def __init__(self):
        self.trusted_publishers = [
            "Microsoft", "Adobe", "Google", "Apple",
            "Intel", "NVIDIA", "Lenovo", "Dell", "HP",
            "Canonical", "Mozilla", "GitHub", "JetBrains"
        ]

    def is_pe_file(self, filepath):
        try:
            with open(filepath, 'rb') as f:
                header = f.read(2)
                return header == b'MZ'
        except:
            return False

    def check_file_signature_via_registry(self, filepath):
        result = {
            "is_signed": False,
            "publisher": "Невідомо",
            "is_trusted": False,
            "thumbprint": "N/A",
            "signed": False,
        }
        if not platform.system() == "Windows":
            return result
        if not self.is_pe_file(filepath):
            return result
        try:
            cert_result = subprocess.run(
                ["certutil", "-verify", "-urlfetch", filepath],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=0x08000000
            )
            if cert_result.returncode == 0 or "verified" in cert_result.stdout.lower():
                result["is_signed"] = True
                result["signed"] = True
                for line in cert_result.stdout.split('\n'):
                    if "Subject:" in line or "Issuer:" in line:
                        pub = line.split(":", 1)[1].strip() if ":" in line else ""
                        if pub:
                            result["publisher"] = pub[:100]
                            for trusted in self.trusted_publishers:
                                if trusted.lower() in pub.lower():
                                    result["is_trusted"] = True
                                    break
                return result
        except Exception as e:
            logger.debug(f"Перевірка не вдалася: {e}")
        return result


class ProcessAnalyzerV12_1:
    """Аналізатор процесів"""

    def __init__(self):
        self.scorer = PterodoScoringSystemV12_1()
        self.sig_checker = DigitalSignatureChecker()
        self.entropy_detector = EntropyDetector()

    def analyze_process(self, process):
        try:
            indicators_dict = {
                "filename": process.name() if hasattr(process, 'name') else "",
                "filepath": process.exe() if hasattr(process, 'exe') else "",
                "is_signed": False,
                "publisher": "",
                "has_autorun": False,
                "has_scheduled_task": False,
                "entropy": 0,
                "parent_name": "",
                "parent_path": "",
                "has_suspicious_connections": False,
            }

            if hasattr(process, 'exe') and process.exe():
                filepath = process.exe()
                sig_info = self.sig_checker.check_file_signature_via_registry(filepath)
                indicators_dict["is_signed"] = sig_info.get("signed", False)
                indicators_dict["publisher"] = sig_info.get("publisher", "")
                entropy_info = self.entropy_detector.check_file_entropy(filepath)
                indicators_dict["entropy"] = entropy_info.get("entropy_score", 0)

            try:
                if hasattr(process, 'ppid'):
                    ppid = process.ppid()
                    if ppid:
                        parent = psutil.Process(ppid)
                        indicators_dict["parent_name"] = parent.name() if hasattr(parent, 'name') else ""
                        indicators_dict["parent_path"] = parent.exe() if hasattr(parent, 'exe') else ""
            except:
                pass

            total_score, severity, indicators = self.scorer.calculate_total_score(indicators_dict)
            if total_score is None:
                return None, None, indicators
            return total_score, severity, indicators

        except Exception as e:
            logger.error(f"Помилка аналізу процесу: {e}")
            return None, None, [f"Помилка: {e}"]

    def scan_all_processes(self):
        findings = []
        try:
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    score, severity, indicators = self.analyze_process(proc)
                    if score is not None:
                        finding = {
                            "type": "Процес",
                            "name": proc.name(),
                            "pid": proc.pid,
                            "exe": proc.exe() if hasattr(proc, 'exe') else "",
                            "score": score,
                            "severity": severity,
                            "indicators": indicators,
                        }
                        findings.append(finding)
                except:
                    pass
        except Exception as e:
            logger.error(f"Помилка сканування процесів: {e}")
        return findings


class PersistenceScannerV12_1:
    """Скан механізмів persistence"""

    def __init__(self):
        self.scorer = PterodoScoringSystemV12_1()

    def scan_registry_autorun(self):
        findings = []
        if platform.system() != "Windows":
            return []
        try:
            autorun_locations = [
                (winreg.HKEY_CURRENT_USER, "Software\\Microsoft\\Windows\\CurrentVersion\\Run", "HKCU\\Run"),
                (winreg.HKEY_LOCAL_MACHINE, "Software\\Microsoft\\Windows\\CurrentVersion\\Run", "HKLM\\Run"),
            ]
            for hive, path, location_name in autorun_locations:
                try:
                    with winreg.OpenKey(hive, path) as key:
                        idx = 0
                        while True:
                            try:
                                name, value, _ = winreg.EnumValue(key, idx)
                                if any(x in value.lower() for x in ["appdata", "temp", "cmd /c", "cscript"]):
                                    indicators = [f"Запис автозапуску: {name}"]
                                    score = 20
                                    if "appdata" in value.lower() or "temp" in value.lower():
                                        score += 30
                                        indicators.append("Команда з AppData або Temp")
                                    if "cmd /c" in value.lower():
                                        score += 30
                                        indicators.append("CMD команда")
                                    if score >= 50:
                                        finding = {
                                            "type": "Підозрілий автозапуск",
                                            "name": name,
                                            "location": location_name,
                                            "command": value[:200],
                                            "full_command": value,
                                            "score": score,
                                            "severity": "ВИСОКИЙ" if score < 70 else "КРИТИЧНИЙ",
                                            "indicators": indicators
                                        }
                                        findings.append(finding)
                                        logger.info(f"AUTORUN: {location_name}\\{name}")
                                idx += 1
                            except WindowsError:
                                break
                except Exception as e:
                    logger.debug(f"Помилка сканування {path}: {e}")
        except Exception as e:
            logger.error(f"Помилка в автозапуску сканування: {e}")
        return findings


class NetworkAnalyzerV12_1:
    """Аналізатор мережевих з'єднань"""

    def analyze_connections(self):
        findings = []
        try:
            connections = psutil.net_connections(kind='inet')
            for conn in connections:
                try:
                    if conn.raddr:
                        remote_ip = conn.raddr[0]
                except:
                    pass
        except Exception as e:
            logger.error(f"Помилка аналізу мережі: {e}")
        return findings


CREATE_NO_WINDOW = 0x08000000


# Загальна утиліта: запускає зовнішні команди без видимого консольного вікна
def run_hidden_subprocess(cmd, timeout=5):
    """Виконати команду БЕЗ видимого console вікна"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL
        )
        return result
    except:
        return None


# ══════════════════════════════════════════════════════════════════
# PTERODO SCANNER v15 (КОМПЛЕКСНИЙ)
# ══════════════════════════════════════════════════════════════════
# Цей клас збирає всі PTERODO-методи в один комплексний сканер
class PterodoScannerV15:
    """Комплексний сканер Pterodo v15.0"""

    def __init__(self):
        self.process_analyzer = ProcessAnalyzerV12_1()
        self.persistence_scanner = PersistenceScannerV12_1()
        self.network_analyzer = NetworkAnalyzerV12_1()
        self.pterodo_engine_v13 = PterodoDetectionEnginev13()
        self.pterodo_engine_v14 = PterodoDetectionEnginev14Extended()
        self.pterodo_engine_v19 = PterodoDetectionEnginev19()

    def check_is_admin(self):
        """Перевірити права адміністратора"""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False

    def run_full_scan_v15(self):
        """ПОВНЕ СКАНУВАННЯ v15 ЗІ ВСІМА МЕТОДАМИ"""
        all_findings = []

        logger.info("════════════════════════════════════════════════════════════════════")
        logger.info("ЗАПУСК КОМПЛЕКСНОГО PTERODO SCANNER v15.0")
        logger.info("════════════════════════════════════════════════════════════════════")

        # v12.1 методи
        logger.info("\n[1/9] Аналіз запущених процесів...")
        all_findings.extend(self.process_analyzer.scan_all_processes())

        logger.info("[2/9] Сканування автозапуску в реєстрі...")
        all_findings.extend(self.persistence_scanner.scan_registry_autorun())

        logger.info("[3/9] Аналіз мережевих з'єднань...")
        all_findings.extend(self.network_analyzer.analyze_connections())

        # v13 методи
        logger.info("[4/9] Сканування офісних документів з макросами...")
        all_findings.extend(self.pterodo_engine_v13.scan_office_documents_with_macros())

        logger.info("[5/9] Сканування приховани файлів на USB...")
        all_findings.extend(self.pterodo_engine_v13.scan_hidden_files_on_removable_media())

        logger.info("[6/9] Пошук виконуваних дублікатів...")
        all_findings.extend(self.pterodo_engine_v13.scan_executable_doubles())

        logger.info("[7/9] Аналіз мережевих з'єднань на C2...")
        all_findings.extend(self.pterodo_engine_v13.analyze_network_connections())

        logger.info("[8/9] Сканування реєстру на C2 домени...")
        all_findings.extend(self.pterodo_engine_v13.scan_registry_for_c2_domains())

        # v14 розширені методи
        logger.info("[9/10] Розширене сканування Pterodo v14...")
        all_findings.extend(self.pterodo_engine_v14.scan_pterodo_processes_extended())
        all_findings.extend(self.pterodo_engine_v14.scan_pterodo_files_advanced())
        all_findings.extend(self.pterodo_engine_v14.scan_pterodo_registry_deep())
        all_findings.extend(self.pterodo_engine_v14.scan_pterodo_network_extended())

        # 🔥🔥🔥 v17 - ПОВНА ДЕТЕКЦІЯ 🔥🔥🔥
        logger.info("[10/10] 🔥 v19 ТОЧНЕ PTERODO СКАНУВАННЯ...")
        v19_engine = PterodoDetectionEnginev19()

        logger.info("[10a] Реєстр...")
        all_findings.extend(v19_engine.scan_registry_pterodo_only())

        logger.info("[10b] Файли...")
        all_findings.extend(v19_engine.scan_files_pterodo())

        logger.info("[10c] Документи...")
        all_findings.extend(v19_engine.scan_documents_pterodo())

        logger.info("[10d] Процеси...")
        all_findings.extend(v19_engine.scan_processes_pterodo())

        logger.info("════════════════════════════════════════════════════════════════════")
        logger.info(f"ВСЬОГО ВИЯВЛЕНО: {len(all_findings)} ЗАГРОЗ")
        logger.info("════════════════════════════════════════════════════════════════════\n")

        return all_findings


# ══════════════════════════════════════════════════════════════════
# БЛОК: ЗАБОРОНЕНЕ ПЗ
# ══════════════════════════════════════════════════════════════════

GAME_INSTALLATION_PATHS = [
    r"C:\Program Files\Steam",
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Epic Games",
    r"C:\Program Files (x86)\Epic Games",
    r"C:\Program Files\Ubisoft",
    r"C:\Program Files (x86)\Ubisoft",
    r"C:\Program Files\Riot Games",
    r"C:\Program Files (x86)\Riot Games",
    r"C:\Program Files\Rockstar Games",
    r"C:\Program Files (x86)\Rockstar Games",
    r"C:\Program Files\GOG Galaxy",
    r"C:\Program Files (x86)\GOG Galaxy",
    r"C:\Program Files\Origin",
    r"C:\Program Files (x86)\Origin",
    r"C:\Program Files\Electronic Arts",
    r"C:\Program Files (x86)\Electronic Arts",
    r"C:\Program Files\Battle.net",
    r"C:\Program Files (x86)\Battle.net",
    r"C:\Users\*\AppData\Local\Battle.net",
    r"C:\Users\*\AppData\Local\Twitch",
    r"C:\Users\*\AppData\Roaming\.minecraft",
    r"C:\Users\*\AppData\Local\Roblox",
    r"C:\Users\*\AppData\Local\PlariumPlay",
    r"C:\Users\*\AppData\Local\Wargaming.net",
    r"C:\Users\*\AppData\Local\WhatsApp",
    r"C:\Program Files\Genshin Impact",
    r"C:\Program Files\Euro Truck Simulator 2",
]

GAME_UNINSTALL_REGISTRY_PATHS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

GAME_PROCESSES = [
    "steam.exe", "epicgameslauncher.exe", "launcher.exe",
    "valorant.exe", "csgo.exe", "cs2.exe", "dota2.exe",
    "wow.exe", "uplay.exe", "origin.exe", "battlenet.exe",
    "riotclientux.exe", "riotclient.exe", "ubisoftconnect.exe",
    "goggalaxy.exe", "fortniteclient.exe", "launcher.exe"
]

GAME_SIGNATURES = {
    "GTA V": ["gta5", "gta v", "gtav"],
    "Cyberpunk 2077": ["cyberpunk"],
    "The Witcher 3": ["witcher3", "witcher 3"],
    "Red Dead Redemption 2": ["rdr2", "red dead"],
    "Counter-Strike": ["csgo", "cs2", "counter-strike"],
    "Dota 2": ["dota2"],
    "Minecraft": ["minecraft", "tlauncher"],
    "Valorant": ["valorant"],
    "World of Tanks": ["worldoftanks", "wot"],
    "S.T.A.L.K.E.R": ["stalker", "stalkер"],
    "Roblox": ["roblox"],
    "Genshin Impact": ["genshin"],
    "Fortnite": ["fortnite", "fortniteclient", "epicgameslauncher"],
    "Apex Legends": ["apex", "r5apex", "apexlegends"],
    "Call of Duty Warzone": ["warzone", "codwarzone", "modernwarfare"],
    "PUBG": ["pubg", "tslgame"],
    "Battlefield V": ["bfv", "battlefieldv"],
    "Battlefield 2042": ["bf2042", "battlefield2042"],
    "Escape from Tarkov": ["tarkov", "escapefromtarkov"],
    "Rust": ["rust", "rustclient"],
    "ARK Survival Evolved": ["ark", "shootergame"],
    "DayZ": ["dayz", "dayz.exe"],
    "Terraria": ["terraria"],
    "Stardew Valley": ["stardew", "stardewvalley"],
    "The Sims 4": ["sims4", "thesims4"],
    "Euro Truck Simulator 2": ["ets2", "eurotrucksimulator2"],
    "Forza Horizon 5": ["forza", "forzahorizon5"],
    "Need for Speed Heat": ["nfsheat", "needforspeedheat"],
    "Elden Ring": ["eldenring", "elden ring"],
    "Dark Souls 3": ["darksouls3", "ds3"],
    "Sekiro": ["sekiro", "sekiroshadowsdietwice"],
    "Hogwarts Legacy": ["hogwarts", "hogwartslegacy"],
    "Baldur's Gate 3": ["baldursgate3", "bg3"],
    "Starfield": ["starfield"],
    "Fallout 4": ["fallout4"],
    "Skyrim": ["skyrim", "tesv"],
    "Among Us": ["amongus"],
    "Phasmophobia": ["phasmophobia"],
    "Dead by Daylight": ["deadbydaylight", "dbd"],
    "Left 4 Dead 2": ["l4d2", "left4dead2"],
    "Half-Life 2": ["hl2", "halflife2"],
    "Portal 2": ["portal2"],
    "League of Legends": ["leagueoflegends", "lol", "riotclient"],
    "Overwatch 2": ["overwatch2", "ow2"],
    "World of Warcraft": ["wow", "worldofwarcraft"],
    "Diablo IV": ["diablo4", "diabloiv"],
    "Hearthstone": ["hearthstone"],
    "Clash of Clans": ["clashofclans"],
    "Clash Royale": ["clashroyale"],
    "World of Warships": ["worldofwarships", "wows"],
    "Overwatch": ["overwatch"],
    "StarCraft II": ["starcraft2", "sc2"],
    "Heroes of the Storm": ["heroesofthestorm", "hots"],
    "FIFA": ["fifa", "eafc", "fc24", "fc25"],
    "Battlefield 1": ["bf1", "battlefield1"],
    "Battlefield 4": ["bf4", "battlefield4"],
    "The Sims 3": ["sims3", "thesims3"],
    "Assassin's Creed": ["assassinscreed", "acodyssey", "acvalhalla", "acorigins"],
    "Far Cry": ["farcry", "farcry5", "farcry6"],
    "Watch Dogs": ["watchdogs", "watchdogs2"],
    "Rainbow Six Siege": ["rainbowsix", "r6", "r6siege"],
    "Fallout 76": ["fallout76"],
    "DOOM Eternal": ["doom", "doometernal"],
    "Hearts of Iron IV": ["hoi4"],
    "Crusader Kings III": ["ck3"],
    "Europa Universalis IV": ["eu4"],
    "Total War": ["totalwar", "rome2", "warhammer3"],
    "Football Manager": ["footballmanager", "fm24", "fm25"],
    "Mortal Kombat 11": ["mk11", "mortalkombat11"],
    "Batman Arkham Knight": ["arkhamknight", "batmanarkham"],
    "SnowRunner": ["snowrunner"],
    "A Plague Tale": ["plaguetale"],
    "STALKER Anomaly": ["anomaly"],
    "Counter-Strike 1.6": ["cs1.6", "cs16"],
    "Counter-Strike Source": ["css", "cstrike"],
    "Roblox Studio": ["robloxstudio"],
    "Genshin Impact": ["genshinimpact", "yuanshen"],
    "Euro Truck Simulator 2": ["ets2", "eurotrucksimulator2"],
    "Telegram": ["telegram", "telegramdesktop"],
    "WhatsApp": ["whatsapp"],
    "Viber": ["viber"],
    "Tor Browser": ["tor", "torbrowser"],
    "Yandex": ["yandex", "yandexbrowser"],
    "Twitch": ["twitch"],
    "Plarium Play": ["plariumplay"],
    "Steam": ["steam"],
    "Epic Games Launcher": ["epicgameslauncher", "epicgames"],
    "Ubisoft Connect": ["ubisoftconnect", "ubisoft"],
    "Rockstar Launcher": ["rockstarlauncher", "rockstargames"],
    "GOG Galaxy": ["goggalaxy", "gog"],
    "Origin": ["origin"],
    "EA App": ["eadesktop", "eaapp"],
    "Battle.net": ["battlenet", "blizzard"],
    "Riot Client": ["riotclient", "riotgames"],
    "GTA IV": ["gta4", "gtaiv"],
    "GTA San Andreas": ["gtasa", "sanandreas"],
    "Max Payne 3": ["maxpayne3"],
    "The Witcher 2": ["witcher2"],
    "The Witcher 1": ["witcher1"],
    "Oblivion": ["oblivion"],
    "Morrowind": ["morrowind"],
    "Fallout New Vegas": ["newvegas", "falloutnv"],
    "Dark Souls Remastered": ["darksoulsremastered"],
    "Dark Souls 2": ["darksouls2", "ds2"],
    "Bloodborne (emu)": ["bloodborne"],
    "Assassin's Creed II": ["ac2"],
    "Assassin's Creed Brotherhood": ["acbrotherhood"],
    "Assassin's Creed Revelations": ["acrevelations"],
    "Far Cry 3": ["farcry3"],
    "Far Cry 4": ["farcry4"],
    "DOOM 2016": ["doom2016"],
    "Quake Champions": ["quakechampions"],
    "Team Fortress 2": ["tf2"],
    "Paladins": ["paladins"],
    "Warface": ["warface"],
    "The Forest": ["theforest"],
    "Sons of the Forest": ["sonsoftheforest"],
    "Green Hell": ["greenhell"],
    "Subnautica": ["subnautica"],
    "Raft": ["raft"],
    "Factorio": ["factorio"],
    "RimWorld": ["rimworld"],
    "Oxygen Not Included": ["oxygennotincluded"],
    "Don't Starve": ["dontstarve"],
    "Project Zomboid": ["zomboid"],
    "Need for Speed Most Wanted": ["nfsmw", "mostwanted"],
    "Need for Speed Underground 2": ["nfsu2"],
    "Assetto Corsa": ["assettocorsa"],
    "BeamNG.drive": ["beamng"],
    "Microsoft Flight Simulator": ["flightsimulator"],
    "Farming Simulator 22": ["farmingsimulator22", "fs22"],
    "House Flipper": ["houseflipper"],
    "PowerWash Simulator": ["powerwashsimulator"],
    "Outlast": ["outlast"],
    "Outlast 2": ["outlast2"],
    "Resident Evil 2": ["re2", "residentevil2"],
    "Resident Evil 4": ["re4", "residentevil4"],
    "Poppy Playtime": ["poppyplaytime"],
    "Civilization VI": ["civ6"],
    "Civilization V": ["civ5"],
    "Age of Empires II": ["aoe2"],
    "Age of Empires IV": ["aoe4"],
    "Stronghold Crusader": ["strongholdcrusader"],
    "War Thunder": ["warthunder"],
    "Black Desert": ["blackdesert"],
    "Lineage 2": ["lineage2"],
    "Albion Online": ["albion"],
    "CrossFire": ["crossfire"],
    "Honkai Star Rail": ["honkaistarrail"],
    "Honkai Impact 3rd": ["honkaiimpact"],
    "Tower of Fantasy": ["toweroffantasy"],
    "Fall Guys": ["fallguys"],
    "Human Fall Flat": ["humanfallflat"],
    "Gang Beasts": ["gangbeasts"],
    "Goat Simulator": ["goatsimulator"],
    "Half-Life": ["halflife"],
    "Half-Life Alyx": ["alyx"],
    "Portal": ["portal"],
    "Serious Sam": ["serioussam"],
    "Prince of Persia": ["princeofpersia"],
    "STALKER 2": ["stalker2", "heartofchernobyl"],
    "Metro 2033": ["metro2033"],
    "Metro Last Light": ["metrolastlight"],
    "Metro Exodus": ["metroexodus"],
    "GTA Trilogy": ["gta3", "gtavicecity", "gtasanandreas"],
    "Bully": ["bully"],
    "L.A. Noire": ["lanoire"],
    "Assassin's Creed Odyssey": ["acodyssey"],
    "Assassin's Creed Valhalla": ["acvalhalla"],
    "Assassin's Creed Origins": ["acorigins"],
    "The Division": ["division"],
    "The Division 2": ["division2"],
    "Ghost Recon Wildlands": ["wildlands"],
    "Ghost Recon Breakpoint": ["breakpoint"],
    "Titanfall 2": ["titanfall2"],
    "Star Wars Jedi Fallen Order": ["fallenorder"],
    "Star Wars Jedi Survivor": ["jedisurvivor"],
    "Wolfenstein The New Order": ["wolfenstein"],
    "Wolfenstein II": ["wolf2"],
    "Dishonored": ["dishonored"],
    "Dishonored 2": ["dishonored2"],
    "Prey": ["prey"],
    "Resident Evil 3": ["re3"],
    "Resident Evil 7": ["re7"],
    "Resident Evil Village": ["re8", "village"],
    "Devil May Cry 5": ["dmc5"],
    "Monster Hunter World": ["monsterhunterworld"],
    "Final Fantasy XV": ["ffxv"],
    "Final Fantasy VII Remake": ["ff7remake"],
    "Rise of the Tomb Raider": ["tombraider"],
    "Shadow of the Tomb Raider": ["shadowoftombraider"],
    "God of War": ["godofwar"],
    "Days Gone": ["daysgone"],
    "Horizon Zero Dawn": ["horizonzerodawn"],
    "Spider-Man Remastered": ["spidermanremastered"],
    "Valheim": ["valheim"],
    "7 Days to Die": ["7daystodie"],
    "Scum": ["scum"],
    "The Long Dark": ["thelongdark"],
    "Insurgency Sandstorm": ["insurgency"],
    "Squad": ["squad"],
    "Hell Let Loose": ["hellletloose"],
    "Ready or Not": ["readyornot"],
    "People Playground": ["peopleplayground"],
    "Teardown": ["teardown"],
    "Besiege": ["besiege"],
    "Undertale": ["undertale"],
    "Deltarune": ["deltarune"],
    "Cuphead": ["cuphead"],
    "Hollow Knight": ["hollowknight"],
    "Dead Cells": ["deadcells"],
    "Celeste": ["celeste"],
    "Five Nights at Freddy's": ["fnaf"],
    "Security Breach": ["securitybreach"],
    "Visage": ["visage"],
    "Cities Skylines": ["cities skylines", "citiesskylines"],
    "Banished": ["banished"],
    "Anno 1800": ["anno1800"],
    "Lost Ark": ["lostark"],
    "New World": ["newworld"],
    "Guild Wars 2": ["gw2"],
    "EVE Online": ["eveonline"],
    "Forza Horizon 4": ["forzahorizon4"],
    "F1 22": ["f122"],
    "F1 23": ["f123"],
    "DIRT Rally 2.0": ["dirtrally2"],
    "Tekken 7": ["tekken7"],
    "Street Fighter V": ["sfv"],
    "Injustice 2": ["injustice2"],
    "NBA 2K23": ["nba2k23"],
    "NBA 2K24": ["nba2k24"],
    "eFootball": ["efootball"],
    "Kerbal Space Program": ["ksp"],
    "Prison Architect": ["prisonarchitect"],
    "Beat Saber": ["beatsaber"],
    "Boneworks": ["boneworks"],

}

ANTIVIRUS_DETECTION = {
    "Kaspersky": {
        "processes": ["avp.exe", "kissvc.exe", "kavsvc.exe"],
        "paths": [r"C:\Program Files\Kaspersky", r"C:\Program Files (x86)\Kaspersky"],
    },
    "Norton": {
        "processes": ["NortonAntiVirus.exe", "NSNSE.exe"],
        "paths": [r"C:\Program Files\Norton", r"C:\Program Files (x86)\Norton"],
    },
    "McAfee": {
        "processes": ["mcagent.exe", "mcapexe.exe"],
        "paths": [r"C:\Program Files\McAfee", r"C:\Program Files (x86)\McAfee"],
    },
    "Avast": {
        "processes": ["AvastUI.exe", "avastui.exe"],
        "paths": [r"C:\Program Files\Avast", r"C:\Program Files (x86)\Avast"],
    },
    "AVG": {
        "processes": ["avgui.exe", "avgcsvc.exe"],
        "paths": [r"C:\Program Files\AVG", r"C:\Program Files (x86)\AVG"],
    },
    "Bitdefender": {
        "processes": ["bdagent.exe", "vsserv.exe"],
        "paths": [r"C:\Program Files\Bitdefender", r"C:\Program Files (x86)\Bitdefender"],
    },
    "Dr.Web": {
        "processes": ["dwservice.exe", "dwengine.exe"],
        "paths": [r"C:\Program Files\DrWeb", r"C:\Program Files (x86)\DrWeb"],
    },
    "Panda": {
        "processes": ["panda.exe", "pandaguard.exe"],
        "paths": [r"C:\Program Files\Panda", r"C:\Program Files (x86)\Panda"],
    },
    "Trend Micro": {
        "processes": ["tmproxy.exe", "ntrtscan.exe"],
        "paths": [r"C:\Program Files\Trend Micro", r"C:\Program Files (x86)\Trend Micro"],
    },
    "F-Secure": {
        "processes": ["fshoster32.exe", "fsaua.exe"],
        "paths": [r"C:\Program Files\F-Secure", r"C:\Program Files (x86)\F-Secure"],
    },
    "Malwarebytes": {
        "processes": ["mbamservice.exe", "mbamtray.exe"],
        "paths": [r"C:\Program Files\Malwarebytes", r"C:\Program Files (x86)\Malwarebytes"],
    },
    "Sophos": {
        "processes": ["SophosHealth.exe", "SophosFS.exe", "SophosClean.exe"],
        "paths": [r"C:\Program Files\Sophos", r"C:\Program Files (x86)\Sophos"],
    },
    "Comodo": {
        "processes": ["cis.exe", "cmdagent.exe"],
        "paths": [r"C:\Program Files\Comodo", r"C:\Program Files (x86)\Comodo"],
    },
    "360 Total Security": {
        "processes": ["360sd.exe", "360tray.exe"],
        "paths": [r"C:\Program Files\360", r"C:\Program Files (x86)\360"],
    },
    "Cylance": {
        "processes": ["CylanceSvc.exe"],
        "paths": [r"C:\Program Files\Cylance", r"C:\Program Files (x86)\Blackberry\Cylance"],
    },
    "SentinelOne": {
        "processes": ["SentinelAgent.exe", "SentinelMonitor.exe"],
        "paths": [r"C:\Program Files\SentinelOne"],
    },
    "Adaware": {
        "processes": ["adaware.exe", "adawareservice.exe"],
        "paths": [r"C:\Program Files\Adaware", r"C:\Program Files (x86)\Adaware"],
    },
    "ZoneAlarm": {
        "processes": ["vmon.exe", "zatray.exe"],
        "paths": [r"C:\Program Files\CheckPoint\ZoneAlarm"],
    },
    "G Data": {
        "processes": ["GDScan.exe", "GDServ.exe"],
        "paths": [r"C:\Program Files\G Data", r"C:\Program Files (x86)\G Data"],
    },
    "BullGuard": {
        "processes": ["BullGuard.exe", "BullGuardScanner.exe"],
        "paths": [r"C:\Program Files\BullGuard Ltd"],
    },
    "Zillya": {
        "processes": ["Zillya.exe", "ZillyaService.exe", "ZillyaScanner.exe", "ZAVAU.exe"],
        "paths": [r"C:\Program Files\Zillya Antivirus", r"C:\Program Files (x86)\Zillya Antivirus"],
    },
    "Cisco Secure Endpoint": {
        "processes": ["sfc.exe", "iptray.exe"],
        "paths": [r"C:\Program Files\Cisco\AMP"],
    },
    "TotalAV": {
        "processes": ["TotalAV.exe", "TotalAVService.exe"],
        "paths": [r"C:\Program Files\TotalAV", r"C:\Program Files (x86)\TotalAV"],
    },
    "Avira": {
        "processes": ["avpui.exe", "avguard.exe", "sched.exe"],
        "paths": [r"C:\Program Files\Avira", r"C:\Program Files (x86)\Avira"],
    },
    "VipNet": {
        "processes": ["VipNet.exe", "vntray.exe"],
        "paths": [r"C:\Program Files\InfoTeCS", r"C:\Program Files (x86)\InfoTeCS"],
    }
}


def get_disk_types_dict():
    """Отримати словник дисків v15.0"""
    try:
        return AdvancedDiskDetectorV15.map_drives_to_types()
    except Exception as e:
        logger.error(f"Error getting disk types: {e}")
        return {}


def get_public_ip():
    """Отримати публічну IP"""
    try:
        with urllib.request.urlopen('https://api.ipify.org?format=json', timeout=3) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data.get('ip', 'Невідомо')
    except:
        return "Невідомо"


def get_gateway_ip():
    """Отримати IP шлюзу"""
    if platform.system() != "Windows":
        return "Невідомо"
    try:
        key_path = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            idx = 0
            while True:
                try:
                    interface_name = winreg.EnumKey(key, idx)
                    interface_path = f"{key_path}\\{interface_name}"
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, interface_path) as iface_key:
                        try:
                            dhcp_enabled, _ = winreg.QueryValueEx(iface_key, "EnableDHCP")
                            if dhcp_enabled:
                                gateway, _ = winreg.QueryValueEx(iface_key, "DhcpDefaultGateway")
                                if gateway and isinstance(gateway, list) and len(gateway) > 0:
                                    return gateway[0]
                            else:
                                gateway, _ = winreg.QueryValueEx(iface_key, "DefaultGateway")
                                if gateway and isinstance(gateway, list) and len(gateway) > 0:
                                    return gateway[0]
                        except:
                            pass
                    idx += 1
                except WindowsError:
                    break
    except:
        pass
    return "Невідомо"


def get_dns_servers():
    """Отримати DNS"""
    dns_set = set()
    if platform.system() != "Windows":
        return []
    try:
        try:
            key_path = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                try:
                    dns_servers, _ = winreg.QueryValueEx(key, "NameServer")
                    if dns_servers:
                        for dns in str(dns_servers).split(','):
                            if dns.strip():
                                dns_set.add(dns.strip())
                except:
                    pass
        except:
            pass

        try:
            result = run_hidden_subprocess(["ipconfig", "/all"], timeout=5)
            if result and result.stdout:
                lines = result.stdout.split('\n')
                for line in lines:
                    if "DNS Server" in line:
                        try:
                            parts = line.split(':')
                            if len(parts) > 1:
                                dns = parts[1].strip()
                                if dns and dns not in ['.', '']:
                                    dns_set.add(dns)
                        except:
                            pass
        except:
            pass

        try:
            key_path = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                idx = 0
                while True:
                    try:
                        interface_name = winreg.EnumKey(key, idx)
                        interface_path = f"{key_path}\\{interface_name}"
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, interface_path) as iface_key:
                            try:
                                static_dns, _ = winreg.QueryValueEx(iface_key, "NameServer")
                                if static_dns:
                                    for dns in str(static_dns).split(','):
                                        if dns.strip():
                                            dns_set.add(dns.strip())
                            except:
                                pass
                            try:
                                dhcp_dns, _ = winreg.QueryValueEx(iface_key, "DhcpNameServer")
                                if dhcp_dns:
                                    for dns in str(dhcp_dns).split(','):
                                        if dns.strip():
                                            dns_set.add(dns.strip())
                            except:
                                pass
                        idx += 1
                    except WindowsError:
                        break
        except:
            pass
    except Exception as e:
        logger.error(f"Помилка збору DNS: {e}")
    return sorted([dns for dns in dns_set if dns and dns not in ['.', 'unknown']])


def get_mac_addresses():
    """Отримати MAC"""
    macs = []
    try:
        for interface, if_addrs in psutil.net_if_addrs().items():
            for addr in if_addrs:
                if addr.family == psutil.AF_LINK:
                    try:
                        is_active = psutil.net_if_stats()[interface].isup
                    except:
                        is_active = False
                    macs.append({
                        "interface": interface,
                        "mac": addr.address,
                        "is_active": is_active
                    })
    except:
        pass
    return macs


def get_precise_windows_version():
    """Отримати версію Windows"""
    try:
        if platform.system() != "Windows":
            return platform.platform()

        version = sys.getwindowsversion()
        build = version.build
        major = version.major
        minor = version.minor
        edition = ""
        try:
            edition = platform.win32_edition()
        except:
            edition = ""
        arch = platform.machine() or ""

        if major == 10:
            name = "Windows 11" if build >= 22000 else "Windows 10"
        elif major == 6 and minor == 3:
            name = "Windows 8.1"
        elif major == 6 and minor == 2:
            name = "Windows 8"
        elif major == 6 and minor == 1:
            name = "Windows 7"
        else:
            name = "Windows"

        parts = [name]
        if edition:
            parts.append(edition)
        parts.append(f"Build {build}")
        if arch:
            parts.append(arch)
        return " ".join(parts)
    except:
        try:
            return platform.platform()
        except:
            return "Windows (Невідомо)"


def get_last_boot_time():
    """Отримати час завантаження"""
    try:
        boot_time_timestamp = psutil.boot_time()
        boot_time = dt.fromtimestamp(boot_time_timestamp)
        uptime_seconds = int(time.time() - boot_time_timestamp)
        hours = uptime_seconds // 3600
        minutes = (uptime_seconds % 3600) // 60
        return {
            "last_boot": boot_time.isoformat(),
            "uptime": f"{hours}h {minutes}m",
            "uptime_seconds": uptime_seconds
        }
    except:
        return {"last_boot": "Невідомо", "uptime": "Невідомо", "uptime_seconds": 0}


def get_cpu_model():
    """Отримати CPU"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu_name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if cpu_name and cpu_name.strip():
                return cpu_name.strip()
    except:
        pass
    try:
        result = run_hidden_subprocess(["wmic", "cpu", "get", "Name"], timeout=3)
        if result and result.stdout:
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1 and lines[1].strip():
                return lines[1].strip()
    except:
        pass
    return "Невідомо"


def get_gpu_models():
    """Отримати GPU"""
    gpus = []
    try:
        if wmi:
            w = wmi.WMI()
            for gpu in w.Win32_VideoController():
                if gpu.Name:
                    gpus.append(gpu.Name)
    except:
        pass
    if not gpus:
        try:
            result = run_hidden_subprocess(["wmic", "path", "win32_videocontroller", "get", "name"], timeout=5)
            if result:
                lines = result.stdout.strip().split('\n')
                for line in lines[1:]:
                    if line.strip():
                        gpus.append(line.strip())
        except:
            pass
    return gpus if gpus else ["Невідомо"]


# Клас для збору загальної інформації про систему: CPU, RAM, диск, мережу, IP та ін.
class SystemInformationCollector:
    """Збір системної інформації"""

    @staticmethod
    def get_system_info():
        """Отримати систему"""
        try:
            cpu_count = psutil.cpu_count(logical=False)
            cpu_logical = psutil.cpu_count(logical=True)
            cpu_model = get_cpu_model()
            memory = psutil.virtual_memory()
            gpu_models = get_gpu_models()
            windows_ver = get_precise_windows_version()

            try:
                hostname = socket.gethostname()
                ip_address = socket.gethostbyname(hostname)
            except:
                hostname = "НЕВІДОМО"
                ip_address = "НЕВІДОМО"

            mac_addresses = get_mac_addresses()
            boot_info = get_last_boot_time()
            disk_types_map = get_disk_types_dict()

            disks_info = []
            for partition in psutil.disk_partitions():
                if 'cdrom' in partition.opts or partition.fstype == '':
                    continue
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    disk_type = disk_types_map.get(partition.device, "Unknown")
                    disks_info.append({
                        "drive": partition.device,
                        "total": round(usage.total / (1024 ** 3), 2),
                        "used": round(usage.used / (1024 ** 3), 2),
                        "free": round(usage.free / (1024 ** 3), 2),
                        "used_percent": round(usage.percent, 1),
                        "free_percent": round(100 - usage.percent, 1),
                        "type": disk_type
                    })
                except:
                    pass

            public_ip = get_public_ip()
            gateway_ip = get_gateway_ip()
            dns_servers = get_dns_servers()

            return {
                "computer_name": os.environ.get('COMPUTERNAME', 'НЕВІДОМО'),
                "username": os.environ.get('USERNAME', 'НЕВІДОМО'),
                "os": windows_ver,
                "cpu_model": cpu_model,
                "cpu_cores": cpu_count,
                "cpu_threads": cpu_logical,
                "gpu_models": gpu_models,
                "ram_total": round(memory.total / (1024 ** 3), 2),
                "ram_available": round(memory.available / (1024 ** 3), 2),
                "ram_percent": round(memory.percent, 1),
                "disks": disks_info,
                "hostname": hostname,
                "ip_address": ip_address,
                "public_ip": public_ip,
                "gateway_ip": gateway_ip,
                "mac_addresses": mac_addresses,
                "dns_servers": dns_servers,
                "last_boot_time": boot_info["last_boot"],
                "system_uptime": boot_info["uptime"],
                "scan_time": dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as e:
            logger.error(f"Помилка збору системної інформації: {e}")
            return {"error": str(e)}


# Клас для виявлення забороненого програмного забезпечення: ігор та сторонніх антивірусів
class GameAndSoftwareScanner:
    """Сканер ігор та антивірусів"""

    @staticmethod
    def _expand_paths(paths):
        """Розгортає шляхи з *"""
        expanded = []
        for path in paths:
            if "*" in path:
                expanded.extend(glob.glob(path))
            else:
                expanded.append(path)
        return expanded

    @staticmethod
    def _detect_game_name(raw_name):
        """Нормалізація назви гри"""
        name = raw_name.lower()

        for game, keywords in GAME_SIGNATURES.items():
            for kw in keywords:
                if kw in name:
                    return game

        return raw_name

    @staticmethod
    def _scan_game_directory(base_path, findings, seen):
        if not os.path.exists(base_path):
            return

        try:
            for root, dirs, files in os.walk(base_path):
                rel_depth = root.count(os.sep) - base_path.count(os.sep)
                if rel_depth > 4:
                    dirs[:] = []
                    continue

                root_lower = root.lower()
                for game, keywords in GAME_SIGNATURES.items():
                    if any(kw in root_lower for kw in keywords):
                        if game not in seen:
                            findings.append({
                                "software": game,
                                "type": "Встановлена гра або лаунчер"
                            })
                            seen.add(game)
                        break

                for file in files:
                    file_lower = file.lower()
                    if file_lower.endswith((".exe", ".lnk", ".url")):
                        raw_name = os.path.splitext(file_lower)[0]
                        game_name = GameAndSoftwareScanner._detect_game_name(raw_name)
                        if game_name not in seen:
                            findings.append({
                                "software": game_name,
                                "type": "Ймовірна гра"
                            })
                            seen.add(game_name)
        except Exception:
            pass

    @staticmethod
    def _scan_registry_for_games(findings, seen):
        for hive, key_path in GAME_UNINSTALL_REGISTRY_PATHS:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    idx = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(key, idx)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                values = []
                                for value_name in ("DisplayName", "InstallLocation", "DisplayIcon", "Publisher"):
                                    try:
                                        value, _ = winreg.QueryValueEx(subkey, value_name)
                                        if value:
                                            values.append(str(value))
                                    except WindowsError:
                                        pass

                                search_text = " ".join(values).lower()
                                if not search_text:
                                    idx += 1
                                    continue

                                for game, keywords in GAME_SIGNATURES.items():
                                    for kw in keywords:
                                        if kw in search_text:
                                            if game not in seen:
                                                findings.append({
                                                    "software": game,
                                                    "type": "Інстальована гра (реєстр)"
                                                })
                                                seen.add(game)
                                            break
                                    else:
                                        continue
                                    break
                        except WindowsError:
                            break
                        idx += 1
            except Exception:
                pass

    @staticmethod
    def scan_for_games():
        findings = []
        seen = set()

        if platform.system() != "Windows":
            return []

        expanded_paths = GameAndSoftwareScanner._expand_paths(GAME_INSTALLATION_PATHS)

        for base_path in expanded_paths:
            GameAndSoftwareScanner._scan_game_directory(base_path, findings, seen)

        GameAndSoftwareScanner._scan_registry_for_games(findings, seen)

        try:
            result = run_hidden_subprocess(["tasklist"])
            if result:
                output = result.stdout.lower()

                for game_proc in GAME_PROCESSES:
                    if game_proc in output:
                        raw_name = game_proc.replace(".exe", "")
                        game_name = GameAndSoftwareScanner._detect_game_name(raw_name)
                        if game_name not in seen:
                            findings.append({
                                "software": game_name,
                                "type": "Запущена гра"
                            })
                            seen.add(game_name)
        except Exception:
            pass

        return findings

    @staticmethod
    def scan_for_third_party_antivirus():
        """Пошук антивірусів"""
        findings = []
        if platform.system() != "Windows":
            return []
        for av_name, av_info in ANTIVIRUS_DETECTION.items():
            detected = False
            try:
                for process_name in av_info.get("processes", []):
                    result = run_hidden_subprocess(["tasklist", "/FI", f"IMAGENAME eq {process_name}"])
                    if result and process_name.lower() in result.stdout.lower():
                        detected = True
                        break
            except:
                pass
            if not detected:
                for path in av_info.get("paths", []):
                    try:
                        if os.path.exists(path):
                            detected = True
                            break
                    except:
                        pass
            if detected:
                findings.append({"software": av_name, "type": "Сторонній антивірус"})
        return findings


# ══════════════════════════════════════════════════════════════════
# БЛОК: ГРАФІЧНИЙ ІНТЕРФЕЙС (GUI)
# ══════════════════════════════════════════════════════════════════
# КОМПОНЕНТИ ГРАФІЧНОГО ІНТЕРФЕЙСУ (ОРИГІНАЛЬ)
# ══════════════════════════════════════════════════════════════════

# Всі GUI-елементи та управління інтерфейсом зібрані в цьому блоці

def resource_path(relative_path):
    """Отримати абсолютний шлях до ресурсів"""
    try:
        base_path = sys._MEIPASS
    except:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def get_logo_image(size=(50, 50)):
    """Завантажити логотип з файлу"""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")

    logo_name = "Управліннінформаційно-аналітичної підтримки.png"
    full_path = os.path.join(base_path, logo_name)

    if os.path.exists(full_path):
        try:
            img = tk.PhotoImage(file=full_path)
            return img
        except:
            pass

    return None


class RoundedButton(tk.Canvas):
    """Закруглена кнопка з градієнтом та ефектом наведення"""

    def __init__(self, parent, text, command, bg_color, bg_color2=None, width=260, height=55):
        super().__init__(parent, width=width, height=height, bg="#11161c", highlightthickness=0, relief=tk.FLAT)
        self.command = command
        self.bg_color = bg_color
        self.bg_color2 = bg_color2 if bg_color2 else bg_color
        self.width = width
        self.height = height
        self.text = text
        self.radius = 15
        self.draw()

        self.bind("<Enter>", lambda e: self.on_enter())
        self.bind("<Leave>", lambda e: self.on_leave())
        self.bind("<Button-1>", lambda e: self.on_click())

    def _interpolate(self, color1, color2, fraction):
        """Допоміжна функція для розрахунку кольору градієнта"""
        r1, g1, b1 = int(color1[1:3], 16), int(color1[3:5], 16), int(color1[5:7], 16)
        r2, g2, b2 = int(color2[1:3], 16), int(color2[3:5], 16), int(color2[5:7], 16)
        r = int(r1 + (r2 - r1) * fraction)
        g = int(g1 + (g2 - g1) * fraction)
        b = int(b1 + (b2 - b1) * fraction)
        return f'#{r:02x}{g:02x}{b:02x}'

    def draw(self, hover=False):
        """Рисування кнопки з градієнтом"""
        self.delete("all")

        c1 = self.lighten_color(self.bg_color, 25) if hover else self.bg_color
        c2 = self.lighten_color(self.bg_color2, 25) if hover else self.bg_color2

        for i in range(self.height):
            color = self._interpolate(c1, c2, i / self.height)

            if i < self.radius:
                offset = self.radius - (self.radius ** 2 - (self.radius - i) ** 2) ** 0.5
            elif i > self.height - self.radius:
                offset = self.radius - (self.radius ** 2 - (i - (self.height - self.radius)) ** 2) ** 0.5
            else:
                offset = 0

            self.create_line(offset, i, self.width - offset, i, fill=color)

        self.create_text(self.width // 2, self.height // 2, text=self.text, font=("Segoe UI", 10, "bold"), fill="white")

    def lighten_color(self, hex_color, amount):
        """Посвітлити колір"""
        hex_color = hex_color.lstrip('#')
        r = min(255, int(hex_color[0:2], 16) + amount)
        g = min(255, int(hex_color[2:4], 16) + amount)
        b = min(255, int(hex_color[4:6], 16) + amount)
        return f'#{r:02x}{g:02x}{b:02x}'

    def on_enter(self):
        """Подія: курсор наведений на кнопку"""
        self.draw(True)
        self.config(cursor="hand2")

    def on_leave(self):
        """Подія: курсор залишив межи кнопки"""
        self.draw(False)
        self.config(cursor="arrow")

    def on_click(self):
        """Подія: клік по кнопці"""
        if self.command:
            self.command()


# ══════════════════════════════════════════════════════════════════
# ОРИГІНАЛЬНИЙ ГРАФІЧНИЙ ІНТЕРФЕЙС (ЗБЕРЕЖЕНИЙ ПОВНІСТЮ)
# ══════════════════════════════════════════════════════════════════

class PterodoForensicGUIApp:
    """Графічний інтерфейс"""

    def __init__(self, root):
        self.root = root
        self.root.title("UAC-0010 SCANNER v15.0 - Управління інформаційно-аналітичної підтримки")
        self.root.geometry("1200x800")
        self.root.configure(bg="#11161c")
        self.logo_image = None
        self.scanning = False

        self.create_ui()

    def create_ui(self):
        """Створення графічного інтерфейсу"""
        main_frame = tk.Frame(self.root, bg="#11161c")
        main_frame.pack(fill=tk.BOTH, expand=True)

        try:
            bg_img = Image.open(resource_path("mic.png")).convert("RGBA")
            overlay = Image.new("RGBA", bg_img.size, (13, 17, 23, 120))
            bg_img = Image.alpha_composite(bg_img, overlay)

            opacity = 0.08
            alpha = bg_img.split()[3]
            alpha = ImageEnhance.Brightness(alpha).enhance(opacity)
            bg_img.putalpha(alpha)

            bg_img = bg_img.resize((1200, 800), Image.LANCZOS)

            self.bg_photo = ImageTk.PhotoImage(bg_img)

            bg_label = tk.Label(main_frame, image=self.bg_photo, bg="#11161c")
            bg_label.place(x=0, y=0, relwidth=1, relheight=1)

        except:
            pass

        # ===== ШАПКА ПРОГРАМИ =====
        top_frame = tk.Frame(main_frame, bg="#11161c")
        top_frame.pack(fill=tk.X, padx=10, pady=(20, 10))

        top_frame.columnconfigure(0, weight=0)
        top_frame.columnconfigure(1, weight=1)
        top_frame.columnconfigure(2, weight=0)

        # ЛОГОТИП - ПОКРАЩЕНА ЗАВАНТАЖЕННЯ
        self.logo_image = get_logo_image((60, 60))
        if self.logo_image:
            self.logo_image = self.logo_image.subsample(2, 2)
            logo_label = tk.Label(top_frame, image=self.logo_image, bg="#11161c")
            logo_label.grid(row=0, column=0, sticky="w", padx=(40, 0))

        text_container = tk.Frame(top_frame, bg="#11161c")
        text_container.grid(row=0, column=1, sticky="nsew")

        tk.Label(
            text_container,
            text="UAC-0010 SCANNER v15.0",
            font=("Segoe UI", 28, "bold"),
            fg="#79c0ff",
            bg="#11161c"
        ).pack(expand=True)

        tk.Label(
            text_container,
            text="Управління інформаційно-аналітичної підтримки \n ГУНП в Хмельницькій області",
            font=("Segoe UI", 14),
            fg="#c9d1d9",
            bg="#11161c"
        ).pack(expand=True)

        spacer = tk.Frame(top_frame, bg="#11161c", width=100)
        spacer.grid(row=0, column=2)

        # ===== ОСНОВНИЙ КОНТЕНТ =====
        content_frame = tk.Frame(main_frame, bg="#11161c")
        content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        content_frame.columnconfigure(1, weight=1)
        content_frame.rowconfigure(0, weight=1)

        # ===== КНОПКИ СКАНУВАННЯ =====
        buttons_frame = tk.Frame(content_frame, bg="#11161c")
        buttons_frame.grid(row=0, column=0, padx=(0, 20))

        button_configs = [
            ("🔍 Pterodo", self.start_pterodo_scan, "#FF6B35"),
            ("🎮 Заборонене ПЗ", self.start_games_scan, "#004E89"),
            ("⚠️ Антивіруси", self.start_antivirus_scan, "#F77F00"),
            ("💻 Система", self.show_system_info, "#06A77D"),
            ("ІПНП та ІІПС", self.action_ipnp_iips, "#2EA043"),
            ("🔐 Повний аудит", self.start_full_scan_sequential, "#9D4EDD"),
        ]

        for btn_text, btn_cmd, color in button_configs:
            btn = RoundedButton(buttons_frame, btn_text, btn_cmd, color, width=250, height=50)
            btn.pack(pady=10)

        # ===== ОБЛАСТЬ РЕЗУЛЬТАТІВ =====
        output_frame = tk.Frame(content_frame, bg="#11161c")
        output_frame.grid(row=0, column=1, sticky="nsew")

        tk.Label(output_frame, text="📊 Звіт сканування", font=("Segoe UI", 12, "bold"), fg="#79c0ff",
                 bg="#11161c").pack(anchor="w")

        self.result_text = scrolledtext.ScrolledText(
            output_frame, wrap=tk.WORD, bg="#161b22", fg="#c9d1d9",
            font=("Consolas", 9), borderwidth=0, highlightthickness=1, highlightbackground="#30363d"
        )
        self.result_text.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        # ===== НИЖНЯ ПАНЕЛЬ =====
        bottom_frame = tk.Frame(main_frame, bg="#11161c")
        bottom_frame.pack(fill=tk.X, padx=25, pady=15)

        save_btn = RoundedButton(bottom_frame, "💾 Зберегти звіт", self.save_report, "#238636", width=140, height=35)
        save_btn.pack(side=tk.LEFT, padx=5)

        clear_btn = RoundedButton(bottom_frame, "🗑️ Очистити", self.clear_results, "#da3633", width=140, height=35)
        clear_btn.pack(side=tk.LEFT, padx=5)

        self.status_label = tk.Label(bottom_frame, text="✓ Готово", font=("Segoe UI", 10, "bold"),
                                     fg="#3fb950",
                                     bg="#11161c")
        self.status_label.pack(side=tk.RIGHT)

        # ===== ТЕГИ КОЛЬОРУВАННЯ =====
        self.result_text.tag_config("header", foreground="#79c0ff", font=("Consolas", 10, "bold"))
        self.result_text.tag_config("success", foreground="#3fb950")
        self.result_text.tag_config("warning", foreground="#d29922")
        self.result_text.tag_config("critical", foreground="#f85149")
        self.result_text.tag_config("info", foreground="#79c0ff")
        self.result_text.tag_config("signature", foreground="#a371f7")
        self.result_text.tag_config("entropy", foreground="#b392f0")
        self.result_text.tag_config("score", foreground="#56d4dd")
        self.result_text.tag_config("disk_ssd", foreground="#3fb950")
        self.result_text.tag_config("disk_hdd", foreground="#d29922")

    def append_result(self, text, tag=None):
        """До  ати текст до області результатів"""
        self.result_text.insert(tk.END, text + "\n", tag)
        self.result_text.see(tk.END)
        self.root.update()

    def start_pterodo_scan(self):
        """Запуск сканування на Pterodo v15"""
        if self.scanning:
            self.status_label.config(text="⏳ Вже сканується...")
            return

        self.scanning = True
        self.clear_results()

        def scan():
            try:
                scanner = PterodoScannerV15()

                if not scanner.check_is_admin():
                    self.append_result("❌ ПОМИЛКА: Необхідні права адміністратора!", "critical")
                    self.status_label.config(text="❌ ПОМИЛКА: Необхідні права адміністратора!")
                    self.scanning = False
                    return

                self.append_result("╔" + "═" * 70 + "╗", "info")
                self.append_result("║ PTERODO DETECTION ENGINE v15.0".center(72) + "║", "header")
                self.append_result("╚" + "═" * 70 + "   \n", "info")
                self.status_label.config(text="⏳ Комплексне виявлення Pterodo v15...")

                findings = scanner.run_full_scan_v15()

                if not findings:
                    self.append_result("✓ Система чиста - ознак Pterodo не виявлено", "success")
                    self.append_result("\n🟢 РЕЗУЛЬТАТ: БЕЗПЕЧНА СИСТЕМА\n", "success")
                else:
                    critical = [f for f in findings if f.get('score', 0) >= 80 or 'КРИТИЧНО' in str(f.get('risk', ''))]
                    high = [f for f in findings if 70 <= f.get('score', 0) < 80 or 'ВИСОКА' in str(f.get('risk', ''))]
                    medium = [f for f in findings if 50 <= f.get('score', 0) < 70]

                    self.append_result(f"🔴 КРИТИЧНО: {len(critical)}", "critical")
                    self.append_result(f"🟠 ВИСОКА: {len(high)}", "warning")
                    self.append_result(f"🟡 СЕРЕДНЯ: {len(medium)}\n", "info")

                    self.append_result("📋 ДЕТАЛЬНИЙ ЗВІТ (ПОВНА ІНФОРМАЦІЯ):", "header")
                    self.append_result("━" * 70 + "\n", "info")

                    for i, f in enumerate(findings):
                        score = f.get('score', '?')
                        risk = f.get('risk', f.get('type', 'Unknown'))

                        if score >= 80:
                            tag = "critical"
                        elif score >= 70:
                            tag = "warning"
                        else:
                            tag = "info"

                        self.append_result(f"[{i + 1}] ╔═══════════════════════════════════╗", tag)
                        self.append_result(f"    ║ {f.get('type', 'Unknown')[:30]:<30} ║", tag)
                        self.append_result(f"    ╚═══════════════════════════════════╝", tag)
                        self.append_result(f"    Score: {score} | Severity: {risk}\n", tag)

                        # Виведення ВСІ ДЕТАЛЕЙ
                        for key, value in f.items():
                            if key not in ['type', 'risk', 'score', 'indicators']:
                                if isinstance(value, list):
                                    self.append_result(f"    {key}: {', '.join(str(v)[:50] for v in value)}", "info")
                                elif isinstance(value, dict):
                                    self.append_result(f"    {key}: {str(value)[:100]}", "info")
                                else:
                                    self.append_result(f"    {key}: {str(value)[:200]}", "info")

                        if 'full_path' in f:
                            self.append_result(f"    🔴 ПОВНИЙ ШЛЯХ: {f.get('full_path')}", "critical")
                        elif 'path' in f:
                            self.append_result(f"    🔴 ШЛЯХ: {f.get('path')}", "critical")

                        if 'indicators' in f:
                            self.append_result(f"    ⚠️ ІНДИКАТОРИ:", "warning")
                            for ind in f['indicators']:
                                self.append_result(f"       • {ind}", "warning")

                        self.append_result("    " + "─" * 64 + "\n", "info")

                    self.append_result(f"\n🔴 ЗАГАЛЬНИЙ РЕЗУЛЬТАТ: {len(findings)} ЗАГРОЗ ВИЯВЛЕНО!\n", "critical")

                self.status_label.config(text=f"✓ Завершено: {len(findings)} загроз виявлено")
            except Exception as e:
                self.append_result(f"❌ Помилка: {str(e)}", "critical")
                self.status_label.config(text="❌ Помилка сканування")
                import traceback
                logger.error(traceback.format_exc())
            finally:
                self.scanning = False

        threading.Thread(target=scan, daemon=True).start()

    def start_games_scan(self):
        """Запуск сканування на ігри"""
        if self.scanning:
            self.status_label.config(text="⏳ Вже сканується...")
            return

        self.scanning = True
        self.clear_results()

        def scan():
            try:
                self.append_result("╔" + "═" * 70 + "╗", "info")
                self.append_result("║ ПОШУК ІГРОВОГО ПЗ ТА ПЛАТФОРМ".center(72) + "║", "header")
                self.append_result("╚" + "═" * 70 + "╝\n", "info")
                self.status_label.config(text="⏳ Сканування на ігри...")

                findings = GameAndSoftwareScanner.scan_for_games()

                if not findings:
                    self.append_result("✓ Ігри не виявлені", "success")
                else:
                    self.append_result(f"Виявлено: {len(findings)} ігор/платформ\n", "warning")
                    for i, f in enumerate(findings, 1):
                        self.append_result(f"  [{i}] {f.get('software')}", "warning")
                        self.append_result(f"       Тип: {f.get('type', 'N/A')}\n", "info")

                self.status_label.config(text=f"✓ Сканування завершено")
            finally:
                self.scanning = False

        threading.Thread(target=scan, daemon=True).start()

    def start_antivirus_scan(self):
        """Запуск сканування на антивіруси"""
        if self.scanning:
            self.status_label.config(text="⏳ Вже сканується...")
            return

        self.scanning = True
        self.clear_results()

        def scan():
            try:
                self.append_result("╔" + "═" * 70 + "╗", "info")
                self.append_result("║ ВИ  ВЛЕННЯ СТОРОННІХ АНТИВІРУСІВ".center(72) + "║", "header")
                self.append_result("╚" + "═" * 70 + "╝\n", "info")
                self.status_label.config(text="⏳ Сканування антивірусів...")

                findings = GameAndSoftwareScanner.scan_for_third_party_antivirus()

                if not findings:
                    self.append_result("✓ Сторонніх антивірусів не знайдено", "success")
                else:
                    self.append_result(f"Виявлено: {len(findings)} сторонніх антивірусів\n", "warning")
                    for i, f in enumerate(findings, 1):
                        self.append_result(f"  [{i}] {f['software']}", "critical")

                self.status_label.config(text=f"✓ Аналіз завершено")
            finally:
                self.scanning = False

        threading.Thread(target=scan, daemon=True).start()

    def show_system_info(self):
        """Вивід інформації про систему"""
        if self.scanning:
            self.status_label.config(text="⏳ Операція вже års...")
            return

        self.scanning = True
        self.clear_results()
        self.status_label.config(text="⏳ Збір інформації про систему...")
        self.root.update()

        def collect():
            try:
                info = SystemInformationCollector.get_system_info()

                self.append_result("╔" + "═" * 70 + "╗", "info")
                self.append_result("║ТЕХНІЧНА ІНФОРМАЦІЯ ПРО СИСТЕМУ".center(72) + "║", "header")
                self.append_result("╚" + "═" * 70 + "╝\n", "info")

                self.append_result("ОСНОВНА ІНФОРМАЦІЯ:", "header")
                self.append_result(f"  Комп'ютер:      {info.get('computer_name', 'N/A')}", "info")
                self.append_result(f"  Користувач:     {info.get('username', 'N/A')}", "info")
                self.append_result(f"  ОС:             {info.get('os', 'N/A')}", "info")
                self.append_result(f"  IP адреса:      {info.get('ip_address', 'N/A')}", "success")
                self.append_result(f"  Public IP:      {info.get('public_ip', 'N/A')}", "success")
                self.append_result(f"  Шлюз (Router):  {info.get('gateway_ip', 'N/A')}\n", "success")

                self.append_result("ПРОЦЕСОР:", "header")
                self.append_result(f"  Модель:         {info.get('cpu_model', 'UNKNOWN')}", "info")
                self.append_result(f"  Ядра:           {info.get('cpu_cores', 'N/A')}", "info")
                self.append_result(f"  Потоки:         {info.get('cpu_threads', 'N/A')}\n", "info")

                self.append_result("ВИДЕОКАРТА:", "header")
                for gpu in info.get('gpu_models', ['НЕВІДОМО']):
                    self.append_result(f"  {gpu}", "info")
                self.append_result("")

                self.append_result("ОЗУ:", "header")
                self.append_result(f"  Всього:         {info.get('ram_total', 0)} GB", "info")
                self.append_result(f"  Доступна:       {info.get('ram_available', 0)} GB", "success")
                self.append_result(f"  Використання:   {info.get('ram_percent', 0)}%\n", "info")

                self.append_result("MAC АДРЕСИ:", "header")
                mac_addresses = info.get('mac_addresses', [])
                if mac_addresses:
                    for mac in mac_addresses[:10]:
                        status = "🟢 Активний" if mac.get('is_active') else "⚫ Неактивний"
                        self.append_result(f"  {mac.get('interface'):<20} {mac.get('mac')} ({status})", "info")
                else:
                    self.append_result("  Не знайдено", "warning")
                self.append_result("")

                self.append_result("DNS СЕРВЕРИ:", "header")
                dns_servers = info.get('dns_servers', [])
                if dns_servers:
                    for dns in dns_servers:
                        self.append_result(f"  {dns}", "info")
                else:
                    self.append_result("  Не знайдено", "warning")
                self.append_result("")

                self.append_result("РОЗДІЛИ (v15 - MULTI-METHOD DETECTION):", "header")
                for disk in info.get('disks', []):
                    disk_type = disk.get('type', 'Unknown')

                    if "SSD" in disk_type or "NVMe" in disk_type:
                        disk_tag = "disk_ssd"
                        emoji = "⚡"
                    elif "HDD" in disk_type:
                        disk_tag = "disk_hdd"
                        emoji = "💾"
                    else:
                        disk_tag = "info"
                        emoji = "❓"

                    disk_line = (f"  {emoji} {disk['drive']:<5} | "
                                 f"Total: {disk['total']}GB | "
                                 f"Used: {disk['used']}GB ({disk['used_percent']}%) | "
                                 f"Free: {disk['free']}GB ({disk['free_percent']}%) | "
                                 f"Type: {disk_type}")

                    self.append_result(disk_line, disk_tag)
                self.append_result("")

                self.status_label.config(text="✓ Інформація отримана")
            finally:
                self.scanning = False

        threading.Thread(target=collect, daemon=True).start()

    def action_ipnp_iips(self):
        """Очищення журналу браузера через "Властивості браузера" та налаштування Edge IE mode.

        Виконує автоматичну роботу в такій послідовності:
        1) відкриває системну утиліту "Властивості браузера";
        2) на вкладці "Загальні" натискає кнопку "Видалити";
        3) у діалозі "Очищення журналу браузера" знімає "Зберегти дані вподобаних веб-сайтів";
           увімкнює "Тимчасові файли Інтернету", "Файли cookie", "Журнал", "Журнал завантажень",
           "Дані форм", "Паролі";
        4) натискає "Видалити", "Застосувати" і закриває діалог;
        5) відкриває Edge, налаштовує IE mode і додає цільові сторінки.
        """
        if self.scanning:
            self.status_label.config(text="⏳ Операція вже виконується...")
            return

        proceed = messagebox.askyesno("Підтвердження",
                                      "Ви підтверджуєте очищення журналу браузера та зміну налаштувань Edge?")
        if not proceed:
            return

        self.scanning = True
        self.clear_results()
        self.status_label.config(text="⏳ Виконання ІПНП/ІІПС...")

        def work():
            if platform.system() != 'Windows':
                self.append_result('❌ Операція доступна лише на Windows.', 'critical')
                self.scanning = False
                return

            def wait_window(patterns, timeout=20):
                end = time.time() + timeout
                while time.time() < end:
                    hwnd = find_window_by_title(patterns, timeout=1)
                    if hwnd:
                        return hwnd
                    time.sleep(0.2)
                return None

            def debug_window_controls(hwnd):
                try:
                    controls = []
                    for child in enum_child_windows(hwnd):
                        controls.append((get_class_name(child), get_window_text(child)))
                    self.append_result(f'Контроли вікна {hwnd}: {controls}', 'debug')
                except Exception:
                    pass

            main_titles = ['Internet Options', 'Властивості браузера', 'Властивості браузера Internet', 'Властивості']
            delete_titles = ['Delete Browsing History', 'Очищення журналу браузера', 'Delete History', 'Очищення',
                             'Журнал']
            delete_button_labels = ['Delete', 'Видалити']
            apply_button_labels = ['Apply', 'Застосувати']
            ok_button_labels = ['OK', 'ОК', 'Так']
            preserve_labels = ['Preserve favorites', 'Зберегти дані вподобаних веб-сайтів', 'уподобан']
            clear_labels = [
                'Temporary Internet files', 'Тимчасові файли Інтернету',
                'Cookies', 'Файли cookie',
                'History', 'Журнал',
                'Download history', 'Журнал завантажень',
                'Form data', 'Дані форм',
                'Passwords', 'Паролі'
            ]

            try:
                subprocess.Popen(['control', 'inetcpl.cpl'])
            except Exception as e:
                self.append_result(f'Не вдалося відкрити Властивості браузера: {e}', 'critical')
                self.scanning = False
                return

            main_hwnd = wait_window(main_titles, timeout=25)
            if not main_hwnd:
                self.append_result('Не знайдено вікно "Властивості браузера".', 'critical')
                self.scanning = False
                return

            USER32.SetForegroundWindow(main_hwnd)
            self.append_result('Знайдено вікно "Властивості браузера".', 'info')

            delete_btn = find_child_by_text(main_hwnd, delete_button_labels, class_name='Button')
            if not delete_btn:
                debug_window_controls(main_hwnd)
                self.append_result('Не вдалося знайти кнопку "Видалити" у Властивостях браузера.', 'critical')
                self.scanning = False
                return

            if not click_button(delete_btn):
                USER32.PostMessageW(delete_btn, BM_CLICK, 0, 0)
            self.append_result('Натиснуто кнопку "Видалити".', 'info')

            # НОВИЙ ПІДХІД: Чекаємо нове вікно без прив'язки до заголовка
            time.sleep(0.8)
            initial_windows = set(enum_windows())
            delete_hwnd = None
            wait_end = time.time() + 10

            while time.time() < wait_end:
                current_windows = set(enum_windows())
                new_windows = current_windows - initial_windows

                if new_windows:
                    # Шукаємо нове вікно, яке має чекбокси з потрібними мітками
                    for w in new_windows:
                        try:
                            all_children = enum_all_child_windows(w)
                            found_preserve = False
                            found_clear = False

                            for c in all_children:
                                try:
                                    txt = normalize_control_text(get_window_text(c))
                                    if txt:
                                        if any(normalize_control_text(lbl) in txt for lbl in preserve_labels):
                                            found_preserve = True
                                        if any(normalize_control_text(lbl) in txt for lbl in clear_labels):
                                            found_clear = True
                                except Exception:
                                    pass

                            if found_preserve or found_clear:
                                delete_hwnd = w
                                self.append_result(f'Знайдено нове вікно (HWND={delete_hwnd}) з чекбоксами.', 'debug')
                                break
                        except Exception:
                            pass

                    if delete_hwnd:
                        break

                time.sleep(0.3)

            if not delete_hwnd:
                # Фолбек: якщо не знайшли по чекбоксам — беремо перше нове вікно
                new_windows = set(enum_windows()) - initial_windows
                if new_windows:
                    delete_hwnd = list(new_windows)[0]
                    self.append_result(f'Вибір першого нового вікна (HWND={delete_hwnd}) за замовчуванням.', 'warning')

            if not delete_hwnd:
                # Якщо вікно не виділилося як окреме, діалог може бути у складі основного вікна
                self.append_result('Не знайдено окремого вікна очищення. Шукаємо в дочірніх контролях основного вікна.',
                                   'warning')
                delete_hwnd = main_hwnd

            self.append_result('Працюємо з вікном очищення/контейнером.', 'debug')

            # PYWINAUTO ПІДХІД (якщо доступен)
            checkbox_configured = False
            if Application is not None:
                try:
                    time.sleep(0.8)
                    desktop = Desktop(backend='uia')

                    # Спроба знайти вікно за HWND
                    dlg = None
                    try:
                        dlg = desktop.window(handle=delete_hwnd)
                    except Exception:
                        # Якщо це не спрацює — шукаємо по заголовку
                        for w in desktop.windows():
                            try:
                                if w.handle == delete_hwnd:
                                    dlg = w
                                    break
                            except Exception:
                                continue

                    if dlg is not None:
                        self.append_result('pywinauto: Знайдено вікно діалогу.', 'debug')

                        # Спроба знайти і керувати чекбоксами за текстом мітки
                        for lbl in preserve_labels:
                            try:
                                # Ищем CheckBox по тексту
                                chk = dlg.child_window(title_re=f".*{lbl.replace(' ', '.*')}.*",
                                                       control_type='CheckBox')
                                if chk.exists():
                                    state = chk.get_toggle_state()
                                    if state:  # Якщо увімкнена — вимкаємо
                                        chk.toggle()
                                        time.sleep(0.2)
                                        self.append_result(f'pywinauto: Знято "{lbl}"', 'info')
                                        checkbox_configured = True
                                    else:
                                        self.append_result(f'pywinauto: Уже знято "{lbl}"', 'debug')
                                        checkbox_configured = True
                                    break
                            except Exception as e:
                                self.append_result(f'pywinauto: Помилка при поиску "{lbl}": {e}', 'debug')
                                continue

                        for lbl in clear_labels:
                            try:
                                chk = dlg.child_window(title_re=f".*{lbl.replace(' ', '.*')}.*",
                                                       control_type='CheckBox')
                                if chk.exists():
                                    state = chk.get_toggle_state()
                                    if not state:  # Якщо вимкнена — вмикаємо
                                        chk.toggle()
                                        time.sleep(0.2)
                                        self.append_result(f'pywinauto: Увімкнено "{lbl}"', 'info')
                                        checkbox_configured = True
                                    else:
                                        self.append_result(f'pywinauto: Уже увімкнено "{lbl}"', 'debug')
                                        checkbox_configured = True
                            except Exception as e:
                                self.append_result(f'pywinauto: Помилка при пошуку "{lbl}": {e}', 'debug')
                                continue

                    else:
                        self.append_result('pywinauto: Не вдалося знайти вікно за HWND.', 'warning')

                except Exception as e:
                    self.append_result(f'pywinauto: Загальна помилка: {e}', 'warning')
            else:
                self.append_result('pywinauto не встановлено; спроба Win32 API', 'debug')

            # WIN32 FALLBACK (якщо pywinauto не спрацював)
            if not checkbox_configured:
                self.append_result('Win32 API: Спроба керування чекбоксами через контрольні елементи.', 'debug')

                def get_window_rect(hwnd):
                    try:
                        rect = wintypes.RECT()
                        USER32.GetWindowRect(hwnd, ctypes.byref(rect))
                        return rect.left, rect.top, rect.right, rect.bottom
                    except Exception:
                        return (0, 0, 0, 0)

                children = enum_all_child_windows(delete_hwnd)
                statics = []
                buttons = []
                for c in children:
                    try:
                        cls = (get_class_name(c) or '').lower()
                        txt = normalize_control_text(get_window_text(c))
                        rect = get_window_rect(c)
                        if 'static' in cls or cls == '#32770' or cls.startswith('static'):
                            statics.append((c, txt, rect))
                        elif 'button' in cls or cls.startswith('button'):
                            buttons.append((c, txt, rect))
                    except Exception:
                        continue

                found_any = False

                # 1) Надійне зіставлення: шукаємо статичні мітки і підбираємо найближчий чекбокс ліворуч/праворуч
                for s_hwnd, s_txt, s_rect in statics:
                    try:
                        label = s_txt
                        if not label:
                            continue
                        target_type = None
                        if any(normalize_control_text(lbl) in label for lbl in preserve_labels):
                            target_type = 'preserve'
                        elif any(normalize_control_text(lbl) in label for lbl in clear_labels):
                            target_type = 'clear'
                        if not target_type:
                            continue

                        sx1, sy1, sx2, sy2 = s_rect
                        mid_sy = (sy1 + sy2) / 2

                        # знайти найближчий кнопковий чекбокс по вертикалі і побічно по горизонталі
                        closest_btn = None
                        closest_dist = 9999
                        for b_hwnd, b_txt, b_rect in buttons:
                            bx1, by1, bx2, by2 = b_rect
                            mid_by = (by1 + by2) / 2
                            # вертикальна відповідність
                            if abs(mid_by - mid_sy) > 40:
                                continue
                            # горизонтальна відстань між центрами
                            dist = abs(((bx1 + bx2) / 2) - ((sx1 + sx2) / 2))
                            if dist < closest_dist:
                                closest_dist = dist
                                closest_btn = (b_hwnd, b_txt)

                        if closest_btn:
                            b_hwnd, b_txt = closest_btn
                            if target_type == 'preserve':
                                if get_checkbox_state(b_hwnd) == BST_CHECKED:
                                    set_checkbox(b_hwnd, False)
                                    self.append_result(f'Win32: Знято галочку: {label}', 'info')
                                else:
                                    self.append_result(f'Win32: Галочка вже знята: {label}', 'debug')
                            else:
                                if get_checkbox_state(b_hwnd) != BST_CHECKED:
                                    set_checkbox(b_hwnd, True)
                                    self.append_result(f'Win32: Увімкнено галочку: {label}', 'info')
                                else:
                                    self.append_result(f'Win32: Галочка вже увімкнена: {label}', 'debug')
                            found_any = True
                    except Exception:
                        continue

                # 2) Фолбек: якщо нічого не знайдено — пробуємо проаналізувати кнопки і шукати статичні мітки справа
                if not found_any:
                    for btn_hwnd, btn_txt, btn_rect in buttons:
                        try:
                            label = btn_txt
                            bx1, by1, bx2, by2 = btn_rect
                            mid_y = (by1 + by2) / 2
                            if not label:
                                # знайти статичну мітку справа або зліва
                                closest = None
                                closest_dist = 9999
                                for s_hwnd, s_txt, s_rect in statics:
                                    sx1, sy1, sx2, sy2 = s_rect
                                    mid_sy = (sy1 + sy2) / 2
                                    if abs(mid_sy - mid_y) < 40:
                                        # відстань від кнопки до статичної мітки
                                        dist = min(abs(sx1 - bx2), abs(sx2 - bx1))
                                        if dist < closest_dist:
                                            closest_dist = dist
                                            closest = s_txt
                                label = closest or ''

                            if not label:
                                continue

                            if any(normalize_control_text(lbl) in label for lbl in preserve_labels):
                                if get_checkbox_state(btn_hwnd) == BST_CHECKED:
                                    set_checkbox(btn_hwnd, False)
                                    self.append_result(f'Win32 fallback: Знято галочку: {label}', 'info')
                                found_any = True
                            elif any(normalize_control_text(lbl) in label for lbl in clear_labels):
                                if get_checkbox_state(btn_hwnd) != BST_CHECKED:
                                    set_checkbox(btn_hwnd, True)
                                    self.append_result(f'Win32 fallback: Увімкнено галочку: {label}', 'info')
                                found_any = True
                        except Exception:
                            continue

                if not found_any:
                    self.append_result(
                        'Win32: Увага — не вдалося автоматично знайти необхідні чекбокси. Потрібна ручна перевірка.',
                        'warning')

            # Натискаємо кнопку "Видалити" у діалозі очищення
            delete_confirm = find_child_by_text(delete_hwnd, delete_button_labels, class_name='Button')
            if delete_confirm:
                if not click_button(delete_confirm):
                    USER32.PostMessageW(delete_confirm, BM_CLICK, 0, 0)
                self.append_result('Натиснуто кнопку "Видалити" у вікні очищення.', 'info')
                end = time.time() + 15
                while time.time() < end and USER32.IsWindow(delete_hwnd):
                    time.sleep(0.3)
                if USER32.IsWindow(delete_hwnd):
                    self.append_result('Увага: діалог очищення журналу браузера не закрився автоматично.', 'warning')
            else:
                self.append_result('Не знайдено кнопку "Видалити" у вікні очищення.', 'warning')

            time.sleep(1)

            apply_btn = find_child_by_text(main_hwnd, apply_button_labels, class_name='Button')
            if apply_btn:
                if not click_button(apply_btn):
                    USER32.PostMessageW(apply_btn, BM_CLICK, 0, 0)
                self.append_result('Натиснуто кнопку "Застосувати" у властивостях браузера.', 'info')
            else:
                self.append_result('Не знайдено кнопку "Застосувати".', 'warning')

            time.sleep(1)
            ok_btn = find_child_by_text(main_hwnd, ok_button_labels, class_name='Button')
            if ok_btn:
                if not click_button(ok_btn):
                    USER32.PostMessageW(ok_btn, BM_CLICK, 0, 0)
                self.append_result('Натиснуто кнопку "OK" у властивостях браузера.', 'info')
            else:
                self.append_result('Не знайдено кнопку "OK".', 'warning')

            end = time.time() + 10
            while time.time() < end and USER32.IsWindow(main_hwnd):
                time.sleep(0.2)

            try:
                programdata = os.environ.get('PROGRAMDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
                sitelist_path = os.path.join(programdata, 'EdgeSiteList.xml')
                xml_content = ('<?xml version="1.0" encoding="utf-8"?>\n'
                               '<site-list version="1">\n'
                               '  <site url="http://101.index.php"/>\n'
                               '  <site url="http://102.index.php"/>\n'
                               '</site-list>')
                with open(sitelist_path, 'w', encoding='utf-8') as xf:
                    xf.write(xml_content)
                edge_key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\\Policies\\Microsoft\\Edge')
                winreg.SetValueEx(edge_key, 'InternetExplorerIntegrationLevel', 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(edge_key, 'InternetExplorerIntegrationSiteList', 0, winreg.REG_SZ, sitelist_path)
                winreg.CloseKey(edge_key)
                self.append_result('Задано політики Edge для IE mode + site list.', 'info')
            except Exception as e:
                self.append_result(f'Не вдалося налаштувати політики Edge: {e}', 'warning')

            edge_opened = False
            try:
                subprocess.Popen('start microsoft-edge:edge://settings/defaultBrowser', shell=True)
                edge_opened = True
            except Exception as e:
                self.append_result(f'Не вдалося відкрити Edge на сторінці стандартного браузера: {e}', 'warning')

            try:
                subprocess.Popen('start microsoft-edge:http://101.index.php', shell=True)
                subprocess.Popen('start microsoft-edge:http://102.index.php', shell=True)
                self.append_result('Відкрито обидві сторінки в Edge.', 'info')
            except Exception as e:
                self.append_result(f'Не вдалося відкрити цільові сторінки: {e}', 'warning')

            if Application is not None and edge_opened:
                try:
                    time.sleep(6)
                    desktop = Desktop(backend='uia')
                    edge_window = None
                    for w in desktop.windows():
                        title = w.window_text()
                        if title and 'Microsoft Edge' in title:
                            edge_window = w
                            break
                    if edge_window is not None:
                        edge_window.set_focus()
                        self.append_result('Знайдено вікно Edge для автоматизації.', 'info')
                        try:
                            toggle = edge_window.child_window(
                                title_re='.*Internet Explorer.*режим.*|.*Allow sites.*Internet Explorer.*',
                                control_type='CheckBox')
                            if toggle.exists():
                                if not toggle.get_toggle_state():
                                    toggle.toggle()
                                    self.append_result('Увімкнено дозвіл IE mode у налаштуваннях Edge.', 'info')
                            else:
                                self.append_result('Не знайдено чекбокс для увімкнення IE mode.', 'warning')
                        except Exception:
                            self.append_result('Автоматизація вмикання IE mode у Edge не вдалася.', 'warning')
                        for page in ['http://101.index.php', 'http://102.index.php']:
                            try:
                                add_btn = edge_window.child_window(title_re='.*Add a page.*|.*Додавання сторінки.*',
                                                                   control_type='Button')
                                if add_btn.exists():
                                    add_btn.click_input()
                                    time.sleep(1)
                                    send_keys(page)
                                    send_keys('{ENTER}')
                                    self.append_result(f'Додано сторінку у IE mode: {page}', 'info')
                                    time.sleep(1)
                                else:
                                    self.append_result('Не знайдено кнопку "Додавання сторінки" у Edge.', 'warning')
                                    break
                            except Exception:
                                self.append_result(f'Не вдалося додати сторінку у Edge: {page}', 'warning')
                    else:
                        self.append_result('Не вдалося знайти вікно Edge для автоматизації.', 'warning')
                except Exception as e:
                    self.append_result(f'Не вдалося автоматизувати Edge UI: {e}', 'warning')
            else:
                if Application is None:
                    self.append_result('pywinauto не встановлено; Edge UI автоматизацію пропущено.', 'warning')

            self.append_result('Операція ІПНП/ІІПС завершена. Перевірте Edge та Internet Options.', 'success')
            self.scanning = False

        threading.Thread(target=work, daemon=True).start()

    def start_full_scan_sequential(self):
        """Повне послідовне сканування"""
        if self.scanning:
            self.status_label.config(text="⏳ Вже сканується...")
            return

        self.scanning = True
        self.clear_results()

        def full_sequential_scan():
            try:
                # КРОК 1: Система
                self.append_result("════════════════════════════════════════════════════════════════════", "header")
                self.append_result("КРОК 1: СИСТЕМА", "header")
                self.append_result("════════════════════════════════════════════════════════════════════\n", "header")

                info = SystemInformationCollector.get_system_info()

                self.append_result("╔" + "═" * 70 + "╗", "info")
                self.append_result("║ СИСТЕМНА ІНФОРМАЦІЯ".center(72) + "║", "header")
                self.append_result("╚" + "═" * 70 + "╝\n", "info")

                self.append_result("ОСНОВНА ІНФОРМАЦІЯ:", "header")
                self.append_result(f"  Комп'ютер: {info.get('computer_name', 'N/A')}", "info")
                self.append_result(f"  ОС: {info.get('os', 'N/A')}", "info")
                self.append_result(f"  CPU: {info.get('cpu_model', 'N/A')}", "info")
                self.append_result(f"  RAM: {info.get('ram_total', 0)} GB\n", "info")

                self.append_result("DNS СЕРВЕРИ:", "header")
                dns_servers = info.get('dns_servers', [])
                if dns_servers:
                    for dns in dns_servers:
                        self.append_result(f"  • {dns}", "info")
                else:
                    self.append_result("  Не знайдено", "warning")
                self.append_result("")

                self.append_result("РОЗДІЛИ (v15.0):", "header")
                for disk in info.get('disks', []):
                    disk_type = disk.get('type', 'Unknown')
                    if "SSD" in disk_type or "NVMe" in disk_type:
                        disk_tag = "disk_ssd"
                        emoji = "⚡"
                    else:
                        disk_tag = "disk_hdd"
                        emoji = "💾"

                    disk_line = (f"  {emoji} {disk['drive']:<5} | Total: {disk['total']}GB | "
                                 f"Used: {disk['used']}GB ({disk['used_percent']}%) | Type: {disk_type}")
                    self.append_result(disk_line, disk_tag)

                # КРОК 2: Pterodo v15
                self.append_result("\n\n════════════════════════════════════════════════════════════════════", "header")
                self.append_result("КРОК 2: PTERODO DETECTION v15.0", "header")
                self.append_result("════════════════════════════════════════════════════════════════════\n", "header")

                self.status_label.config(text="⏳ Pterodo сканування (це може зайняти час)...")
                self.root.update()

                scanner = PterodoScannerV15()
                pterodo_findings = scanner.run_full_scan_v15()

                self.append_result("╔" + "═" * 70 + "╗", "info")
                self.append_result("║ PTERODO".center(72) + "║", "header")
                self.append_result("╚" + "═" * 70 + "╝\n", "info")

                if not pterodo_findings:
                    self.append_result("✓ Pterodo не виявлено", "success")
                else:
                    critical = [f for f in pterodo_findings if
                                f.get('score', 0) >= 80 or 'КРИТИЧНО' in str(f.get('risk', ''))]
                    high = [f for f in pterodo_findings if 70 <= f.get('score', 0) < 80]

                    self.append_result(f"🔴 КРИТИЧНО: {len(critical)}", "critical")
                    self.append_result(f"🟠 ВИСОКА: {len(high)}\n", "warning")

                    for i, f in enumerate(pterodo_findings[:15]):
                        self.append_result(f"  [{i + 1}] {f.get('type')}: {f.get('risk', 'N/A')}", "critical")
                        if 'full_path' in f:
                            self.append_result(f"       🔴 Path: {f.get('full_path')[:80]}", "critical")

                # КРОК 3: Ігри
                self.append_result("\n\n════════════════════════════════════════════════════════════════════", "header")
                self.append_result("КРОК 3: ІГРИ", "header")
                self.append_result("════════════════════════════════════════════════════════════════════\n", "header")

                self.status_label.config(text="⏳ Сканування ігор...")
                self.root.update()

                game_findings = GameAndSoftwareScanner.scan_for_games()

                self.append_result("╔" + "═" * 70 + "╗", "info")
                self.append_result("║ ІГРИ ВИЯВЛЕНО".center(72) + "║", "header")
                self.append_result("╚" + "═" * 70 + "╝\n", "info")

                if not game_findings:
                    self.append_result("✓ Ігри не виявлені", "success")
                else:
                    self.append_result(f"Виявлено: {len(game_findings)} ігор/платформ\n", "warning")
                    for game in game_findings[:15]:
                        self.append_result(f"  • {game.get('software')}", "warning")

                # КРОК 4: Антивіруси
                self.append_result("\n\n════════════════════════════════════════════════════════════════════", "header")
                self.append_result("КРОК 4: АНТИВІРУСИ", "header")
                self.append_result("════════════════════════════════════════════════════════════════════\n", "header")

                self.status_label.config(text="⏳ Сканування антивірусів...")
                self.root.update()

                av_findings = GameAndSoftwareScanner.scan_for_third_party_antivirus()

                self.append_result("╔" + "═" * 70 + "╗", "info")
                self.append_result("║ АНТИВІРУСИ ВИЯВЛЕНО".center(72) + "║", "header")
                self.append_result("╚" + "═" * 70 + "╝\n", "info")

                if not av_findings:
                    self.append_result("✓ Сторонніх антивірусів не знайдено", "success")
                else:
                    self.append_result(f"Виявлено: {len(av_findings)} антивірусів\n", "warning")
                    for av in av_findings:
                        self.append_result(f"  • {av.get('software')}", "critical")

                # ФІНАЛ
                self.append_result("\n\n════════════════════════════════════════════════════════════════════", "header")
                self.append_result("✓ ПОВНЕ СКАНУВАННЯ ЗАВЕРШЕНО", "success")
                self.append_result("════════════════════════════════════════════════════════════════════", "header")

                self.status_label.config(text="✓ Повне сканування завершено!")

            except Exception as e:
                self.append_result(f"\n❌ Помилка при повному скануванні: {str(e)}", "critical")
                self.status_label.config(text="❌ Помилка")
                import traceback
                logger.error(traceback.format_exc())
            finally:
                self.scanning = False

        threading.Thread(target=full_sequential_scan, daemon=True).start()

    def save_report(self):
        """Збереження звіту в файл"""
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Текстові файли", "*.txt"), ("Всі файли", "*.*")],
            initialfile=f"pterodo_report_{dt.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )

        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(self.result_text.get(1.0, tk.END))
                messagebox.showinfo("Успіх", f"Звіт збережено: {filename}")
                self.status_label.config(text=f"✓ Звіт збережен: {filename}")
            except Exception as e:
                messagebox.showerror("Помилка", f"Помилка при збереженні: {str(e)}")
                self.status_label.config(text="❌ Помилка збереження")

    def clear_results(self):
        """Очистка області результатів"""
        self.result_text.delete(1.0, tk.END)


# ══════════════════════════════════════════════════════════════════
# ТОЧКА ВХОДУ ПРОГРАМИ (MAIN)
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # КРОК 1: Запросити права адміністратора якщо їх немає
    if platform.system() == "Windows":
        if not ctypes.windll.shell32.IsUserAnAdmin():
            print("📋 Програма вимагає прав адміністратора...")
            print("⏳ Перезапуск з правами адміністратора...")
            request_admin_privileges()
            sys.exit(0)

    # КРОК 2: Мінімальна ініціалізація
    print("🔍 UAC-0010 SCANNER v15.0")
    print("⏳ Запуск...\n")

    # КРОК 3: Відкрити GUI БЕЗ ЗАТРИМОК
    try:
        root = tk.Tk()

        # ВАЖЛИВО: Встановити вікно ВИДИМИМ МИТТЄВО
        root.withdraw()  # Приховати спочатку

        # ОПТИМІЗАЦІЯ: Не чекати на операції системи
        app = PterodoForensicGUIApp(root)

        # ПОКАЗАТИ вікно
        root.deiconify()

        # Запустити GUI
        root.mainloop()
    except Exception as e:
        print(f"❌ Помилка: {e}")
        import traceback

        traceback.print_exc()
