import csv
import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


BAUD_DEFAULT = 115200
MAX_POINTS = 1500
WAVES = ("DC", "STEP", "SQUARE", "PULSE", "SINE", "TRIANGLE")
COLORS = {
    "surface": "#f5f7fb",
    "panel": "#ffffff",
    "text": "#202124",
    "primary": "#1976d2",
    "primary_hover": "#1565c0",
    "primary_active": "#0d47a1",
    "secondary": "#5e35b1",
    "secondary_hover": "#512da8",
    "success": "#2e7d32",
    "success_hover": "#1b5e20",
    "warning": "#f57c00",
    "warning_hover": "#ef6c00",
    "clear": "#ffb300",
    "clear_hover": "#ffa000",
    "reset": "#3949ab",
    "reset_hover": "#303f9f",
    "danger": "#c62828",
    "danger_hover": "#b71c1c",
    "neutral": "#eceff1",
    "neutral_hover": "#cfd8dc",
}


class SerialWorker:
    def __init__(self, event_queue):
        self.event_queue = event_queue
        self.ser = None
        self.thread = None
        self.running = False
        self.lock = threading.Lock()

    def connect(self, port, baud):
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        self.disconnect()
        self.ser = serial.Serial(port=port, baudrate=baud, timeout=0.1)
        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def disconnect(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.5)
        self.thread = None
        if self.ser:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
        self.ser = None

    def send(self, command):
        with self.lock:
            if not self.ser or not self.ser.is_open:
                raise RuntimeError("Serial port is not connected")
            self.ser.write((command.strip() + "\r\n").encode("ascii"))

    def _read_loop(self):
        while self.running:
            try:
                raw = self.ser.readline()
            except serial.SerialException as exc:
                self.event_queue.put(("status", f"Serial error: {exc}"))
                self.running = False
                break

            if not raw:
                continue

            line = raw.decode("ascii", errors="replace").strip()
            if line:
                self.event_queue.put(("line", line))


class PlotCanvas(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, background="#101418", highlightthickness=0, **kwargs)
        self.zoom_level = 1
        self._last_adc_points = []
        self._last_dac_points = []
        self.bind("<Configure>", lambda _event: self.redraw_cached())
        self.bind("<MouseWheel>", self._on_mouse_wheel)

    def zoom_in(self):
        self.zoom_level = min(self.zoom_level * 2, 64)
        self.redraw_cached()

    def zoom_out(self):
        self.zoom_level = max(self.zoom_level // 2, 1)
        self.redraw_cached()

    def reset_zoom(self):
        self.zoom_level = 1
        self.redraw_cached()

    def redraw_cached(self):
        self.redraw(self._last_adc_points, self._last_dac_points)

    def _on_mouse_wheel(self, event):
        if event.delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()

    def redraw(self, adc_points, dac_points):
        self._last_adc_points = list(adc_points)
        self._last_dac_points = list(dac_points)
        self.delete("all")
        width = max(self.winfo_width(), 10)
        height = max(self.winfo_height(), 10)
        pad_l, pad_r, pad_t, pad_b = 52, 16, 40, 34
        x0, y0 = pad_l, pad_t
        x1, y1 = width - pad_r, height - pad_b

        self.create_rectangle(x0, y0, x1, y1, outline="#2c3440", width=1)

        for i in range(1, 5):
            y = y0 + (y1 - y0) * i / 5
            self.create_line(x0, y, x1, y, fill="#202832")
            mv = int(3300 - 3300 * i / 5)
            self.create_text(8, y, text=f"{mv}", fill="#9aa7b2", anchor="w", font=("Segoe UI", 8))

        for i in range(1, 6):
            x = x0 + (x1 - x0) * i / 6
            self.create_line(x, y0, x, y1, fill="#202832")

        self.create_text(x0, height - 16, text="time", fill="#9aa7b2", anchor="w", font=("Segoe UI", 9))
        self.create_text(8, y0, text="mV", fill="#9aa7b2", anchor="w", font=("Segoe UI", 9))
        self.create_line(x1 - 150, y0 + 10, x1 - 118, y0 + 10, fill="#45a3ff", width=2)
        self.create_text(x1 - 112, y0 + 10, text="ADC PA5", fill="#c8d4df", anchor="w", font=("Segoe UI", 9))
        self.create_line(x1 - 150, y0 + 28, x1 - 118, y0 + 28, fill="#ffbd4a", width=2)
        self.create_text(x1 - 112, y0 + 28, text="DAC PA4", fill="#c8d4df", anchor="w", font=("Segoe UI", 9))

        if not adc_points:
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="No data yet",
                             fill="#697785", font=("Segoe UI", 12))
            return

        adc_points, dac_points = self._visible_points(adc_points, dac_points)
        self._draw_value_header(adc_points, dac_points, x0, y0)

        t_min = adc_points[0][0]
        t_max = adc_points[-1][0]
        if t_max <= t_min:
            t_max = t_min + 1

        def to_xy(point):
            t_us, mv = point
            x = x0 + (x1 - x0) * (t_us - t_min) / (t_max - t_min)
            mv = max(0, min(3300, mv))
            y = y1 - (y1 - y0) * mv / 3300
            return x, y

        self._draw_series(adc_points, to_xy, "#45a3ff")
        self._draw_series(dac_points, to_xy, "#ffbd4a")

    def _visible_points(self, adc_points, dac_points):
        if self.zoom_level <= 1 or len(adc_points) <= 2:
            return adc_points, dac_points

        count = max(2, len(adc_points) // self.zoom_level)
        return adc_points[-count:], dac_points[-count:]

    def _draw_value_header(self, adc_points, dac_points, x0, y0):
        if not adc_points or not dac_points:
            return

        t_us, adc_mv = adc_points[-1]
        _dac_t_us, dac_mv = dac_points[-1]
        error_mv = dac_mv - adc_mv
        span_ms = (adc_points[-1][0] - adc_points[0][0]) / 1000.0
        text = (
            f"t={t_us / 1000000.0:.3f}s   "
            f"ADC={adc_mv} mV   DAC={dac_mv} mV   "
            f"error={error_mv} mV   zoom={self.zoom_level}x   window={span_ms:.1f} ms"
        )
        self.create_text(x0, y0 - 20, text=text, fill="#d7e3ee", anchor="w", font=("Segoe UI", 10, "bold"))

    def _draw_series(self, points, to_xy, color):
        if len(points) < 2:
            return
        coords = []
        for point in points:
            coords.extend(to_xy(point))
        self.create_line(*coords, fill=color, width=2, smooth=False)


class StmControlApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("STM32 RLC Control Interface")
        self.geometry("1120x720")
        self.minsize(980, 620)

        self.events = queue.Queue()
        self.worker = SerialWorker(self.events)
        self.adc_points = deque(maxlen=MAX_POINTS)
        self.dac_points = deque(maxlen=MAX_POINTS)
        self.rows = []
        self.last_line_count = 0
        self.live_active = False

        self._build_ui()
        self.refresh_ports()
        self.after(40, self.process_events)
        self.after(120, self.update_plot)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        self._configure_styles(style)

        self.configure(background=COLORS["surface"])

        root = ttk.Frame(self, padding=10, style="App.TFrame")
        root.pack(fill="both", expand=True)

        left = ttk.Frame(root, width=300, style="App.TFrame")
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        right = ttk.Frame(root, style="App.TFrame")
        right.pack(side="right", fill="both", expand=True)

        self._build_connection(left)
        self._build_controls(left)
        self._build_log(left)

        plot_tools = ttk.Frame(right, style="App.TFrame")
        plot_tools.pack(fill="x", pady=(0, 6))
        ttk.Button(plot_tools, text="Zoom In", command=self.zoom_in, style="Tool.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(plot_tools, text="Zoom Out", command=self.zoom_out, style="Tool.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(plot_tools, text="Reset Zoom", command=self.reset_zoom, style="Reset.TButton").pack(side="left")
        ttk.Label(plot_tools, text="Mouse wheel also zooms the graph").pack(side="right")

        self.plot = PlotCanvas(right)
        self.plot.pack(fill="both", expand=True)

        status = ttk.Frame(right, style="App.TFrame")
        status.pack(fill="x", pady=(8, 0))
        self.status_var = tk.StringVar(value="Disconnected")
        self.latest_var = tk.StringVar(value="ADC: - mV    DAC: - mV")
        ttk.Label(status, textvariable=self.status_var).pack(side="left")
        ttk.Label(status, textvariable=self.latest_var).pack(side="right")

    def _configure_styles(self, style):
        style.configure(".", font=("Segoe UI", 9), background=COLORS["surface"], foreground=COLORS["text"])
        style.configure("App.TFrame", background=COLORS["surface"])
        style.configure("TLabelframe", background=COLORS["surface"], bordercolor="#cfd8dc", relief="solid")
        style.configure("TLabelframe.Label", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI", 9, "bold"))
        style.configure("TLabel", background=COLORS["surface"], foreground=COLORS["text"])
        style.configure("TEntry", fieldbackground=COLORS["panel"], foreground=COLORS["text"], bordercolor="#b0bec5")
        style.configure("TCombobox", fieldbackground=COLORS["panel"], foreground=COLORS["text"], bordercolor="#b0bec5")

        self._button_style(style, "Primary.TButton", COLORS["primary"], "#ffffff", COLORS["primary_hover"], COLORS["primary_active"])
        self._button_style(style, "Secondary.TButton", COLORS["secondary"], "#ffffff", COLORS["secondary_hover"], COLORS["secondary"])
        self._button_style(style, "Success.TButton", COLORS["success"], "#ffffff", COLORS["success_hover"], COLORS["success"])
        self._button_style(style, "Warning.TButton", COLORS["warning"], "#ffffff", COLORS["warning_hover"], COLORS["warning"])
        self._button_style(style, "Clear.TButton", COLORS["clear"], COLORS["text"], COLORS["clear_hover"], "#ff8f00")
        self._button_style(style, "Reset.TButton", COLORS["reset"], "#ffffff", COLORS["reset_hover"], "#283593")
        self._button_style(style, "Danger.TButton", COLORS["danger"], "#ffffff", COLORS["danger_hover"], COLORS["danger"])
        self._button_style(style, "Tool.TButton", "#455a64", "#ffffff", "#37474f", "#263238")
        self._button_style(style, "Neutral.TButton", COLORS["neutral"], COLORS["text"], COLORS["neutral_hover"], "#b0bec5")

    def _button_style(self, style, name, bg, fg, hover, active):
        style.configure(
            name,
            background=bg,
            foreground=fg,
            borderwidth=0,
            focusthickness=0,
            focuscolor=bg,
            padding=(12, 8),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            name,
            background=[("active", hover), ("pressed", active), ("disabled", "#b0bec5")],
            foreground=[("disabled", "#eceff1")],
        )

    def _build_connection(self, parent):
        frame = ttk.LabelFrame(parent, text="Connection", padding=8)
        frame.pack(fill="x")

        ttk.Label(frame, text="Port").grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(frame, textvariable=self.port_var, width=16, state="readonly")
        self.port_box.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(frame, text="Refresh", command=self.refresh_ports, style="Neutral.TButton").grid(row=0, column=2)

        ttk.Label(frame, text="Baud").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.baud_var = tk.StringVar(value=str(BAUD_DEFAULT))
        ttk.Entry(frame, textvariable=self.baud_var, width=12).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))

        buttons = ttk.Frame(frame, style="App.TFrame")
        buttons.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Connect", command=self.connect, style="Success.TButton").pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(buttons, text="Disconnect", command=self.disconnect, style="Neutral.TButton").pack(side="left", fill="x", expand=True, padx=(4, 0))

        frame.columnconfigure(1, weight=1)

    def _build_controls(self, parent):
        frame = ttk.LabelFrame(parent, text="Waveform", padding=8)
        frame.pack(fill="x", pady=(10, 0))

        self.wave_var = tk.StringVar(value="SQUARE")
        self.amp_var = tk.StringVar(value="1300")
        self.offset_var = tk.StringVar(value="0")
        self.freq_var = tk.StringVar(value="20")
        self.duty_var = tk.StringVar(value="50")
        self.ts_var = tk.StringVar(value="1000")
        self.n_var = tk.StringVar(value="1000")

        fields = [
            ("Wave", ttk.Combobox(frame, textvariable=self.wave_var, values=WAVES, state="readonly")),
            ("Amp mV", ttk.Entry(frame, textvariable=self.amp_var)),
            ("Offset mV", ttk.Entry(frame, textvariable=self.offset_var)),
            ("Freq Hz", ttk.Entry(frame, textvariable=self.freq_var)),
            ("Duty %", ttk.Entry(frame, textvariable=self.duty_var)),
            ("Ts us", ttk.Entry(frame, textvariable=self.ts_var)),
            ("Samples", ttk.Entry(frame, textvariable=self.n_var)),
        ]

        for row, (label, widget) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
            widget.grid(row=row, column=1, sticky="ew", pady=3, padx=(8, 0))

        frame.columnconfigure(1, weight=1)

        ttk.Button(frame, text="Apply Settings", command=self.apply_settings, style="Secondary.TButton").grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 3))
        ttk.Button(frame, text="Start Live", command=self.start_live, style="Primary.TButton").grid(row=8, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Button(frame, text="Start Capture", command=self.start_capture, style="Success.TButton").grid(row=9, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Button(frame, text="Stop", command=self.stop, style="Danger.TButton").grid(row=10, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Button(frame, text="Clear Plot", command=self.clear_plot, style="Clear.TButton").grid(row=11, column=0, columnspan=2, sticky="ew", pady=(10, 3))
        ttk.Button(frame, text="Save CSV", command=self.save_csv, style="Warning.TButton").grid(row=12, column=0, columnspan=2, sticky="ew", pady=3)

    def _build_log(self, parent):
        frame = ttk.LabelFrame(parent, text="Serial Log", padding=8)
        frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = tk.Text(frame, height=10, wrap="none")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def refresh_ports(self):
        if list_ports is None:
            self.port_box["values"] = []
            self.log("pyserial is missing. Run run_gui.bat to install it.")
            return
        ports = [port.device for port in list_ports.comports()]
        self.port_box["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def connect(self):
        try:
            self.worker.connect(self.port_var.get(), int(self.baud_var.get()))
            self.status_var.set(f"Connected to {self.port_var.get()}")
            self.log("Connected")
        except Exception as exc:
            messagebox.showerror("Connection failed", str(exc))

    def disconnect(self):
        self.worker.disconnect()
        self.live_active = False
        self.status_var.set("Disconnected")
        self.log("Disconnected")

    def send(self, command):
        try:
            self.worker.send(command)
            self.log(f"> {command}")
        except Exception as exc:
            messagebox.showerror("Send failed", str(exc))

    def apply_settings(self, stop_first=True, restart_live=None):
        if restart_live is None:
            restart_live = self.live_active

        if stop_first:
            self.send("STOP")
            self.live_active = False
            time.sleep(0.15)

        commands = [
            f"SET,WAVE,{self.wave_var.get()}",
            f"SET,AMP,{self.amp_var.get()}",
            f"SET,OFFSET,{self.offset_var.get()}",
            f"SET,FREQ,{self.freq_var.get()}",
            f"SET,DUTY,{self.duty_var.get()}",
            f"SET,TS,{self.ts_var.get()}",
            f"SET,N,{self.n_var.get()}",
        ]
        for command in commands:
            self.send(command)
            time.sleep(0.02)

        if restart_live:
            self.clear_plot()
            time.sleep(0.05)
            self.send("START,LIVE")
            self.live_active = True

    def start_live(self):
        try:
            ts_us = int(self.ts_var.get())
        except ValueError:
            self.log("Live mode requires Ts us to be a number. Example: 1000")
            return

        if ts_us < 1000:
            self.log("Live mode cannot use Ts us below 1000 because UART/GUI cannot stream that fast.")
            self.log("For live graph: set Ts us = 1000 and use lower frequencies like 20-50 Hz.")
            self.log("For fast RLC behavior: keep Ts us = 10, but use Start Capture instead of Start Live.")
            return

        self.apply_settings(stop_first=True, restart_live=False)
        self.clear_plot()
        time.sleep(0.05)
        self.send("START,LIVE")
        self.live_active = True

    def start_capture(self):
        try:
            ts_us = int(self.ts_var.get())
            samples = int(self.n_var.get())
        except ValueError:
            self.log("Capture mode requires numeric Ts us and Samples values. Example: Ts us=10, Samples=1000")
            return

        duration_ms = (ts_us * samples) / 1000.0
        self.log(f"Capture will record {samples} samples at Ts={ts_us} us, total window={duration_ms:.2f} ms.")
        if ts_us >= 1000:
            self.log("Note: Ts us is slow for RLC transient. Use Ts us=10 or 20 for fast RLC behavior.")

        self.apply_settings(stop_first=True, restart_live=False)
        self.clear_plot()
        time.sleep(0.05)
        self.send("START,CAPTURE")
        self.live_active = False

    def stop(self):
        self.send("STOP")
        self.live_active = False

    def zoom_in(self):
        self.plot.zoom_in()

    def zoom_out(self):
        self.plot.zoom_out()

    def reset_zoom(self):
        self.plot.reset_zoom()

    def clear_plot(self):
        self.adc_points.clear()
        self.dac_points.clear()
        self.rows.clear()
        self.latest_var.set("ADC: - mV    DAC: - mV")
        self.plot.redraw([], [])

    def save_csv(self):
        if not self.rows:
            messagebox.showinfo("No data", "There is no data to save yet.")
            return

        default_name = f"stm32_capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return

        with Path(path).open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["time_us", "adc_mV", "dac_mV"])
            writer.writerows(self.rows)
        self.log(f"Saved {path}")

    def process_events(self):
        processed = 0
        while processed < 500:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "line":
                self.handle_line(value)
            elif kind == "status":
                self.status_var.set(value)
                self.log(value)
            processed += 1

        self.after(40, self.process_events)

    def handle_line(self, line):
        if line == "ERR,LIVE_TS_MIN_1000US":
            self.log(line)
            self.log("Live mode icin Ts us en az 1000 olmali.")
            self.log("Ts us=10 gibi hizli olcumlerde Start Capture kullan.")
            return

        if line.startswith("DATA,"):
            parts = line.split(",")
            if len(parts) == 4:
                try:
                    t_us = int(parts[1])
                    adc_mv = int(parts[2])
                    dac_mv = int(parts[3])
                except ValueError:
                    self.log(line)
                    return

                self.adc_points.append((t_us, adc_mv))
                self.dac_points.append((t_us, dac_mv))
                self.rows.append((t_us, adc_mv, dac_mv))
                self.latest_var.set(f"ADC: {adc_mv} mV    DAC: {dac_mv} mV")
                return

        self.log(line)

    def update_plot(self):
        current_count = len(self.rows)
        if current_count != self.last_line_count:
            self.last_line_count = current_count
            self.plot.redraw(list(self.adc_points), list(self.dac_points))
        self.after(120, self.update_plot)

    def log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_close(self):
        self.worker.disconnect()
        self.destroy()


if __name__ == "__main__":
    app = StmControlApp()
    app.mainloop()
