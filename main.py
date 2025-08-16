import os
import sys
import subprocess
import threading
import time
import tempfile
import shutil
import requests
import zipfile
import platform
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QTextEdit, QProgressBar,
    QTabWidget, QGroupBox, QFileDialog, QMessageBox, QTreeWidget,
    QTreeWidgetItem, QInputDialog, QLineEdit, QComboBox, QCheckBox,
    QDialog, QProgressDialog
)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal, QObject, QSize
from PyQt5.QtGui import QIcon, QColor, QFont, QPixmap

# ====================== CONSTANTS ======================
APP_NAME = "iDevice Manager for Windows"
VERSION = "1.0.0"
WINDOWS_BIN_DIR = os.path.join(os.getenv('ProgramFiles'), 'iDeviceManager', 'bin')
TOOL_URLS = {
    'ideviceinfo.exe': 'https://github.com/libimobiledevice-win32/imobiledevice-net/releases/download/v1.3.0/imobiledevice-net.zip',
    'ifuse.exe': 'https://github.com/libimobiledevice/ifuse/releases/download/v1.1.1/ifuse-windows.zip',
    'ideviceinstaller.exe': 'https://github.com/libimobiledevice/ideviceinstaller/releases/download/1.1.1/ideviceinstaller-win32.zip',
    'idevice_id.exe': 'https://github.com/libimobiledevice-win32/imobiledevice-net/releases/download/v1.3.0/imobiledevice-net.zip',
    'idevicebackup2.exe': 'https://github.com/libimobiledevice/idevicebackup2/releases/download/1.0.0/idevicebackup2-windows.zip',
    'idevicediagnostics.exe': 'https://github.com/libimobiledevice/idevicediagnostics/releases/download/1.0.0/idevicediagnostics-windows.zip',
    'idevicescreenshot.exe': 'https://github.com/libimobiledevice/idevicescreenshot/releases/download/1.0.0/idevicescreenshot-windows.zip'
}

# ====================== DEPENDENCY MANAGER ======================
class DependencyManager:
    def __init__(self):
        self.ensure_bin_directory()
        self.check_dependencies()

    def ensure_bin_directory(self):
        """Create binary directory if it doesn't exist"""
        if not os.path.exists(WINDOWS_BIN_DIR):
            os.makedirs(WINDOWS_BIN_DIR)

    def check_dependencies(self):
        """Check if all required tools are available"""
        missing_tools = []
        for tool in TOOL_URLS.keys():
            tool_path = os.path.join(WINDOWS_BIN_DIR, tool)
            if not os.path.exists(tool_path):
                missing_tools.append(tool)

        if missing_tools:
            self.download_tools(missing_tools)

    def download_tools(self, tools):
        """Download and extract required tools"""
        for tool in tools:
            try:
                self.download_and_extract_tool(tool)
            except Exception as e:
                QMessageBox.critical(
                    None, "Dependency Error",
                    f"Failed to install {tool}:\n{str(e)}\n\n"
                    f"Please download manually from:\n{TOOL_URLS[tool]}"
                )

    def download_and_extract_tool(self, tool_name):
        """Download and extract a single tool"""
        url = TOOL_URLS[tool_name]
        zip_name = os.path.basename(url)
        zip_path = os.path.join(WINDOWS_BIN_DIR, zip_name)

        # Download the tool
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Extract the tool
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(WINDOWS_BIN_DIR)

        # Clean up
        os.remove(zip_path)

    def get_tool_path(self, tool_name):
        """Get full path to a tool"""
        return os.path.join(WINDOWS_BIN_DIR, tool_name)

# ====================== DEVICE MANAGER ======================
class DeviceSignals(QObject):
    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int)
    device_update_signal = pyqtSignal(dict)

class DeviceManager:
    def __init__(self, dep_manager):
        self.dep_manager = dep_manager
        self.signals = DeviceSignals()
        self.udid = None
        self.device_info = {}
        self.mount_point = "Z:\\"
        self.operation_lock = threading.Lock()

    def run_command(self, command, args=None, timeout=30):
        """Run a command with proper error handling"""
        if args is None:
            args = []
            
        try:
            cmd_path = self.dep_manager.get_tool_path(command)
            if not os.path.exists(cmd_path):
                self.signals.log_signal.emit(f"Tool not found: {command}", "error")
                return None

            result = subprocess.run(
                [cmd_path] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return result
        except subprocess.TimeoutExpired:
            self.signals.log_signal.emit(f"Command timed out: {command}", "error")
            return None
        except Exception as e:
            self.signals.log_signal.emit(f"Command failed: {command} - {str(e)}", "error")
            return None

    def check_connection(self):
        """Check if device is connected and get info"""
        with self.operation_lock:
            # Get UDID first
            result = self.run_command("idevice_id.exe", ["-l"])
            if not result or not result.stdout.strip():
                self.signals.device_update_signal.emit({})
                return False

            self.udid = result.stdout.strip().split('\n')[0]

            # Get detailed info
            info_result = self.run_command("ideviceinfo.exe", ["-u", self.udid])
            if info_result and info_result.returncode == 0:
                device_info = {}
                for line in info_result.stdout.splitlines():
                    if ":" in line:
                        key, val = line.split(":", 1)
                        device_info[key.strip()] = val.strip()

                # Get battery info if available
                battery_result = self.run_command(
                    "idevicediagnostics.exe",
                    ["ioregentry", "AppleSmartBattery"]
                )
                if battery_result and "CurrentCapacity" in battery_result.stdout:
                    for line in battery_result.stdout.splitlines():
                        if "CurrentCapacity" in line:
                            device_info["BatteryLevel"] = line.split("=")[1].strip()

                self.signals.device_update_signal.emit(device_info)
                return True

            self.signals.device_update_signal.emit({})
            return False

    def mount_device(self):
        """Mount device filesystem"""
        with self.operation_lock:
            if not self.udid:
                return False

            # Unmount first if already mounted
            self.unmount_device()

            result = self.run_command(
                "ifuse.exe",
                [self.mount_point, "--udid", self.udid]
            )
            return result.returncode == 0 if result else False

    def unmount_device(self):
        """Unmount device filesystem"""
        result = self.run_command("fusermount.exe", ["-u", self.mount_point])
        return result.returncode == 0 if result else False

# ====================== MAIN APPLICATION ======================
class iDeviceManager(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Check platform
        if not sys.platform.startswith('win'):
            QMessageBox.critical(
                None, "Unsupported Platform",
                "This application only runs on Windows."
            )
            sys.exit(1)

        # Initialize dependencies
        self.dep_manager = DependencyManager()
        self.device_mgr = DeviceManager(self.dep_manager)
        
        # Setup UI
        self.setup_ui()
        self.setup_signals()
        
        # Start device monitoring
        self.start_device_monitoring()

    def setup_ui(self):
        """Initialize main window UI"""
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setGeometry(100, 100, 1200, 800)
        
        # Try to set window icon
        try:
            self.setWindowIcon(QIcon("icons/app.ico"))
        except:
            pass

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QHBoxLayout()
        central_widget.setLayout(main_layout)
        
        # Create sidebar
        self.create_sidebar(main_layout)
        
        # Create content area
        self.create_content_area(main_layout)
        
        # Apply dark theme by default
        self.apply_dark_theme()

    def apply_dark_theme(self):
        """Set dark theme for the application"""
        dark_palette = QApplication.palette()
        
        # Base colors
        dark_palette.setColor(dark_palette.Window, QColor(53, 53, 53))
        dark_palette.setColor(dark_palette.WindowText, Qt.white)
        dark_palette.setColor(dark_palette.Base, QColor(25, 25, 25))
        dark_palette.setColor(dark_palette.AlternateBase, QColor(53, 53, 53))
        
        # Text colors
        dark_palette.setColor(dark_palette.Text, Qt.white)
        dark_palette.setColor(dark_palette.ButtonText, Qt.white)
        
        # Button colors
        dark_palette.setColor(dark_palette.Button, QColor(53, 53, 53))
        
        # Highlight colors
        dark_palette.setColor(dark_palette.Highlight, QColor(42, 130, 218))
        dark_palette.setColor(dark_palette.HighlightedText, Qt.black)
        
        # Tooltip colors
        dark_palette.setColor(dark_palette.ToolTipBase, Qt.white)
        dark_palette.setColor(dark_palette.ToolTipText, Qt.black)
        
        QApplication.setPalette(dark_palette)

    def setup_signals(self):
        """Connect signals to slots"""
        self.device_mgr.signals.log_signal.connect(self.log_message)
        self.device_mgr.signals.progress_signal.connect(self.update_progress)
        self.device_mgr.signals.device_update_signal.connect(self.update_device_info)

    def create_sidebar(self, parent_layout):
        """Create the sidebar navigation"""
        sidebar = QWidget()
        sidebar.setStyleSheet("""
            background-color: #2c3e50;
            color: white;
            padding: 10px;
        """)
        sidebar_layout = QVBoxLayout()
        sidebar.setLayout(sidebar_layout)
        
        # Logo
        logo = QLabel(APP_NAME)
        logo.setStyleSheet("""
            font-size: 18px;
            font-weight: bold;
            padding: 20px;
            qproperty-alignment: AlignCenter;
        """)
        sidebar_layout.addWidget(logo)
        
        # Version label
        version_label = QLabel(f"v{VERSION}")
        version_label.setStyleSheet("""
            font-size: 12px;
            qproperty-alignment: AlignCenter;
            padding-bottom: 20px;
        """)
        sidebar_layout.addWidget(version_label)
        
        # Device status
        self.device_status = QLabel("No device connected")
        self.device_status.setAlignment(Qt.AlignCenter)
        self.device_status.setStyleSheet("""
            font-size: 14px;
            padding-bottom: 20px;
        """)
        sidebar_layout.addWidget(self.device_status)
        
        # Navigation buttons
        nav_buttons = [
            ("Dashboard", "home", self.show_dashboard),
            ("Flash & JB", "bolt", self.show_flash_jb),
            ("Apps", "th-large", self.show_apps),
            ("Files", "folder", self.show_files),
            ("Backup", "save", self.show_backup),
            ("Toolbox", "wrench", self.show_toolbox),
            ("Settings", "cog", self.show_settings)
        ]
        
        for text, icon_name, handler in nav_buttons:
            btn = QPushButton(text)
            try:
                btn.setIcon(QIcon.fromTheme(icon_name))
            except:
                # Fallback icon
                btn.setIcon(QIcon("icons/app.png"))
                
            btn.setStyleSheet("""
                QPushButton {
                    text-align: left;
                    padding: 12px;
                    border: none;
                    color: white;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background-color: #3498db;
                }
            """)
            btn.clicked.connect(handler)
            sidebar_layout.addWidget(btn)
        
        sidebar_layout.addStretch()
        parent_layout.addWidget(sidebar, stretch=1)

    def create_content_area(self, parent_layout):
        """Create the main content area"""
        content = QWidget()
        content_layout = QVBoxLayout()
        content.setLayout(content_layout)
        
        # Device status bar
        status_bar = QWidget()
        status_layout = QHBoxLayout()
        status_bar.setLayout(status_layout)
        
        self.device_name_label = QLabel("No device connected")
        self.device_name_label.setStyleSheet("font-weight: bold;")
        
        self.device_model_label = QLabel()
        self.ios_version_label = QLabel()
        self.battery_label = QLabel()
        
        status_layout.addWidget(self.device_name_label)
        status_layout.addWidget(self.device_model_label)
        status_layout.addWidget(self.ios_version_label)
        status_layout.addWidget(self.battery_label)
        status_layout.addStretch()
        
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_device)
        status_layout.addWidget(refresh_btn)
        
        content_layout.addWidget(status_bar)
        
        # Tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.North)
        
        # Create tabs
        self.create_dashboard_tab()
        self.create_flash_jb_tab()
        self.create_apps_tab()
        self.create_files_tab()
        self.create_backup_tab()
        self.create_toolbox_tab()
        self.create_settings_tab()
        
        content_layout.addWidget(self.tab_widget)
        
        # Log output
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("""
            background-color: #1a202c;
            color: #cbd5e0;
            font-family: Consolas;
            font-size: 12px;
            border: 1px solid #2d3748;
        """)
        content_layout.addWidget(self.log_output, stretch=1)
        
        parent_layout.addWidget(content, stretch=4)

    def create_dashboard_tab(self):
        """Create dashboard tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        
        # Device info group
        device_group = QGroupBox("Device Information")
        device_layout = QVBoxLayout()
        device_group.setLayout(device_layout)
        
        self.device_info_label = QLabel("Connect an iOS device to view information")
        self.device_info_label.setWordWrap(True)
        device_layout.addWidget(self.device_info_label)
        
        # Quick actions
        quick_actions = QHBoxLayout()
        screenshot_btn = QPushButton("Take Screenshot")
        screenshot_btn.clicked.connect(self.take_screenshot)
        reboot_btn = QPushButton("Reboot Device")
        reboot_btn.clicked.connect(self.reboot_device)
        quick_actions.addWidget(screenshot_btn)
        quick_actions.addWidget(reboot_btn)
        device_layout.addLayout(quick_actions)
        
        # System status
        status_group = QGroupBox("System Status")
        status_layout = QVBoxLayout()
        status_group.setLayout(status_layout)
        
        # Storage
        storage_group = QGroupBox("Storage")
        storage_layout = QVBoxLayout()
        storage_group.setLayout(storage_layout)
        self.storage_progress = QProgressBar()
        storage_layout.addWidget(QLabel("Used Space:"))
        storage_layout.addWidget(self.storage_progress)
        
        # Battery
        battery_group = QGroupBox("Battery")
        battery_layout = QVBoxLayout()
        battery_group.setLayout(battery_layout)
        self.battery_progress = QProgressBar()
        battery_layout.addWidget(QLabel("Battery Level:"))
        battery_layout.addWidget(self.battery_progress)
        
        status_layout.addWidget(storage_group)
        status_layout.addWidget(battery_group)
        
        layout.addWidget(device_group)
        layout.addWidget(status_group)
        layout.addStretch()
        
        self.tab_widget.addTab(tab, "Dashboard")

    def create_flash_jb_tab(self):
        """Create Flash & Jailbreak tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        
        # Flash section
        flash_group = QGroupBox("Firmware Operations")
        flash_layout = QVBoxLayout()
        flash_group.setLayout(flash_layout)
        
        self.firmware_list = QListWidget()
        self.populate_firmware_list()
        self.flash_progress = QProgressBar()
        flash_btn = QPushButton("Flash Firmware")
        flash_btn.clicked.connect(self.start_flash)
        
        flash_layout.addWidget(QLabel("Available Firmware:"))
        flash_layout.addWidget(self.firmware_list)
        flash_layout.addWidget(self.flash_progress)
        flash_layout.addWidget(flash_btn)
        
        # Jailbreak section
        jb_group = QGroupBox("Jailbreak Operations")
        jb_layout = QVBoxLayout()
        jb_group.setLayout(jb_layout)
        
        self.jb_list = QListWidget()
        self.jb_list.addItems(["checkra1n", "unc0ver", "Taurine", "palera1n"])
        self.jb_progress = QProgressBar()
        jb_btn = QPushButton("Jailbreak Device")
        jb_btn.clicked.connect(self.start_jailbreak)
        
        jb_layout.addWidget(QLabel("Available Jailbreaks:"))
        jb_layout.addWidget(self.jb_list)
        jb_layout.addWidget(self.jb_progress)
        jb_layout.addWidget(jb_btn)
        
        layout.addWidget(flash_group)
        layout.addWidget(jb_group)
        layout.addStretch()
        
        self.tab_widget.addTab(tab, "Flash & JB")

    def populate_firmware_list(self):
        """Populate firmware list (simulated)"""
        self.firmware_list.clear()
        firmwares = [
            "iOS 16.6 (20G75)",
            "iOS 15.7.8 (19H364)",
            "iOS 17.0 Beta 3 (21A5277h)"
        ]
        self.firmware_list.addItems(firmwares)

    def start_flash(self):
        """Start firmware flashing process"""
        if not self.device_mgr.udid:
            self.log_message("No device connected", "error")
            return
            
        selected = self.firmware_list.currentItem()
        if not selected:
            self.log_message("Select firmware first", "warning")
            return
            
        firmware = selected.text()
        
        reply = QMessageBox.question(
            self, "Confirm Flash",
            f"Flash {firmware}? This cannot be undone!",
            QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.No:
            return
            
        self.log_message(f"Starting {firmware} flash...", "info")
        
        # Disable UI during operation
        self.tab_widget.setEnabled(False)
        
        # Start flash in background thread
        self.flash_thread = threading.Thread(
            target=self.run_flash,
            args=(firmware,),
            daemon=True
        )
        self.flash_thread.start()

    def run_flash(self, firmware):
        """Simulate firmware flashing"""
        try:
            steps = [
                (10, "Preparing device..."),
                (25, "Entering recovery mode..."),
                (50, "Downloading firmware..."),
                (75, "Verifying firmware..."),
                (90, "Flashing device..."),
                (100, "Flash complete!")
            ]
            
            for progress, message in steps:
                time.sleep(1)
                self.device_mgr.signals.progress_signal.emit(progress)
                self.log_message(message, "info")
                
            self.log_message(f"{firmware} flashed successfully!", "success")
        except Exception as e:
            self.log_message(f"Flash failed: {str(e)}", "error")
        finally:
            # Re-enable UI
            self.tab_widget.setEnabled(True)

    def start_jailbreak(self):
        """Start jailbreak process"""
        if not self.device_mgr.udid:
            self.log_message("No device connected", "error")
            return
            
        selected = self.jb_list.currentItem()
        if not selected:
            self.log_message("Select jailbreak tool first", "warning")
            return
            
        tool = selected.text()
        
        reply = QMessageBox.question(
            self, "Confirm Jailbreak",
            f"Run {tool} jailbreak? This may void your warranty!",
            QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.No:
            return
            
        self.log_message(f"Starting {tool} jailbreak...", "info")
        
        # Disable UI during operation
        self.tab_widget.setEnabled(False)
        
        # Start jailbreak in background thread
        self.jb_thread = threading.Thread(
            target=self.run_jailbreak,
            args=(tool,),
            daemon=True
        )
        self.jb_thread.start()

    def run_jailbreak(self, tool):
        """Simulate jailbreak process"""
        try:
            steps = [
                (10, "Preparing device..."),
                (30, "Exploiting vulnerability..."),
                (60, "Installing jailbreak..."),
                (90, "Finalizing..."),
                (100, "Jailbreak complete!")
            ]
            
            for progress, message in steps:
                time.sleep(1)
                self.device_mgr.signals.progress_signal.emit(progress)
                self.log_message(message, "info")
                
            self.log_message(f"{tool} jailbreak successful!", "success")
        except Exception as e:
            self.log_message(f"Jailbreak failed: {str(e)}", "error")
        finally:
            # Re-enable UI
            self.tab_widget.setEnabled(True)

    # [Additional tab creation methods...]
    # create_apps_tab(), create_files_tab(), create_backup_tab(), etc.

    def start_device_monitoring(self):
        """Start periodic device monitoring"""
        self.device_timer = QTimer(self)
        self.device_timer.timeout.connect(self.refresh_device)
        self.device_timer.start(3000)  # Check every 3 seconds

    def refresh_device(self):
        """Refresh device connection status"""
        self.device_mgr.check_connection()

    def update_device_info(self, device_info):
        """Update UI with device information"""
        if device_info:
            self.device_info = device_info
            
            # Update status bar
            name = device_info.get("DeviceName", "Unknown Device")
            model = device_info.get("ProductType", "Unknown Model")
            version = device_info.get("ProductVersion", "Unknown Version")
            battery = device_info.get("BatteryLevel", "N/A")
            
            self.device_name_label.setText(name)
            self.device_model_label.setText(f"Model: {model}")
            self.ios_version_label.setText(f"iOS: {version}")
            self.battery_label.setText(f"Battery: {battery}%")
            
            # Update dashboard
            info_text = f"""
                <b>Device Name:</b> {name}<br>
                <b>Model:</b> {model}<br>
                <b>iOS Version:</b> {version}<br>
                <b>UDID:</b> {self.device_mgr.udid}<br>
                <b>Battery:</b> {battery}%
            """
            self.device_info_label.setText(info_text)
            
            # Update progress bars
            try:
                self.battery_progress.setValue(int(battery))
            except:
                pass
                
            # Update status indicator
            self.device_status.setText("✔ Device Connected")
            self.device_status.setStyleSheet("color: #2ecc71;")
            
            self.log_message(f"Connected: {name} ({model})", "success")
        else:
            self.device_name_label.setText("No device connected")
            self.device_model_label.setText("")
            self.ios_version_label.setText("")
            self.battery_label.setText("")
            
            self.device_info_label.setText("Connect an iOS device to view information")
            self.device_status.setText("✖ No Device")
            self.device_status.setStyleSheet("color: #e74c3c;")
            
            self.log_message("Device disconnected", "warning")

    def update_progress(self, value):
        """Update progress bars"""
        self.flash_progress.setValue(value)
        self.jb_progress.setValue(value)

    def log_message(self, message, level="info"):
        """Add message to log with colored formatting"""
        colors = {
            "info": "#63b3ed",
            "success": "#68d391",
            "warning": "#faf089",
            "error": "#fc8181"
        }
        
        color = colors.get(level, "white")
        timestamp = time.strftime("%H:%M:%S")
        
        html = f"""
        <div style="margin-bottom: 5px;">
            <span style="color: #718096;">[{timestamp}]</span>
            <span style="color: {color};">{level.upper()}:</span>
            <span>{message}</span>
        </div>
        """
        
        self.log_output.insertHtml(html)
        self.log_output.ensureCursorVisible()

    # [Additional methods for other functionality...]
    # take_screenshot(), reboot_device(), etc.

    def closeEvent(self, event):
        """Handle application close"""
        # Clean up any mounted filesystems
        if hasattr(self, 'device_mgr'):
            self.device_mgr.unmount_device()
        
        # Stop device monitoring
        if hasattr(self, 'device_timer'):
            self.device_timer.stop()
        
        event.accept()

# ====================== APPLICATION ENTRY POINT ======================
if __name__ == "__main__":
    # Check if running on Windows
    if not sys.platform.startswith('win'):
        print("This application only runs on Windows.")
        sys.exit(1)

    # Initialize Qt application
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    # Create and show main window
    window = iDeviceManager()
    window.show()
    
    # Run application
    sys.exit(app.exec_())
