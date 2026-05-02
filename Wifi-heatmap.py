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
parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], help='Set the logging level')
args, unknown = parser.parse_known_args()

logging.basicConfig(level=getattr(logging, args.log_level), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout, force=True)
logger = logging.getLogger('WiFiHeatmap')

# Silence external libraries unless explicitly requested
logging.getLogger('matplotlib').setLevel(logging.WARNING)
logging.getLogger('PIL').setLevel(logging.WARNING)
if args.log_level != 'DEBUG':
    logging.getLogger('pywifi').setLevel(logging.WARNING)

matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import os

class WifiHeatmapApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Wi-Fi Heatmap Generator")
        self.root.geometry("1000x700")
        
        # Configure a simple nice theme
        style = ttk.Style()
        style.theme_use('clam')

        self.os_name = platform.system()
        
        self.image_path = None
        self.original_image = None
        self.img_width = 0
        self.img_height = 0
        
        self.calibration_points = []
        self.pixels_per_meter = None
        
        self.measurements = []
        
        self.state = 'IDLE' 
        
        self.selected_interface = tk.StringVar()
        self.selected_ssid = tk.StringVar()
        self.interfaces_map = {}
        
        self.setup_ui()
        self.load_interfaces()
        
        # Handle window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    def on_closing(self):
        if messagebox.askyesno("Quit", "Are you sure you want to exit? Any unsaved progress will be lost."):
            self.root.destroy()
            self.root.quit()
        

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
        display_names = []
        self.interfaces_map = {}
        try:
            if self.os_name == 'Windows':
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
                output = subprocess.check_output(['nmcli', '-t', '-f', 'DEVICE,TYPE', 'device'], encoding='utf-8', errors='ignore')
                for line in output.split('\n'):
                    if ':wifi' in line:
                        name = line.split(':')[0]
                        self.interfaces_map[name] = name
                        display_names.append(name)
            elif self.os_name == 'Darwin':
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
            
        logger.info(f"Interfaces detected: {display_names}")
            
        if display_names:
            self.interface_combo['values'] = display_names
            self.interface_combo.current(0)
        else:
            messagebox.showwarning("Wi-Fi Interfaces", "Could not find any Wi-Fi interfaces. Scanning might not work.")

    def load_map(self):
        if self.original_image is not None:
            if not messagebox.askyesno("Confirm", "Loading a new map will delete all current measurements, calibrations, and session data. Continue?"):
                return
                
        file_path = filedialog.askopenfilename(filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp *.tiff")])
        if file_path:
            self.image_path = file_path
            try:
                img = Image.open(file_path).convert('RGB')
                self.original_image = np.array(img)
                self.img_height, self.img_width = self.original_image.shape[:2]
                
                logger.info(f"Map image loaded: {file_path} ({self.img_width}x{self.img_height})")
                
                self.redraw_map()
                
                self.btn_calibrate['state'] = tk.NORMAL
                self.btn_measure.config(state=tk.DISABLED, text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)
                self.btn_generate['state'] = tk.DISABLED
                self.lbl_calibration.config(text="Not calibrated", fg='#cc0000')
                self.pixels_per_meter = None
                self.measurements = []
                self.update_ssid_dropdown()
                self.lbl_status.config(text="Status: Map loaded")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load image: {e}")

    def redraw_map(self):
        self.fig.clf()
        self.ax = self.fig.add_subplot(111)
        self.ax.axis('off')
        if self.original_image is not None:
            self.ax.imshow(self.original_image)
            
            # Draw a border around the image to clearly show its bounds
            h, w = self.img_height, self.img_width
            self.ax.plot([-0.5, w-0.5, w-0.5, -0.5, -0.5], [-0.5, -0.5, h-0.5, h-0.5, -0.5], color='black', linewidth=1.5)
            
            # Plot existing measurements
            if self.measurements:
                x = [m['x'] for m in self.measurements]
                y = [m['y'] for m in self.measurements]
                self.ax.plot(x, y, 'ro', markersize=6, markeredgecolor='black')
                
        self.fig.tight_layout()
        self.canvas.draw()

    def start_calibration(self):
        if self.original_image is None: return
        self.state = 'CALIBRATING'
        self.calibration_points = []
        self.lbl_status.config(text="Status: CALIBRATING\n(Click 1st point)")
        self.canvas.get_tk_widget().config(cursor="crosshair")
        self.btn_measure.config(text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)

    def toggle_measuring(self):
        if not self.selected_interface.get():
            messagebox.showwarning("Warning", "Please select a Wi-Fi interface first.")
            return
        if self.original_image is None or self.pixels_per_meter is None: return
        
        if self.state == 'MEASURING':
            self.state = 'IDLE'
            self.lbl_status.config(text="Status: IDLE")
            self.canvas.get_tk_widget().config(cursor="")
            self.btn_measure.config(text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)
        else:
            self.state = 'MEASURING'
            self.lbl_status.config(text="Status: MEASURING\n(Click on map to measure)")
            self.canvas.get_tk_widget().config(cursor="target")
            self.btn_measure.config(text="Stop Measuring", bg='#90ee90', relief=tk.SUNKEN)

    def on_map_click(self, event):
        if event.xdata is None or event.ydata is None: return
        
        # Enforce that clicks must be strictly inside the image bounds
        if event.xdata < 0 or event.xdata >= self.img_width or event.ydata < 0 or event.ydata >= self.img_height:
            return
            
        x, y = int(event.xdata), int(event.ydata)
        
        if self.state == 'CALIBRATING':
            self.calibration_points.append((x, y))
            self.ax.plot(x, y, 'bx', markersize=10, markeredgewidth=2)
            self.canvas.draw()
            
            if len(self.calibration_points) == 1:
                self.lbl_status.config(text="Status: CALIBRATING\n(Click 2nd point)")
            elif len(self.calibration_points) == 2:
                self.canvas.get_tk_widget().config(cursor="")
                self.state = 'IDLE'
                self.lbl_status.config(text="Status: IDLE")
                
                # Ask for distance
                distance = simpledialog.askfloat("Calibration", "Enter real distance between points in meters:")
                if distance and distance > 0:
                    px_distance = np.sqrt((self.calibration_points[0][0] - self.calibration_points[1][0])**2 + 
                                          (self.calibration_points[0][1] - self.calibration_points[1][1])**2)
                    self.pixels_per_meter = px_distance / distance
                    logger.info(f"Calibration complete: {self.pixels_per_meter:.2f} px/m (pixel distance: {px_distance:.1f}, real distance: {distance}m)")
                    self.lbl_calibration.config(text=f"Calibrated: {self.pixels_per_meter:.2f} px/m", fg='#008800')
                    self.btn_measure['state'] = tk.NORMAL
                    self.btn_generate['state'] = tk.NORMAL
                else:
                    self.redraw_map() # remove markers if cancelled
                    
        elif self.state == 'MEASURING':
            self.lbl_status.config(text="Status: SCANNING\n(Please wait...)")
            self.canvas.get_tk_widget().config(cursor="wait")
            self.root.update()
            
            # Take 3 measurements
            scans = []
            for i in range(3):
                scan_res = self.scan_wifi_once()
                scans.append(scan_res)
                logger.info(f"Scan {i+1}/3 at ({x}, {y}) results: {scan_res}")
                time.sleep(1) # Delay between scans
                
            # Average the results
            avg_scan = {}
            all_ssids = set()
            for s in scans:
                all_ssids.update(s.keys())
                
            for ssid in all_ssids:
                vals = [s[ssid] for s in scans if ssid in s]
                avg_scan[ssid] = int(sum(vals) / len(vals))
                
            logger.info(f"Recorded measurement at ({x}, {y}) for {len(avg_scan)} SSIDs: {avg_scan}")
            self.measurements.append({'x': x, 'y': y, 'ssids': avg_scan})
            self.update_ssid_dropdown()
            
            self.ax.plot(x, y, 'ro', markersize=6, markeredgecolor='black')
            self.canvas.draw()
            
            self.lbl_status.config(text="Status: MEASURING\n(Click on map to measure)")
            self.canvas.get_tk_widget().config(cursor="target")

    def scan_wifi_once(self):
        display_name = self.selected_interface.get()
        interface = self.interfaces_map.get(display_name, display_name)
        results = {}
        try:
            if self.os_name == 'Windows':
                if HAS_PYWIFI and hasattr(interface, 'scan'):
                    logger.info(f"Running hardware scan using pywifi")
                    interface.scan()
                    time.sleep(2.5) # Give it time to scan
                    scan_res = interface.scan_results()
                    for network in scan_res:
                        ssid_val = network.ssid.strip()
                        ssid = ssid_val if ssid_val else "[Hidden SSID]"
                        dbm = network.signal
                        signal = max(0, min(100, int(2 * (dbm + 100))))
                        if ssid not in results or signal > results[ssid]:
                            results[ssid] = signal
                            logger.info(f"Parsed PyWiFi SSID: {ssid}, Signal: {signal} (from {dbm} dBm)")
                else:
                    cmd = ['netsh', 'wlan', 'show', 'networks', f'interface={interface}', 'mode=bssid']
                    logger.info(f"Running command: {' '.join(cmd)}")
                    try:
                        output_bytes = subprocess.check_output(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
                        output = output_bytes.decode('mbcs', errors='ignore')
                    except Exception as e:
                        logger.error(f"Command failed: {e}")
                        output = ""
                    logger.debug(f"Command output:\n{output}")
                    
                    current_ssid = ""
                    for line in output.split('\n'):
                        line = line.strip()
                        if line.startswith('SSID'):
                            parts = line.split(':', 1)
                            if len(parts) > 1:
                                ssid_val = parts[1].strip()
                                current_ssid = ssid_val if ssid_val else "[Hidden SSID]"
                        elif ('%' in line and ':' in line) or line.startswith('Signal') or line.startswith('Señal'):
                            parts = line.split(':', 1)
                            if len(parts) > 1 and current_ssid:
                                signal_str = parts[1].strip().replace('%', '')
                                try:
                                    signal = int(signal_str)
                                    if current_ssid not in results or signal > results[current_ssid]:
                                        results[current_ssid] = signal
                                    logger.info(f"Parsed Windows SSID: {current_ssid}, Signal: {signal}")
                                except ValueError: pass
            elif self.os_name == 'Linux':
                try:
                    logger.info(f"Forcing rescan on Linux: nmcli dev wifi rescan")
                    subprocess.run(['nmcli', 'dev', 'wifi', 'rescan'], check=False)
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"Failed to force rescan: {e}")
                    
                cmd = ['nmcli', '-t', '-f', 'SSID,SIGNAL', 'dev', 'wifi', 'list', 'ifname', interface]
                output = subprocess.check_output(cmd, encoding='utf-8', errors='ignore')
                for line in output.split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 2:
                        ssid_val = parts[0].strip()
                        ssid = ssid_val if ssid_val and ssid_val != '--' else "[Hidden SSID]"
                        signal_str = parts[1]
                        try:
                            signal = int(signal_str)
                            if ssid not in results or signal > results[ssid]:
                                results[ssid] = signal
                        except: pass
            elif self.os_name == 'Darwin':
                cmd = ['/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport', interface, '-s']
                output = subprocess.check_output(cmd, encoding='utf-8', errors='ignore')
                lines = output.split('\n')[1:] 
                for line in lines:
                    if not line.strip(): continue
                    match = re.search(r'(.*?)\s+([0-9a-fA-F:]{17})\s+(-\d+)', line)
                    if match:
                        ssid_val = match.group(1).strip()
                        ssid = ssid_val if ssid_val else "[Hidden SSID]"
                        dbm_str = match.group(3)
                        try:
                            dbm = int(dbm_str)
                            # Convert dBm to approximate percentage (100% at -50dBm, 0% at -100dBm)
                            signal = max(0, min(100, int(2 * (dbm + 100))))
                            if ssid not in results or signal > results[ssid]:
                                results[ssid] = signal
                        except: pass
        except Exception as e:
            logger.error(f"Error scanning: {e}")
        return results

    def update_ssid_dropdown(self):
        all_ssids = set()
        for m in self.measurements:
            all_ssids.update(m['ssids'].keys())
        
        # Empty SSIDs are now tracked as [Hidden]
        
        ssids = sorted(list(all_ssids))
        self.ssid_combo['values'] = ssids
        if ssids and not self.selected_ssid.get() in ssids:
            self.ssid_combo.current(0)

    def generate_heatmap(self):
        ssid = self.selected_ssid.get()
        if not ssid:
            messagebox.showwarning("Warning", "No SSID selected.")
            return
            
        if ssid == "[Hidden SSID]":
            messagebox.showinfo("Hidden Networks", "You are generating a heatmap for [Hidden SSID] networks.\n\nPlease note that multiple distinct hidden networks might be grouped together under this label.")
            
        x = [m['x'] for m in self.measurements if ssid in m['ssids']]
        y = [m['y'] for m in self.measurements if ssid in m['ssids']]
        z = [m['ssids'][ssid] for m in self.measurements if ssid in m['ssids']]
        
        if len(x) < 3:
            messagebox.showwarning("Warning", "Need at least 3 points for this SSID to generate a heatmap.")
            return
            
        try:
            self.state = 'IDLE'
            self.canvas.get_tk_widget().config(cursor="")
            self.btn_measure.config(text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)
            self.lbl_status.config(text="Status: Generating Heatmap...")
            self.root.update()
            
            # Setup Grid
            grid_x, grid_y = np.mgrid[0:self.img_width:200j, 0:self.img_height:200j]
            
            # Interpolate
            grid_z = scipy.interpolate.griddata((x, y), z, (grid_x, grid_y), method='cubic')
            
            # Fallback to linear or nearest if cubic fails (e.g., points are collinear)
            if np.all(np.isnan(grid_z)):
                 grid_z = scipy.interpolate.griddata((x, y), z, (grid_x, grid_y), method='linear')
            if np.all(np.isnan(grid_z)):
                 grid_z = scipy.interpolate.griddata((x, y), z, (grid_x, grid_y), method='nearest')
                 
            self.redraw_map()
            
            self.show_heatmap_window(ssid, grid_z, x, y, z)
            
            self.lbl_status.config(text="Status: IDLE")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate heatmap: {e}")
            self.lbl_status.config(text="Status: IDLE")

    def show_heatmap_window(self, ssid, grid_z, x, y, z):
        top = tk.Toplevel(self.root)
        top.title(f"Wifi Heatmap of {ssid}")
        top.geometry("800x650")
        
        fig, ax = plt.subplots(figsize=(8, 6))
        fig.patch.set_facecolor('white')
        ax.axis('off')
        
        ax.imshow(self.original_image)
        im = ax.imshow(grid_z.T, extent=(0, self.img_width, self.img_height, 0), origin='upper', alpha=0.6, cmap='RdYlGn', vmin=0, vmax=100)
        sc = ax.scatter(x, y, c=z, cmap='RdYlGn', edgecolors='black', s=50, vmin=0, vmax=100)
        fig.colorbar(im, ax=ax, label='Signal Strength (%)')
        ax.set_title(f"Heatmap for {ssid}")
        fig.tight_layout()
        
        canvas = FigureCanvasTkAgg(fig, master=top)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        canvas.draw()
        
        def save_png():
            safe_ssid = "".join([c for c in ssid if c.isalpha() or c.isdigit() or c==' ']).rstrip()
            filepath = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG Image", "*.png")], initialfile=f"Heatmap_{safe_ssid}.png", parent=top)
            if filepath:
                fig.savefig(filepath, dpi=300, bbox_inches='tight')
                messagebox.showinfo("Success", "Heatmap exported successfully!", parent=top)
                
        btn_frame = tk.Frame(top, bg='#f4f4f4', pady=10)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btn_frame, text="Export as PNG", command=save_png).pack()

    def save_session(self):
        if not self.measurements and self.original_image is None:
            messagebox.showinfo("Info", "Nothing to save.")
            return
            
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if file_path:
            try:
                # Encode the image as base64 for portability
                image_b64 = None
                if self.original_image is not None:
                    img = Image.fromarray(self.original_image)
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    image_b64 = base64.b64encode(buf.getvalue()).decode('ascii')
                
                data = {
                    'image_base64': image_b64,
                    'pixels_per_meter': self.pixels_per_meter,
                    'measurements': self.measurements
                }
                with open(file_path, 'w') as f:
                    json.dump(data, f)
                logger.info(f"Session saved to {file_path} ({len(self.measurements)} measurements)")
                messagebox.showinfo("Success", "Session saved successfully.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save session: {e}")

    def load_session(self):
        file_path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if file_path:
            try:
                logger.info(f"Loading session from {file_path}")
                with open(file_path, 'r') as f:
                    data = json.load(f)
                
                # Try base64 image first, fall back to image_path for old session files
                image_b64 = data.get('image_base64')
                if image_b64:
                    img_bytes = base64.b64decode(image_b64)
                    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
                    self.original_image = np.array(img)
                    self.img_height, self.img_width = self.original_image.shape[:2]
                    self.image_path = None
                    logger.info(f"Map image loaded from embedded base64 ({self.img_width}x{self.img_height})")
                else:
                    img_path = data.get('image_path')
                    if img_path and os.path.exists(img_path):
                        self.image_path = img_path
                        img = Image.open(img_path).convert('RGB')
                        self.original_image = np.array(img)
                        self.img_height, self.img_width = self.original_image.shape[:2]
                        logger.info(f"Map image loaded from path: {img_path} ({self.img_width}x{self.img_height})")
                    else:
                        messagebox.showwarning("Warning", "Saved map image not found. Please load a map manually.")
                    
                self.pixels_per_meter = data.get('pixels_per_meter')
                self.measurements = data.get('measurements', [])
                
                if self.original_image is not None:
                    self.redraw_map()
                    self.btn_calibrate['state'] = tk.NORMAL
                    
                if self.pixels_per_meter:
                    self.lbl_calibration.config(text=f"Calibrated: {self.pixels_per_meter:.2f} px/m", fg='#008800')
                    self.btn_measure['state'] = tk.NORMAL
                    self.btn_generate['state'] = tk.NORMAL
                else:
                    self.lbl_calibration.config(text="Not calibrated", fg='#cc0000')
                    self.btn_measure['state'] = tk.DISABLED
                    self.btn_generate['state'] = tk.DISABLED
                    
                self.btn_measure.config(text="Start Measuring", bg='#e0e0e0', relief=tk.RAISED)
                self.update_ssid_dropdown()
                logger.info(f"Session loaded successfully ({len(self.measurements)} measurements, calibration: {self.pixels_per_meter})")
                messagebox.showinfo("Success", "Session loaded successfully.")
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load session: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = WifiHeatmapApp(root)
    root.mainloop()
