import sys
from time import sleep
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QPushButton, QFileDialog, QGraphicsView, QGraphicsScene,
    QVBoxLayout, QWidget, QGraphicsPixmapItem, QInputDialog, QMessageBox, QDialog, QHBoxLayout,
    QCheckBox, QComboBox
)
from PyQt5.QtGui import QPixmap, QPen, QColor
from PyQt5.QtCore import Qt, QPointF
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import numpy as np
from pywifi import PyWiFi, const, Profile
from PIL import Image
import os
from scipy.interpolate import Rbf
import math

class WiFiHeatmapApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WiFi Heatmap App")
        self.setGeometry(100, 100, 800, 600)

        self.map_image_path = None
        self.measurements = []  # Store [(x, y, ssid, signal_strength)]
        self.points = []  # Store graphical points
        self.interface = None
        self.enabled_scans = False
        
        # New calibration attributes
        self.calibration_points = []
        self.pixels_per_meter = None
        self.measurement_radius = 5  # Default 5 meters
        self.is_calibrating = False
        self.calibration_line = None

        self.init_ui()

    def init_ui(self):
        # Main layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout()
        self.central_widget.setLayout(self.layout)

        # Map display
        self.graphics_view = QGraphicsView()
        self.scene = QGraphicsScene()
        self.graphics_view.setScene(self.scene)
        self.layout.addWidget(self.graphics_view)

        # Buttons
        self.select_interface_button = QPushButton("Select WiFi Interface")
        self.select_interface_button.clicked.connect(self.select_interface)
        self.layout.addWidget(self.select_interface_button)

        self.load_map_button = QPushButton("Load Map (.png)")
        self.load_map_button.clicked.connect(self.load_map)
        self.layout.addWidget(self.load_map_button)

        self.set_radius_button = QPushButton("Set Measurement Radius (meters)")
        self.set_radius_button.clicked.connect(self.set_measurement_radius)
        self.set_radius_button.setDisabled(True)
        self.layout.addWidget(self.set_radius_button)

        self.scan_button = QPushButton("Click on map to Scan WiFi: OFF")
        self.scan_button.setDisabled(True)
        self.scan_button.clicked.connect(self.toggle_scan)
        self.layout.addWidget(self.scan_button)

        self.generate_heatmap_button = QPushButton("Generate Heatmap")
        self.generate_heatmap_button.setDisabled(True)
        self.generate_heatmap_button.clicked.connect(self.generate_heatmap)
        self.layout.addWidget(self.generate_heatmap_button)

        self.graphics_view.mousePressEvent = self.record_click

    def select_interface(self):
        wifi = PyWiFi()
        interfaces = wifi.interfaces()
        if not interfaces:
            QMessageBox.critical(self, "Error", "No WiFi interfaces found.")
            return

        iface_names = [iface.name() for iface in interfaces]
        selected_iface_name, ok = QInputDialog.getItem(self, "Select Interface", "Available Interfaces:", iface_names, 0, False)
        if ok and selected_iface_name:
            self.interface = next((iface for iface in interfaces if iface.name() == selected_iface_name), None)
            if self.interface:
                print(f"Interface selected: {self.interface.name()}")
                self.select_interface_button.setText(f"Select WiFi Interface - Currently using {self.interface.name()}")
            else:
                QMessageBox.critical(self, "Error", "Error: Selected interface not found.")
                print("Error: Selected interface not found.")

    def load_map(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Map", "", "PNG Files (*.png)", options=options)
        if file_path:
            self.load_map_button.setText("Discard current and load new Map (.png)")
            self.map_image_path = file_path
            self.display_map()
            self.start_calibration()

    def start_calibration(self):
        self.is_calibrating = True
        self.calibration_points = []
        QMessageBox.about(self, "Calibration", 
            "Please click two points on the map and enter the real-world distance between them in meters.")
    
    def set_measurement_radius(self):
        radius, ok = QInputDialog.getDouble(self, "Set Radius", 
            "Enter measurement radius in meters:", value=self.measurement_radius,
            min=1.0, max=100.0, decimals=1)
        if ok:
            self.measurement_radius = radius

    def calculate_pixels_per_meter(self, distance_meters):
        if len(self.calibration_points) == 2:
            p1, p2 = self.calibration_points
            pixel_distance = np.sqrt((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)
            print(f"Pixels: {pixel_distance}")
            print(f"Meters: {distance_meters}")
            self.pixels_per_meter = pixel_distance / distance_meters
            print(f"Calibration: {self.pixels_per_meter:.2f} pixels per meter")

    def display_map(self):
        pixmap = QPixmap(self.map_image_path)
        self.scene.clear()
        self.scene.addPixmap(pixmap)
        self.points = []  # Reset points when a new map is loaded

    def record_click(self, event):
        if not self.map_image_path:
            return

        scene_pos = self.graphics_view.mapToScene(event.pos())
        x, y = int(scene_pos.x()), int(scene_pos.y())

        if self.is_calibrating:
            self.handle_calibration_click(x, y)
        elif event.button() == Qt.LeftButton and self.enabled_scans:
            self.draw_point(x, y)
            self.perform_scan(x, y)

    def draw_point(self, x, y):
        pen = QPen(QColor("red"))
        pen.setWidth(5)
        point = self.scene.addEllipse(x - 2, y - 2, 4, 4, pen)
        self.points.append(point)
    
    def handle_calibration_click(self, x, y):
        self.calibration_points.append((x, y))
        self.draw_calibration_point(x, y)
        
        if len(self.calibration_points) == 1:
            QMessageBox.information(self, "Calibration", "Now click the second point.")
        
        elif len(self.calibration_points) == 2:
            # Draw line between calibration points
            p1, p2 = self.calibration_points
            pen = QPen(QColor("blue"))
            pen.setWidth(2)
            self.calibration_line = self.scene.addLine(p1[0], p1[1], p2[0], p2[1], pen)
            
            # Get real-world distance
            distance, ok = QInputDialog.getDouble(self, "Calibration", 
                "Enter the real-world distance between the points (in meters):",
                value=10.0, min=0.1, max=1000.0, decimals=1)
            
            if ok:
                self.calculate_pixels_per_meter(distance)
                self.is_calibrating = False
                self.enabled_scans = True
                self.scan_button.setText("Click on map to Scan WiFi: ON")
                self.scan_button.setDisabled(False)
                self.set_radius_button.setDisabled(False)
                QMessageBox.information(self, "Calibration Complete", 
                    "Calibration complete! You can now start scanning.")

    def draw_calibration_point(self, x, y):
        pen = QPen(QColor("blue"))
        pen.setWidth(5)
        point = self.scene.addEllipse(x - 2, y - 2, 4, 4, pen)
        self.points.append(point)

    def mergeDictionary(self, dict1, dict2):
        merged_dict = {}
        for key in set(dict1.keys()).union(dict2.keys()):
            merged_dict[key] = dict1.get(key, []) + dict2.get(key, [])
        return merged_dict
    
    def perform_scan(self, x, y):
        if not self.interface:
            print("No interface selected. Please select a WiFi interface.")
            return

        self.enabled_scans = False
        self.scan_button.setText("Scanning...")
        self.scan_button.setDisabled(True)
        QApplication.processEvents()

        ssid_signals = {}  # Dictionary to store SSIDs and their signal strengths across scans

        for i in range(3):  # Perform three scans
            self.scan_button.setText(f"Scanning... Scan {i+1}/3")
            QApplication.processEvents()
            ssid_signals_scan = {}
            self.wait_while_interface_scanning()
            access_points = self.scan_access_points()

            for ssid, signal_strength in access_points:
                if ssid not in ssid_signals_scan:
                    ssid_signals_scan[ssid] = []
                # Convert dBm to Watts
                signal_watts = math.pow(10, signal_strength / 10) / 1000

                # Keep only the highest signal strength (in Watts) for this scan
                if not ssid in ssid_signals or signal_watts > max(ssid_signals_scan[ssid]):
                    ssid_signals_scan[ssid] = [signal_watts]
            
            self.mergeDictionary(ssid_signals, ssid_signals_scan)
                
        # Compute average signal strength in Watts for each SSID, then convert back to dBm
        averaged_signals = {
            ssid: 10 * math.log10((sum(signals) / len(signals)) * 1000) for ssid, signals in ssid_signals_scan.items() if signals
        }

        # Update measurements with the average values
        for ssid, avg_signal_strength in averaged_signals.items():
            print(f"Averaged SSID: {ssid}, Average Signal Strength: {avg_signal_strength:.2f} dBm")
            existing_measurement = next((m for m in self.measurements if m[0] == x and m[1] == y and m[2] == ssid), None)
            if existing_measurement:
                if avg_signal_strength > existing_measurement[3]:
                    self.measurements.remove(existing_measurement)
                    self.measurements.append((x, y, ssid, avg_signal_strength))
            else:
                self.measurements.append((x, y, ssid, avg_signal_strength))

        self.wait_while_interface_scanning()

        self.enabled_scans = True
        self.scan_button.setText("Click on map to Scan WiFi: ON")
        self.scan_button.setDisabled(False)
        self.generate_heatmap_button.setDisabled(False)
        print(f"Scan complete at ({x}, {y})")

    def wait_while_interface_scanning(self):
        while self.interface.status() == const.IFACE_SCANNING:
            sleep(0.5)

    def scan_access_points(self):
        aps = []
        self.interface.scan() # Note. Because the scan time for each Wi-Fi interface is variant. It is safer to call scan_results() 2 ~ 8 seconds later after calling scan().
        self.wait_while_interface_scanning()
        results = self.interface.scan_results()

        for result in results:
            ssid = result.ssid if result.ssid else "<Hidden SSID>"
            signal_strength = result.signal  # Signal strength in dBm
            aps.append((ssid, signal_strength))

        return aps

    def generate_heatmap(self):
        ssid_choices = {m[2] for m in self.measurements}
        ssid_choices = list(ssid_choices)

        if not ssid_choices:
            print("No SSIDs found to generate a heatmap.")
            return

        ssid, ok = QInputDialog.getItem(self, "Select SSID", "Choose an SSID for the heatmap:", ssid_choices, 0, False)

        if ok and ssid:
            filtered_measurements = {}
            for m in self.measurements:
                if m[2] == ssid:
                    key = (m[0], m[1])
                    if key not in filtered_measurements or m[3] > filtered_measurements[key]:
                        filtered_measurements[key] = m[3]
            
            x_coords, y_coords, strengths = zip(*[
                (x, y, strength) for (x, y), strength in filtered_measurements.items()
            ])

            self.plot_heatmap(x_coords, y_coords, strengths, ssid)

    # Updated `plot_heatmap` to include a checkbox for showing/hiding points
    def plot_heatmap(self, x_coords, y_coords, strengths, ssid):
        img = Image.open(self.map_image_path)
        width, height = img.size

        # Create interpolation grid
        grid_step = self.measurement_radius * self.pixels_per_meter
        x_grid = np.arange(0, width, grid_step)
        y_grid = np.arange(0, height, grid_step)
        X, Y = np.meshgrid(x_grid, y_grid)

        # Interpolate signal strengths using Radial Basis Function
        rbf = Rbf(x_coords, y_coords, strengths, function='gaussian', 
                epsilon=self.measurement_radius * self.pixels_per_meter)
        Z = rbf(X, Y)

        # Adjust color scale: higher values (less negative) have darker colors
        Z = -Z  # Flip the scale by negating the values

        # Create a dialog to display the heatmap
        dialog = QDialog(self)
        dialog.setWindowTitle("Heatmap")
        dialog.setGeometry(100, 100, 800, 600)
        dialog.setMinimumSize(400, 300)

        layout = QVBoxLayout()
        dialog.setLayout(layout)

        fig, ax = plt.subplots()
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)

        # QCheckBox to toggle measurement point visibility
        toggle_points = QCheckBox("Show Measurement Points")
        toggle_points.setChecked(True)  # Default to showing points
        toggle_points.stateChanged.connect(lambda: update_heatmap())
        layout.addWidget(toggle_points)

        # ComboBox to select colormap
        cmap_selector = QComboBox()
        cmap_selector.addItems(plt.colormaps())
        cmap_selector.setCurrentText('Greys')
        cmap_selector.currentTextChanged.connect(lambda cmap: update_colormap(cmap))
        layout.addWidget(cmap_selector)

        button_layout = QHBoxLayout()
        save_button = QPushButton("Save Heatmap")
        save_button.clicked.connect(lambda: self.save_heatmap(fig))
        button_layout.addWidget(save_button)
        layout.addLayout(button_layout)

        # Plot the heatmap and measurement points once at the start
        heatmap_display = ax.imshow(Z, extent=[0, width, 0, height], 
                                    origin='upper', cmap='Greys', alpha=0.7)
        # Overlay the map image
        ax.imshow(img, extent=[0, width, 0, height], origin='upper', alpha=0.5)

        # Create color bar (only once)
        cbar = plt.colorbar(heatmap_display, ax=ax)
        cbar.set_label("Signal Strength (- dBm)")

        def update_heatmap():
            # Clear previous measurement points, if any
            for artist in ax.collections:
                artist.remove()

            # Plot measurement points if checkbox is checked
            if toggle_points.isChecked():
                ax.scatter(x_coords, [height - y for y in y_coords], 
                           c='black', s=50, alpha=0.6, edgecolor='white')

            ax.set_title(f"WiFi Signal Strength - {ssid}")
            ax.set_xticks([])  # Remove x-ticks
            ax.set_yticks([])  # Remove y-ticks

            canvas.draw()

        def update_colormap(cmap):
            heatmap_display.set_cmap(cmap)
            canvas.draw()

        # Initial plot
        update_heatmap()
        dialog.exec_()

    def save_heatmap(self, fig):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Heatmap", "", "PNG Files (*.png)", options=options)
        if file_path:
            try:
                fig.savefig(file_path, dpi=300)
                QMessageBox.information(self, "Saved", f"Heatmap saved to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save heatmap: {e}")

    def toggle_scan(self):
        if self.enabled_scans:
            self.enabled_scans = False
            self.scan_button.setText("Click on map to Scan WiFi: OFF")
        else:
            self.enabled_scans = True
            self.scan_button.setText("Click on map to Scan WiFi: ON")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WiFiHeatmapApp()
    window.show()
    sys.exit(app.exec_())
