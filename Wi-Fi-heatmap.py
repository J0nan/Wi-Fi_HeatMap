import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from PIL import Image, ImageTk
import subprocess
import platform
import re
import time
import json
import base64
import io
import numpy as np
import scipy.interpolate
import matplotlib
import logging
import argparse
import sys

try:
    import pywifi
    HAS_PYWIFI = True
except ImportError:
    HAS_PYWIFI = False

# Setup argument parser for configuration
parser = argparse.ArgumentParser(description="Wi-Fi Heatmap Generator")
parser.add_argument('--log-level', default='WARNING', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], help='Set the logging level')
args, unknown = parser.parse_known_args()

logging.basicConfig(level=getattr(logging, args.log_level), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout, force=True)
logger = logging.getLogger('WiFiHeatmap')

# Silence external libraries unless explicitly requested
if args.log_level != 'DEBUG':
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('pywifi').setLevel(logging.WARNING)

matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.colors import LinearSegmentedColormap
import os

def channel_to_freq(channel):
    """Convert Wi-Fi channel to theoretical center frequency in MHz."""
    try:
        ch = int(channel)
        logger.debug(f"Converting channel {ch} to frequency...")

        if ch == 14:
            return 2484.0
        elif 1 <= ch <= 13:
            return 2407.0 + (ch * 5)
        elif 36 <= ch <= 165:
            return 5000.0 + (ch * 5)
        elif 1 <= ch <= 233:
            return 5950.0 + (ch * 5)
        else:
            return None
    except (TypeError, ValueError):
        return None

class WifiHeatmapApp:
    def __init__(self, root):
        logger.info("Initializing Wi-Fi Heatmap Application...")
        self.root = root
        self.root.title("Wi-Fi Heatmap Generator")
        self.root.geometry("1000x700")
        
        # Configure a simple nice theme
        style = ttk.Style()
        style.theme_use('clam')

        self.os_name = platform.system()
        logger.info(f"Detected Operating System: {self.os_name}")
        
        self.image_path = None
        self.original_image = None
        self.img_width = 0
        self.img_height = 0
        
        self.calibration_points =[]
        self.pixels_per_meter = None
        
        self.measurements =[]
        
        self.state = 'IDLE' 
        
        self.selected_interface = tk.StringVar()
        self.selected_ssid = tk.StringVar()
        self.interfaces_map = {}
        
        self.setup_ui()
        logger.info("UI successfully setup.")
        self.load_interfaces()
        
        # Handle window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    def on_closing(self):
        logger.info("User requested to close the application. Prompting confirmation...")
        if messagebox.askyesno("Quit", "Are you sure you want to exit? Any unsaved progress will be lost."):
            logger.info("Exit confirmed. Shutting down application.")
            self.root.destroy()
            self.root.quit()
        else:
            logger.info("Exit cancelled by user.")

    def setup_ui(self):
        # Sidebar
        self.sidebar = tk.Frame(self.root, width=250, bg='#f4f4f4', padx=15, pady=15, relief=tk.RIDGE, bd=1)
        self.sidebar.pack_propagate(False)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        
        title_font = ('Helvetica', 11, 'bold')
        
        # Interface Selection
        tk.Label(self.sidebar, text="1. Select Interface:", bg='#f4f4f4', font=title_font).pack(anchor='w', pady=(0, 5))
        self.interface_combo = ttk.Combobox(self.sidebar, textvariable=self.selected_interface, state='readonly')
        self.interface_combo.pack(fill=tk.X, pady=(0, 20))
        
        # Load Map
        tk.Label(self.sidebar, text="2. Load Map:", bg='#f4f4f4', font=title_font).pack(anchor='w', pady=(0, 5))
        ttk.Button(self.sidebar, text="Load Map Image", command=self.load_map).pack(fill=tk.X, pady=(0, 20))
        
        # Calibration
        tk.Label(self.sidebar, text="3. Calibration:", bg='#f4f4f4', font=title_font).pack(anchor='w', pady=(0, 5))
        self.btn_calibrate = ttk.Button(self.sidebar, text="Calibrate Map", command=self.start_calibration, state=tk.DISABLED)
        self.btn_calibrate.pack(fill=tk.X, pady=(0, 5))
        self.lbl_calibration = tk.Label(self.sidebar, text="Not calibrated", bg='#f4f4f4', fg='#cc0000', font=('Helvetica', 9))
        self.lbl_calibration.pack(anchor='w', pady=(0, 20))
        
        # Measure
        tk.Label(self.sidebar, text="4. Measure:", bg='#f4f4f4', font=title_font).pack(anchor='w', pady=(0, 5))
        self.btn_measure = tk.Button(self.sidebar, text="Start Measuring", command=self.toggle_measuring, state=tk.DISABLED, bg='#e0e0e0', relief=tk.RAISED)
        self.btn_measure.pack(fill=tk.X, pady=(0, 20))
        
        # Heatmap
        tk.Label(self.sidebar, text="5. Generate Heatmap:", bg='#f4f4f4', font=title_font).pack(anchor='w', pady=(0, 5))
        self.ssid_combo = ttk.Combobox(self.sidebar, textvariable=self.selected_ssid, state='readonly')
        self.ssid_combo.pack(fill=tk.X, pady=(0, 5))
        self.btn_generate = ttk.Button(self.sidebar, text="Generate Heatmap", command=self.generate_heatmap, state=tk.DISABLED)
        self.btn_generate.pack(fill=tk.X, pady=(0, 20))
        
        # Save / Load Session
        tk.Label(self.sidebar, text="Session Data:", bg='#f4f4f4', font=title_font).pack(anchor='w', pady=(0, 5))
        ttk.Button(self.sidebar, text="Save Session", command=self.save_session).pack(fill=tk.X, pady=(0, 5))
        ttk.Button(self.sidebar, text="Load Session", command=self.load_session).pack(fill=tk.X, pady=(0, 20))
        
        self.lbl_status = tk.Label(self.sidebar, text="Status: IDLE", bg='#f4f4f4', fg='#0055cc', font=('Helvetica', 10, 'bold'), wraplength=200, justify=tk.LEFT)
        self.lbl_status.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))
        
        # Main Canvas Frame
        self.main_frame = tk.Frame(self.root, bg='white')
        self.main_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        self.fig, self.ax = plt.subplots(figsize=(8, 6))
        self.fig.patch.set_facecolor('white')
        self.ax.axis('off')
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.main_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect('button_press_event', self.on_map_click)

    def load_interfaces(self):
        logger.info("Detecting available Wi-Fi interfaces...")
        display_names =[]
        self.interfaces_map = {}
        try:
            if self.os_name == 'Windows':
                logger.info("Using Windows backend for interface detection.")
                if HAS_PYWIFI:
                    wifi = pywifi.PyWiFi()
                    for iface in wifi.interfaces():
                        name = iface.name()
                        display = f"Wi-Fi ({name})"
                        self.interfaces_map[display] = iface
                        display_names.append(display)
                else:
                    output_bytes = subprocess.check_output(['netsh', 'wlan', 'show', 'interfaces'], creationflags=subprocess.CREATE_NO_WINDOW)
                    output = output_bytes.decode('mbcs', errors='ignore')
                    current_name = None
                    for line in output.split('\n'):
                        stripped_line = line.strip()
                        if (stripped_line.startswith('Name') or stripped_line.startswith('Nombre')) and ':' in stripped_line:
                            if current_name and current_name not in self.interfaces_map.values():
                                self.interfaces_map[current_name] = current_name
                                display_names.append(current_name)
                            current_name = stripped_line.split(':', 1)[1].strip()
                        elif current_name and (stripped_line.startswith('Description') or stripped_line.startswith('Descripci')) and ':' in stripped_line:
                            desc = stripped_line.split(':', 1)[1].strip()
                            display = f"{current_name} ({desc})"
                            self.interfaces_map[display] = current_name
                            display_names.append(display)
                            current_name = None
                    if current_name and current_name not in self.interfaces_map.values():
                        self.interfaces_map[current_name] = current_name
                        display_names.append(current_name)
            elif self.os_name == 'Linux':
                logger.info("Using Linux NMCLI backend for interface detection.")
                output = subprocess.check_output(['nmcli', '-t', '-f', 'DEVICE,TYPE', 'device'], encoding='utf-8', errors='ignore')
                for line in output.split('\n'):
                    if ':wifi' in line:
                        name = line.split(':')[0]
                        self.interfaces_map[name] = name
                        display_names.append(name)
            elif self.os_name == 'Darwin':
                logger.info("Using Darwin networksetup backend for interface detection.")
                output = subprocess.check_output(['networksetup', '-listallhardwareports'], encoding='utf-8', errors='ignore')
                lines = output.split('\n')
                for i, line in enumerate(lines):
                    if 'Hardware Port: Wi-Fi' in line:
                        if i + 1 < len(lines) and 'Device:' in lines[i+1]:
                            name = lines[i+1].split(':')[1].strip()
                            display = f"Wi-Fi ({name})"
                            self.interfaces_map[display] = name
                            display_names.append(display)
        except Exception as e:
            logger.error(f"Error loading interfaces: {e}")
            
        logger.info(f"Successfully detected {len(display_names)} interfaces: {display_names}")
            
        if display_names:
            self.interface_combo['values'] = display_names
            self.interface_combo.current(0)
            logger.info(f"Default interface selected: {display_names[0]}")
        else:
            messagebox.showwarning("Wi-Fi Interfaces", "Could not find any Wi-Fi interfaces. Scanning might not work.")
            logger.warning("No Wi-Fi interfaces could be detected.")

    def load_map(self):
        logger.info("Opening file dialog to load a map image...")
        if self.original_image is not None:
            logger.info("A map is already loaded. Prompting user for overwrite confirmation.")
            if not messagebox.askyesno("Confirm", "Loading a new map will delete all current measurements, calibrations, and session data. Continue?"):
                logger.info("User cancelled loading a new map.")
                return
                
        file_path = filedialog.askopenfilename(filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp *.tiff")])
        if file_path:
            self.image_path = file_path
            logger.info(f"User selected map image: {file_path}")
            try:
                img = Image.open(file_path).convert('RGB')
                self.original_image = np.array(img)
                self.img_height, self.img_width = self.original_image.shape[:2]
                
                logger.info(f"Map image successfully parsed and loaded into memory. Resolution: {self.img_width}x{self.img_height}")
                
                # Reset states
                self.pixels_per_meter = None
                self.measurements = []
                self.calibration_points = []
                self.update_ssid_dropdown()

                self.redraw_map()
                
                self.btn_calibrate['state'] = tk.NORMAL
                self.btn_measure.config(state=tk.DISABLED, text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)
                self.btn_generate['state'] = tk.DISABLED
                self.lbl_calibration.config(text="Not calibrated", fg='#cc0000')
                self.lbl_status.config(text="Status: Map loaded")
                logger.info("Application state reset following new map load.")
            except Exception as e:
                logger.error(f"Failed to load map image {file_path}: {e}")
                messagebox.showerror("Error", f"Failed to load image: {e}")
        else:
            logger.info("File dialog closed without selecting a map.")

    def redraw_map(self):
        self.fig.clf()
        self.ax = self.fig.add_subplot(111)
        self.ax.axis('off')
        if self.original_image is not None:
            self.ax.imshow(self.original_image)
            
            h, w = self.img_height, self.img_width
            self.ax.plot([-0.5, w-0.5, w-0.5, -0.5, -0.5],[-0.5, -0.5, h-0.5, h-0.5, -0.5], color='black', linewidth=1.5)
            
            if self.measurements:
                x = [m['x'] for m in self.measurements]
                y =[m['y'] for m in self.measurements]
                self.ax.plot(x, y, 'ro', markersize=6, markeredgecolor='black')
                
        self.fig.tight_layout()
        self.canvas.draw()

    def start_calibration(self):
        logger.info("User initiated map calibration process.")
        if self.original_image is None: 
            logger.warning("Attempted to start calibration without a map loaded.")
            return
        self.state = 'CALIBRATING'
        self.calibration_points =[]
        self.redraw_map()
        self.lbl_status.config(text="Status: CALIBRATING\n(Click 1st point)")
        self.canvas.get_tk_widget().config(cursor="crosshair")
        self.btn_measure.config(text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)
        logger.info("Calibration mode activated. Waiting for user to click the 1st reference point.")

    def is_wifi_on(self, interface):
        """Check if the Wi-Fi interface is powered on."""
        logger.info("Checking Wi-Fi power state...")
        if hasattr(interface, 'name'):
            iface_name = interface.name()
        else:
            iface_name = str(interface)
            
        try:
            if self.os_name == 'Windows':
                cmd = ['netsh', 'wlan', 'show', 'interfaces', f'name={iface_name}']
                try:
                    output = subprocess.check_output(cmd, creationflags=subprocess.CREATE_NO_WINDOW, encoding='mbcs', errors='ignore').lower()
                except:
                    # Fallback to all interfaces if specific name fails
                    output = subprocess.check_output(['netsh', 'wlan', 'show', 'interfaces'], creationflags=subprocess.CREATE_NO_WINDOW, encoding='mbcs', errors='ignore').lower()
                
                # Check for localized strings indicating "Off" status
                off_indicators = [
                    'software off', 'hardware off', 
                    'software desactivado', 'hardware desactivado', 
                    'software aus', 'hardware aus', 
                    'software désactivé', 'hardware désactivé', 
                    'software desligado', 'hardware desligado', 
                    'software disattivato', 'hardware disattivato'
                ]
                if any(ind in output for ind in off_indicators):
                    return False
                return True
                
            elif self.os_name == 'Linux':
                output = subprocess.check_output(['nmcli', 'radio', 'wifi'], encoding='utf-8', errors='ignore').strip().lower()
                if output == 'disabled':
                    return False
                return True
                
            elif self.os_name == 'Darwin':
                output = subprocess.check_output(['networksetup', '-getairportpower', iface_name], encoding='utf-8', errors='ignore').strip().lower()
                if 'off' in output:
                    return False
                return True
        except Exception as e:
            logger.warning(f"Could not determine Wi-Fi power state reliably: {e}")
            
        return True # Default to True so we don't block scanning if check fails

    def toggle_measuring(self):
        logger.info("User toggled measuring mode.")
        if not self.selected_interface.get():
            logger.warning("Measuring toggled without selecting a Wi-Fi interface.")
            messagebox.showwarning("Warning", "Please select a Wi-Fi interface first.")
            return
        if self.original_image is None or self.pixels_per_meter is None: 
            logger.warning("Measuring toggled but conditions not met (missing map or calibration).")
            return
        
        if self.state == 'MEASURING':
            self.state = 'IDLE'
            self.lbl_status.config(text="Status: IDLE")
            self.canvas.get_tk_widget().config(cursor="")
            self.btn_measure.config(text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)
            logger.info("Measuring mode disabled. Reverting to IDLE state.")
        else:
            display_name = self.selected_interface.get()
            interface = self.interfaces_map.get(display_name, display_name)
            if not self.is_wifi_on(interface):
                logger.warning("Attempted to start measuring but Wi-Fi adapter is powered off.")
                messagebox.showwarning("Wi-Fi is Off", "The selected Wi-Fi adapter appears to be powered off.\nPlease turn it on before measuring.")
                return

            self.state = 'MEASURING'
            self.lbl_status.config(text="Status: MEASURING\n(Click on map to measure)")
            self.canvas.get_tk_widget().config(cursor="target")
            self.btn_measure.config(text="Stop Measuring", bg='#90ee90', relief=tk.SUNKEN)
            logger.info("Measuring mode enabled. Awaiting user clicks on the map.")

    def on_map_click(self, event):
        if event.xdata is None or event.ydata is None: 
            return
        
        if event.xdata < 0 or event.xdata >= self.img_width or event.ydata < 0 or event.ydata >= self.img_height:
            return
            
        x, y = int(event.xdata), int(event.ydata)
        logger.info(f"Map clicked at pixel coordinates ({x}, {y}) during state: {self.state}")
        
        if self.state == 'CALIBRATING':
            self.calibration_points.append((x, y))
            self.ax.plot(x, y, 'bx', markersize=10, markeredgewidth=2)
            self.canvas.draw()
            
            if len(self.calibration_points) == 1:
                logger.info(f"1st calibration point registered at ({x}, {y}). Waiting for 2nd point.")
                self.lbl_status.config(text="Status: CALIBRATING\n(Click 2nd point)")
            elif len(self.calibration_points) == 2:
                logger.info(f"2nd calibration point registered at ({x}, {y}). Processing calibration.")
                self.canvas.get_tk_widget().config(cursor="")
                self.state = 'IDLE'
                self.lbl_status.config(text="Status: IDLE")
                
                distance = simpledialog.askfloat("Calibration", "Enter real distance between points in meters:")
                if distance and distance > 0:
                    px_distance = np.sqrt((self.calibration_points[0][0] - self.calibration_points[1][0])**2 + 
                                          (self.calibration_points[0][1] - self.calibration_points[1][1])**2)
                    self.pixels_per_meter = px_distance / distance
                    logger.info(f"Calibration successful. Real distance: {distance}m, Pixel distance: {px_distance:.2f}px. Ratio: {self.pixels_per_meter:.2f} px/m.")
                    self.lbl_calibration.config(text=f"Calibrated: {self.pixels_per_meter:.2f} px/m", fg='#008800')
                    self.btn_measure['state'] = tk.NORMAL
                    self.btn_generate['state'] = tk.NORMAL
                else:
                    logger.info("Calibration cancelled or invalid distance provided. Reverting map state.")
                    self.redraw_map()
                    
        elif self.state == 'MEASURING':
            display_name = self.selected_interface.get()
            interface = self.interfaces_map.get(display_name, display_name)
            if not self.is_wifi_on(interface):
                logger.warning("Wi-Fi adapter is powered off during measurement.")
                messagebox.showwarning("Wi-Fi is Off", "The selected Wi-Fi adapter appears to be powered off.\nPlease turn it on before measuring.")
                self.toggle_measuring()
                return

            logger.info(f"Initiating Wi-Fi measurement sequence at map coordinate ({x}, {y}).")
            self.lbl_status.config(text="Status: SCANNING\n(Please wait...)")
            self.canvas.get_tk_widget().config(cursor="watch")
            self.root.update()
            
            scans =[]
            for i in range(3):
                logger.info(f"Executing scan pass {i+1} of 3...")
                scan_res = self.scan_wifi_once()
                scans.append(scan_res)
                logger.info(f"Scan pass {i+1} completed. Found {len(scan_res)} networks.")
                logger.info(f"Scan pass {i+1} results: {scan_res}")
                time.sleep(1)
                
            avg_scan = {}
            all_ssids = set()
            for s in scans:
                all_ssids.update(s.keys())
                
            for ssid in all_ssids:
                entries =[s[ssid] for s in scans if ssid in s]
                avg_signal = int(sum(e['signal'] for e in entries) / len(entries))
                
                # Assume the frequency doesn't change meaningfully across the 3 rapid scans
                freq = entries[0]['freq']
                avg_scan[ssid] = {'signal': avg_signal, 'freq': freq}
                
            logger.info(f"Averaged scan results: {avg_scan}")
            logger.info(f"Aggregated measurement generated at ({x}, {y}) for {len(avg_scan)} unique SSIDs.")
            self.measurements.append({'x': x, 'y': y, 'ssids': avg_scan})
            self.update_ssid_dropdown()
            
            self.ax.plot(x, y, 'ro', markersize=6, markeredgecolor='black')
            self.canvas.draw()
            
            self.lbl_status.config(text="Status: MEASURING\n(Click on map to measure)")
            self.canvas.get_tk_widget().config(cursor="target")
            logger.info("Measurement sequence complete. Ready for next point.")

    def scan_wifi_once(self):
        display_name = self.selected_interface.get()
        interface = self.interfaces_map.get(display_name, display_name)
        logger.info(f"Executing hardware Wi-Fi scan on interface '{display_name}'...")
        results = {}

        # 0% = -100 dBm, 100% = -40 dBm
        def dbm_to_percent(dbm_val):
            return max(0, min(100, int(round((dbm_val + 100.0) * 100.0 / 60.0))))

        try:
            if self.os_name == 'Windows':
                if HAS_PYWIFI and hasattr(interface, 'scan'):
                    logger.info("Utilizing PyWiFi library for Windows scanning.")
                    interface.scan()
                    time.sleep(2.5) 
                    scan_res = interface.scan_results()
                    for network in scan_res:
                        ssid_val = network.ssid.strip()
                        ssid = ssid_val if ssid_val else "[Hidden SSID]"
                        dbm = network.signal
                        signal = dbm_to_percent(dbm)
                        
                        freq_val = network.freq
                        # Sometimes PyWiFi outputs channel, sometimes KHz, sometimes MHz depending on backend
                        if freq_val < 200: freq = channel_to_freq(freq_val)
                        elif freq_val > 10000: freq = freq_val / 1000.0
                        else: freq = float(freq_val)
                        
                        if freq == 0: freq = 2400.0

                        if ssid not in results or signal > results[ssid]['signal']:
                            results[ssid] = {'signal': signal, 'freq': freq}
                else:
                    logger.info("Utilizing netsh utility for Windows scanning.")
                    cmd =['netsh', 'wlan', 'show', 'networks', f'interface={interface}', 'mode=bssid']
                    try:
                        output_bytes = subprocess.check_output(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
                        output = output_bytes.decode('mbcs', errors='ignore')
                    except Exception as e:
                        logger.error(f"netsh command failed: {e}")
                        output = ""
                    
                    current_ssid = ""
                    current_bssid_info = {}
                    bssid_list =[]
                    
                    for line in output.split('\n'):
                        line = line.strip()
                        if line.startswith('SSID'):
                            parts = line.split(':', 1)
                            if len(parts) > 1:
                                ssid_val = parts[1].strip()
                                current_ssid = ssid_val if ssid_val else "[Hidden SSID]"
                        elif line.startswith('BSSID'):
                            if current_bssid_info: bssid_list.append(current_bssid_info)
                            current_bssid_info = {'ssid': current_ssid, 'channel': None, 'signal': None}
                        elif line.startswith('Channel') or line.startswith('Canal'):
                            parts = line.split(':', 1)
                            if len(parts) > 1 and current_bssid_info:
                                try: current_bssid_info['channel'] = int(parts[1].strip())
                                except: pass
                        elif ('%' in line and ':' in line) or line.startswith('Signal') or line.startswith('Señal'):
                            parts = line.split(':', 1)
                            if len(parts) > 1 and current_bssid_info:
                                signal_str = parts[1].strip().replace('%', '')
                                try:
                                    win_pct = int(signal_str)
                                    dbm = (win_pct / 2.0) - 100.0
                                    current_bssid_info['signal'] = dbm_to_percent(dbm)
                                except ValueError: pass
                    if current_bssid_info:
                        bssid_list.append(current_bssid_info)

                    for b in bssid_list:
                        if b['ssid'] and b['signal'] is not None:
                            freq = channel_to_freq(b['channel']) if b['channel'] else 2400.0
                            if b['ssid'] not in results or b['signal'] > results[b['ssid']]['signal']:
                                results[b['ssid']] = {'signal': b['signal'], 'freq': freq}

            elif self.os_name == 'Linux':
                logger.info("Utilizing nmcli utility for Linux scanning.")
                try:
                    subprocess.run(['nmcli', 'dev', 'wifi', 'rescan'], check=False)
                    time.sleep(1)
                except: pass
                    
                # Format requested: SSID:SIGNAL:FREQ (e.g. MyNet:80:2412 MHz)
                cmd =['nmcli', '-t', '-f', 'SSID,SIGNAL,FREQ', 'dev', 'wifi', 'list', 'ifname', interface]
                output = subprocess.check_output(cmd, encoding='utf-8', errors='ignore')
                for line in output.split('\n'):
                    # nmcli escapes ':' inside SSID. Safest split is from the right for the last 2 metadata pieces
                    parts = line.rsplit(':', 2)
                    if len(parts) == 3:
                        ssid_val = parts[0].replace('\\:', ':').strip()
                        ssid = ssid_val if ssid_val and ssid_val != '--' else "[Hidden SSID]"
                        signal_str = parts[1]
                        freq_str = parts[2].replace(' MHz', '').strip()
                        try:
                            linux_pct = int(signal_str)
                            dbm = (linux_pct / 2.0) - 100.0
                            signal = dbm_to_percent(dbm)
                            freq = float(freq_str) if freq_str.isdigit() else 2400.0
                            
                            if ssid not in results or signal > results[ssid]['signal']:
                                results[ssid] = {'signal': signal, 'freq': freq}
                        except: pass

            elif self.os_name == 'Darwin':
                logger.info("Utilizing airport utility for Darwin scanning.")
                cmd =['/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport', interface, '-s']
                output = subprocess.check_output(cmd, encoding='utf-8', errors='ignore')
                lines = output.split('\n')[1:] 
                for line in lines:
                    if not line.strip(): continue
                    # Columns: SSID BSSID RSSI CHANNEL HT CC SECURITY ...
                    match = re.search(r'(.*?)\s+([0-9a-fA-F:]{17})\s+(-\d+)\s+([0-9,\-+]+)', line)
                    if match:
                        ssid_val = match.group(1).strip()
                        ssid = ssid_val if ssid_val else "[Hidden SSID]"
                        dbm_str = match.group(3)
                        chan_str = match.group(4)
                        try:
                            dbm = int(dbm_str)
                            signal = dbm_to_percent(dbm)
                            
                            # Channel might be "157,1", take base
                            primary_chan = chan_str.split(',')[0].replace('-', '')
                            freq = channel_to_freq(int(primary_chan)) if primary_chan.isdigit() else 2400.0

                            if ssid not in results or signal > results[ssid]['signal']:
                                results[ssid] = {'signal': signal, 'freq': freq}
                        except: pass
        except Exception as e:
            logger.error(f"Critical error during Wi-Fi scan execution: {e}")
            
        logger.info(f"Hardware scan cycle complete. Processed {len(results)} networks.")
        return results

    def update_ssid_dropdown(self):
        logger.info("Updating SSID dropdown with new measurement data.")
        all_ssids = set()
        for m in self.measurements:
            all_ssids.update(m['ssids'].keys())
        
        ssids = sorted(list(all_ssids))
        self.ssid_combo['values'] = ssids
        if ssids and not self.selected_ssid.get() in ssids:
            self.ssid_combo.current(0)
            logger.info(f"SSID dropdown refreshed. Total distinct networks: {len(ssids)}.")
        elif not ssids:
            self.selected_ssid.set('')
            logger.info("SSID dropdown cleared. No networks available.")
    def generate_heatmap(self):
        ssid = self.selected_ssid.get()
        logger.info(f"User requested to generate heatmap for SSID: '{ssid}'.")
        logger.debug(f"Measurements array length: {len(self.measurements)}")
        if not ssid:
            logger.warning("Heatmap generation attempted without an SSID selected.")
            messagebox.showwarning("Warning", "No SSID selected.")
            return
            
        if ssid == "[Hidden SSID]":
            logger.info("Notifying user about caveats of hidden SSID processing.")
            messagebox.showinfo("Hidden Networks", "You are generating a heatmap for [Hidden SSID] networks.\n\nPlease note that multiple distinct hidden networks might be grouped together under this label.")
            
        px =[m['x'] for m in self.measurements if ssid in m['ssids']]
        py =[m['y'] for m in self.measurements if ssid in m['ssids']]
        pz =[m['ssids'][ssid]['signal'] for m in self.measurements if ssid in m['ssids']]
        pf = [m['ssids'][ssid]['freq'] for m in self.measurements if ssid in m['ssids']]
        
        if len(px) < 1:
            logger.warning(f"Heatmap generation aborted: Insufficient measurement points for SSID '{ssid}'.")
            messagebox.showwarning("Warning", "Need at least 1 point for this SSID to generate a heatmap.")
            return
            
        try:
            logger.info(f"Initiating heatmap calculation for '{ssid}' with {len(px)} target points.")
            self.state = 'IDLE'
            self.canvas.get_tk_widget().config(cursor="")
            self.btn_measure.config(text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)
            self.lbl_status.config(text="Status: Generating Heatmap...")
            self.root.update()
            
            # Setup Grid
            grid_x, grid_y = np.mgrid[0:self.img_width:200j, 0:self.img_height:200j]
            logger.info(f"Mathematical interpolation grid constructed over {self.img_width}x{self.img_height} area.")

            # Initialize arrays for physical linear power (mW) tracking
            Z_num = np.zeros(grid_x.shape, dtype=float)
            W_sum = np.zeros(grid_x.shape, dtype=float)
            eps = 1e-6

            logger.info("Applying theoretical frequency propagation models based on Free-Space Path Loss formulas...")

            for xi, yi, zi_percent, freq_mhz in zip(px, py, pz, pf):
                # Back-convert unified % back to physical dBm
                zi_dbm = (zi_percent * 60.0 / 100.0) - 100.0

                dx = grid_x - xi
                dy = grid_y - yi
                dist_px = np.sqrt(dx*dx + dy*dy)
                
                dist_m = dist_px / self.pixels_per_meter if self.pixels_per_meter else dist_px
                
                # 1. Transmission Power anchor to align with calibration point
                tx_power_dbm = 0.0
                
                # 2. Reverse FSPL formula to find Virtual AP Distance
                path_loss_db = tx_power_dbm - zi_dbm
                d_ap_km = 10.0 ** ((path_loss_db - 20 * np.log10(freq_mhz) - 32.44) / 20.0)
                
                # 3. New Distance Simulation
                d_total_km = d_ap_km + (dist_m / 1000.0)
                
                # 4. Forward FSPL calculation at the extended distance
                loss_fspl = 20 * np.log10(d_total_km) + 20 * np.log10(freq_mhz) + 32.44
                
                # 5. Indoor Environment Absorption (forces realistic decay to < 40% & 0%)
                indoor_penalty = 1.2 * dist_m
                
                # Dynamic signal prediction across the whole grid
                predicted_dbm = tx_power_dbm - loss_fspl - indoor_penalty
                
                # Convert prediction back to physical linear power for IDW interpolation blending
                predicted_mw = 10.0 ** (predicted_dbm / 10.0)
                
                # The energy density IDW blends everything with the square of the visual distance
                with np.errstate(divide='ignore'):
                    w = 1.0 / (dist_m**2 + eps)

                # Accumulate linear weighted values
                Z_num += w * predicted_mw
                W_sum += w

            logger.info("Normalizing cumulative signal matrices and converting back to perceptual percentage...")
            # Normalize linear power
            with np.errstate(divide='ignore', invalid='ignore'):
                grid_mw = Z_num / W_sum

            # Convert physically blended signal back to dBm, then the UI-friendly unified Percentage
            with np.errstate(divide='ignore', invalid='ignore'):
                grid_z_dbm = 10.0 * np.log10(grid_mw)

            grid_z = (grid_z_dbm + 100.0) * 100.0 / 60.0
            grid_z = np.clip(grid_z, 0.0, 100.0)
            
            # Mask out uncalculated spots safely
            grid_z[W_sum == 0] = np.nan  
            
            # Mask out any value below 30% to render it completely transparent
            logger.info("Masking out areas below 30% signal strength to render them fully transparent.")
            grid_z[grid_z < 35.0] = np.nan
                 
            self.redraw_map()
            
            logger.info("Heatmap calculation complete. Spawning visualization window...")
            self.show_heatmap_window(ssid, grid_z, px, py, pz)
            
            self.lbl_status.config(text="Status: IDLE")
            
        except Exception as e:
            logger.error(f"Critical error during heatmap generation calculations: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to generate heatmap: {e}")
            self.lbl_status.config(text="Status: IDLE")

    def show_heatmap_window(self, ssid, grid_z, x, y, z):
        logger.info(f"Rendering standalone heatmap window for SSID '{ssid}'...")
        top = tk.Toplevel(self.root)
        top.title(f"Wifi Heatmap of {ssid}")
        top.geometry("800x650")
        
        fig, ax = plt.subplots(figsize=(8, 6))
        fig.patch.set_facecolor('white')
        ax.axis('off')
        
        ax.imshow(self.original_image)

        # Matplotlib Color Map Definition:
        # Values scale from [0.0 to 1.0] representing 0% to 100%.
        # - > 75% Good signal
        # - < 75% and > 50% Acceptable signal
        # - < 50% and > 40% Weak signal
        # - < 40% Unreliable signal
        
        colors_list =[
            (0.00, 'black'),
            (0.34, 'black'),
            (0.35, 'red'),
            (0.75, 'blue'),
            (1.00, 'green')
        ]
        wifi_cmap = LinearSegmentedColormap.from_list('wifi_cmap', colors_list)

        im = ax.imshow(grid_z.T, extent=(0, self.img_width, self.img_height, 0), origin='upper', alpha=0.6, cmap=wifi_cmap, vmin=0, vmax=100)
        sc = ax.scatter(x, y, c=z, cmap=wifi_cmap, edgecolors='black', s=50, vmin=0, vmax=100)
        cbar = fig.colorbar(im, ax=ax, label='Signal Strength (%)')
        
        # Add a help icon next to the legend (colorbar)
        cbar_ax = cbar.ax
        help_icon = cbar_ax.annotate(' ? ', xy=(0.5, 1.05), xycoords='axes fraction',
                                     ha='center', va='bottom', fontsize=10, fontweight='bold',
                                     bbox=dict(boxstyle='circle,pad=0.1', fc='lightyellow', ec='black', alpha=0.8))

        tooltip_text = (
            "Signal Quality Guide:\n"
            "> 75% : Good\n"
            "75% - 50% : Acceptable\n"
            "50% - 40% : Weak\n"
            "< 40% : Unreliable"
        )
        
        tooltip = cbar_ax.annotate(
            tooltip_text,
            xy=(0, 1.05), xycoords='axes fraction',
            xytext=(-10, 0), textcoords='offset points',
            ha='right', va='top',
            bbox=dict(boxstyle='round,pad=0.5', fc='#ffffe0', ec='black', alpha=0.9),
            fontsize=9,
            visible=False,
            zorder=100
        )

        def on_hover(event):
            if event.x is None or event.y is None:
                return
            cont, _ = help_icon.contains(event)
            if cont:
                if not tooltip.get_visible():
                    tooltip.set_visible(True)
                    fig.canvas.draw_idle()
            else:
                if tooltip.get_visible():
                    tooltip.set_visible(False)
                    fig.canvas.draw_idle()

        ax.set_title(f"Heatmap for {ssid}")
        fig.tight_layout()
        
        canvas = FigureCanvasTkAgg(fig, master=top)
        canvas.mpl_connect('motion_notify_event', on_hover)
        
        def save_png():
            safe_ssid = "".join([c for c in ssid if c.isalpha() or c.isdigit() or c==' ']).rstrip()
            logger.info("Opening save dialog for heatmap PNG export...")
            filepath = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG Image", "*.png")], initialfile=f"Heatmap_{safe_ssid}.png", parent=top)
            if filepath:
                logger.info(f"User chose to save heatmap PNG to: {filepath}")
                
                # Hide tooltip and icon before export
                was_visible = tooltip.get_visible()
                if was_visible:
                    tooltip.set_visible(False)
                help_icon.set_visible(False)
                    
                fig.savefig(filepath, dpi=300, bbox_inches='tight')
                
                # Restore tooltip and icon visibility
                if was_visible:
                    tooltip.set_visible(True)
                help_icon.set_visible(True)
                    
                logger.info("Heatmap successfully exported.")
                messagebox.showinfo("Success", "Heatmap exported successfully!", parent=top)
            else:
                logger.info("Heatmap PNG export cancelled by user.")
                
        btn_frame = tk.Frame(top, bg='#f4f4f4', pady=10)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btn_frame, text="Export as PNG", command=save_png).pack()

        canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        canvas.draw()
        logger.info("Heatmap visualization successfully drawn and displayed.")

    def save_session(self):
        logger.info("User requested to save the current session.")
        logger.debug(f"Current pixels_per_meter: {self.pixels_per_meter}, Measurements: {len(self.measurements)}")
        if not self.measurements and self.original_image is None:
            logger.warning("Save session aborted: No valid map or measurements exist in the current state.")
            messagebox.showinfo("Info", "Nothing to save.")
            return
            
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if file_path:
            logger.info(f"Target file for session save: {file_path}")
            try:
                image_b64 = None
                if self.original_image is not None:
                    logger.info("Encoding map image to base64 for embedding in session file...")
                    img = Image.fromarray(self.original_image)
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    image_b64 = base64.b64encode(buf.getvalue()).decode('ascii')
                
                data = {
                    'image_base64': image_b64,
                    'pixels_per_meter': self.pixels_per_meter,
                    'measurements': self.measurements
                }
                
                logger.info("Writing JSON payload to disk...")
                with open(file_path, 'w') as f:
                    json.dump(data, f)
                logger.info(f"Session completely saved to {file_path}. Included {len(self.measurements)} measurement points.")
                messagebox.showinfo("Success", "Session saved successfully.")
            except Exception as e:
                logger.error(f"Critical failure while attempting to save session: {e}", exc_info=True)
                messagebox.showerror("Error", f"Failed to save session: {e}")
        else:
            logger.info("Save session cancelled by user.")

    def load_session(self):
        logger.info("User requested to load an existing session file. Opening file dialog...")
        file_path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if file_path:
            try:
                logger.info(f"Initiating session payload load from {file_path}...")
                with open(file_path, 'r') as f:
                    data = json.load(f)
                
                image_b64 = data.get('image_base64')
                if image_b64:
                    logger.info("Decoding embedded base64 map image from session file...")
                    img_bytes = base64.b64decode(image_b64)
                    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
                    self.original_image = np.array(img)
                    self.img_height, self.img_width = self.original_image.shape[:2]
                    self.image_path = None
                    logger.info(f"Map image loaded successfully. Dimensions: ({self.img_width}x{self.img_height})")
                else:
                    logger.warning("No embedded base64 image found in the session payload.")
                    messagebox.showwarning("Warning", "No map image found in session file.")
                    
                self.pixels_per_meter = data.get('pixels_per_meter')
                self.measurements = data.get('measurements',[])
                
                if self.original_image is not None:
                    logger.info("Redrawing GUI map view with loaded image payload.")
                    self.redraw_map()
                    self.btn_calibrate['state'] = tk.NORMAL
                    
                if self.pixels_per_meter:
                    logger.info(f"Restoring calibration matrix. Pixels per meter: {self.pixels_per_meter:.2f}")
                    self.lbl_calibration.config(text=f"Calibrated: {self.pixels_per_meter:.2f} px/m", fg='#008800')
                    self.btn_measure['state'] = tk.NORMAL
                    self.btn_generate['state'] = tk.NORMAL
                else:
                    logger.info("Loaded session lacks calibration metadata.")
                    self.lbl_calibration.config(text="Not calibrated", fg='#cc0000')
                    self.btn_measure['state'] = tk.DISABLED
                    self.btn_generate['state'] = tk.DISABLED
                    
                self.btn_measure.config(text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)
                self.update_ssid_dropdown()
                
                logger.info(f"Session restoration fully complete. Successfully extracted {len(self.measurements)} measurement data points.")
                messagebox.showinfo("Success", "Session loaded successfully.")
                
            except Exception as e:
                logger.error(f"Critical failure while attempting to load session payload: {e}", exc_info=True)
                messagebox.showerror("Error", f"Failed to load session: {e}")
        else:
            logger.info("Load session cancelled by user.")

if __name__ == "__main__":
    root = tk.Tk()
    app = WifiHeatmapApp(root)
    root.mainloop()