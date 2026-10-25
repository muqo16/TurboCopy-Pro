import os
import sys
import json
import time
import winsound
import threading
import subprocess
import psutil
import customtkinter as ctk
from tkinter import filedialog, messagebox
from tkinterdnd2 import TkinterDnD, DND_FILES

from engine import CopyJob, format_bytes, format_time, get_path_size
from context_menu import is_context_menu_installed, install_context_menu, uninstall_context_menu

# Modern Dark Palette
BG_WINDOW = "#212121"
CARD_BG = "#2f2f2f"
CARD_INNER = "#262626"
CODE_BG = "#171717"
BORDER_COLOR = "#424242"
ACCENT_GREEN = "#10a37f"
ACCENT_GREEN_HOVER = "#0e906f"
ACCENT_BADGE_BG = "#1a4d40"
BTN_SEC_BG = "#383838"
BTN_SEC_HOVER = "#484848"
BTN_DANGER = "#dc2626"
BTN_DANGER_HOVER = "#b91c1c"
TEXT_PRIMARY = "#ececec"
TEXT_SECONDARY = "#b4b4b4"
TEXT_MUTED = "#8e8ea0"
WARN_AMBER = "#f59e0b"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")


def get_system_drives():
    """Detect all active disk drives and their free/total space."""
    drives = []
    try:
        partitions = psutil.disk_partitions(all=False)
        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                free_gb = usage.free / (1024 ** 3)
                total_gb = usage.total / (1024 ** 3)
                label = f"{part.device} [{part.fstype.upper()}]  Boş: {free_gb:.1f} / {total_gb:.1f} GB"
                drives.append({
                    "mountpoint": part.mountpoint,
                    "device": part.device,
                    "label": label,
                    "free_bytes": usage.free,
                    "total_bytes": usage.total
                })
            except (PermissionError, OSError):
                continue
    except Exception:
        pass
    return drives


class MiniFloatingBar(ctk.CTkToplevel):
    """Mini floating progress bar widget positioned right above the Windows taskbar clock."""
    def __init__(self, master_app):
        super().__init__()
        self.master_app = master_app
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color="#000000")

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = 360, 125
        pos = self._load_position()
        if pos:
            x, y = pos
            # Keep it on screen if the display layout changed since last run
            x = min(max(0, x), max(0, sw - w))
            y = min(max(0, y), max(0, sh - h))
        else:
            x = max(10, sw - w - 20)
            y = max(10, sh - h - 65)
        self._bar_w, self._bar_h = w, h
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.card = ctk.CTkFrame(
            self,
            fg_color=CARD_BG,
            border_width=1,
            border_color=ACCENT_GREEN,
            corner_radius=12
        )
        self.card.pack(fill="both", expand=True, padx=2, pady=2)

        header = ctk.CTkFrame(self.card, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(8, 2))

        title_lbl = ctk.CTkLabel(
            header,
            text="⚡ TurboCopy",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=ACCENT_GREEN
        )
        title_lbl.pack(side="left")

        self.lbl_mini_speed = ctk.CTkLabel(
            header,
            text="0.0 MB/s",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#ffffff",
            fg_color=ACCENT_BADGE_BG,
            corner_radius=8,
            padx=6,
            pady=1
        )
        self.lbl_mini_speed.pack(side="left", padx=8)

        btn_close = ctk.CTkButton(
            header,
            text="✕",
            width=20,
            height=20,
            corner_radius=10,
            fg_color="transparent",
            hover_color=BTN_SEC_HOVER,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
            command=self.hide_widget
        )
        btn_close.pack(side="right")

        btn_expand = ctk.CTkButton(
            header,
            text="↗",
            width=20,
            height=20,
            corner_radius=10,
            fg_color="transparent",
            hover_color=BTN_SEC_HOVER,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=12),
            command=self.restore_main_window
        )
        btn_expand.pack(side="right", padx=2)

        self.btn_mini_pause = ctk.CTkButton(
            header,
            text="⏸ Duraklat",
            width=76,
            height=22,
            corner_radius=11,
            fg_color=BTN_SEC_BG,
            hover_color=WARN_AMBER,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self.master_app._toggle_pause
        )
        self.btn_mini_pause.pack(side="right", padx=4)

        self.lbl_mini_file = ctk.CTkLabel(
            self.card,
            text="Kopyalama başlatılıyor...",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_PRIMARY,
            anchor="w"
        )
        self.lbl_mini_file.pack(fill="x", padx=12, pady=(0, 4))

        self.mini_pbar = ctk.CTkProgressBar(
            self.card,
            height=10,
            corner_radius=5,
            progress_color=ACCENT_GREEN,
            fg_color=CODE_BG
        )
        self.mini_pbar.pack(fill="x", padx=12, pady=(0, 4))
        self.mini_pbar.set(0)

        footer = ctk.CTkFrame(self.card, fg_color="transparent")
        footer.pack(fill="x", padx=12, pady=(0, 8))

        self.lbl_mini_pct = ctk.CTkLabel(
            footer,
            text="%0.0",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=ACCENT_GREEN
        )
        self.lbl_mini_pct.pack(side="left")

        self.lbl_mini_eta = ctk.CTkLabel(
            footer,
            text="Kalan: --:--",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=WARN_AMBER
        )
        self.lbl_mini_eta.pack(side="right")

        self.lbl_mini_bytes = ctk.CTkLabel(
            footer,
            text="0 B / 0 B",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=TEXT_MUTED
        )
        self.lbl_mini_bytes.pack(side="right", padx=8)

        self._last_file_text = "Kopyalama başlatılıyor..."

        # Drag-and-drop the whole bar anywhere on screen; a click without movement
        # still restores the main window.
        self._drag_origin = None
        self._dragged = False
        for widget in (self.card, header, title_lbl, self.lbl_mini_speed,
                       self.lbl_mini_file, self.lbl_mini_pct,
                       self.lbl_mini_eta, self.lbl_mini_bytes):
            self._make_draggable(widget)

    # ---- Drag support -------------------------------------------------
    POS_FILE = os.path.join(
        os.environ.get("APPDATA") or os.path.expanduser("~"), "TurboCopy", "mini_bar_pos.json"
    )

    def _load_position(self):
        try:
            with open(self.POS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return int(data["x"]), int(data["y"])
        except Exception:
            return None

    def _save_position(self):
        try:
            os.makedirs(os.path.dirname(self.POS_FILE), exist_ok=True)
            with open(self.POS_FILE, "w", encoding="utf-8") as f:
                json.dump({"x": self.winfo_x(), "y": self.winfo_y()}, f)
        except Exception:
            pass

    def _make_draggable(self, widget):
        widget.bind("<Button-1>", self._on_drag_start, add="+")
        widget.bind("<B1-Motion>", self._on_drag_move, add="+")
        widget.bind("<ButtonRelease-1>", self._on_drag_end, add="+")

    def _on_drag_start(self, event):
        self._drag_origin = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())
        self._dragged = False

    def _on_drag_move(self, event):
        if not self._drag_origin:
            return
        dx, dy = self._drag_origin
        nx, ny = event.x_root - dx, event.y_root - dy
        if abs(nx - self.winfo_x()) > 2 or abs(ny - self.winfo_y()) > 2:
            self._dragged = True
        # Allow it anywhere, but never fully off-screen
        nx = min(max(-self._bar_w + 60, nx), self.winfo_screenwidth() - 60)
        ny = min(max(0, ny), self.winfo_screenheight() - 30)
        self.geometry(f"+{nx}+{ny}")

    def _on_drag_end(self, event):
        was_drag = self._dragged
        self._drag_origin = None
        self._dragged = False
        if was_drag:
            self._save_position()
        else:
            self.restore_main_window()

    def set_paused(self, is_paused: bool):
        """Immediately update visual state on the Mini Floating Bar when Pause/Resume is clicked."""
        if is_paused:
            self.card.configure(border_color=WARN_AMBER, border_width=2)
            self.mini_pbar.configure(progress_color=WARN_AMBER)
            self.lbl_mini_pct.configure(text_color=WARN_AMBER)
            self.lbl_mini_speed.configure(text="⏸ DURAKLATILDI", fg_color=WARN_AMBER, text_color="#111111")
            self.btn_mini_pause.configure(text="▶ Devam Et", fg_color=WARN_AMBER, text_color="#111111", hover_color="#d97706")
            self.lbl_mini_file.configure(text=f"⏸ [DURAKLATILDI] {self._last_file_text[:26]}", text_color=WARN_AMBER)
        else:
            self.card.configure(border_color=ACCENT_GREEN, border_width=1)
            self.mini_pbar.configure(progress_color=ACCENT_GREEN)
            self.lbl_mini_pct.configure(text_color=ACCENT_GREEN)
            self.lbl_mini_speed.configure(text="▶ Devam Ediyor...", fg_color=ACCENT_BADGE_BG, text_color="#ffffff")
            self.btn_mini_pause.configure(text="⏸ Duraklat", fg_color=BTN_SEC_BG, text_color=TEXT_PRIMARY, hover_color=WARN_AMBER)
            self.lbl_mini_file.configure(text=self._last_file_text, text_color=TEXT_PRIMARY)

    def update_data(self, p):
        pct = p.get("percent", 0)
        self.mini_pbar.set(pct / 100.0)
        self.lbl_mini_pct.configure(text=f"%{pct:.1f}")

        cur_file = p.get("current_file", "")
        if cur_file:
            if len(cur_file) > 38:
                cur_file = cur_file[:35] + "..."
            self._last_file_text = cur_file

        is_paused = p.get("is_paused", False)
        if is_paused:
            self.set_paused(True)
        else:
            self.card.configure(border_color=ACCENT_GREEN, border_width=1)
            self.mini_pbar.configure(progress_color=ACCENT_GREEN)
            self.lbl_mini_pct.configure(text_color=ACCENT_GREEN)
            speed = p.get("speed", 0)
            self.lbl_mini_speed.configure(text=f"{format_bytes(speed)}/s", fg_color=ACCENT_BADGE_BG, text_color="#ffffff")
            self.btn_mini_pause.configure(text="⏸ Duraklat", fg_color=BTN_SEC_BG, text_color=TEXT_PRIMARY, hover_color=WARN_AMBER)
            if cur_file:
                self.lbl_mini_file.configure(text=self._last_file_text, text_color=TEXT_PRIMARY)

        transferred = p.get("bytes_transferred", 0)
        total = p.get("total_bytes", 0)
        self.lbl_mini_bytes.configure(text=f"{format_bytes(transferred)} / {format_bytes(total)}")

        eta = p.get("eta", 0)
        self.lbl_mini_eta.configure(text="Duraklatıldı" if is_paused else f"Kalan: {format_time(eta)}")

    def set_finished(self, success, message):
        if success:
            self.mini_pbar.set(1.0)
            self.lbl_mini_pct.configure(text="%100.0")
            self.lbl_mini_speed.configure(text="Tamamlandı", fg_color=ACCENT_BADGE_BG)
            self.lbl_mini_file.configure(text="✅ Kopyalama başarıyla tamamlandı!")
            self.lbl_mini_eta.configure(text="Bitti")
            self.after(6000, self.hide_widget)
        else:
            self.lbl_mini_file.configure(text=f"❌ {message}")
            self.after(6000, self.hide_widget)

    def restore_main_window(self):
        try:
            self.master_app.deiconify()
            self.master_app.lift()
            self.master_app.focus_force()
        except Exception:
            pass

    def hide_widget(self):
        try:
            self.withdraw()
        except Exception:
            pass


class TurboCopyApp(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        self.title("TurboCopy Pro - Yüksek Hızlı Disk & Dosya Aktarım Aracı")
        # Compact single-screen size that fits any monitor without vertical scrolling
        self.geometry("940x610")
        self.minsize(860, 560)
        self.configure(fg_color=BG_WINDOW)

        icon_path = os.path.join(os.path.dirname(__file__), "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        self.current_job = None
        self.is_copying = False
        self.detected_drives = []
        self.selected_sources = []
        self.mini_widget = None

        self._build_ui()
        self._refresh_drives()

        if len(sys.argv) > 1:
            for arg in sys.argv[1:]:
                if os.path.exists(arg):
                    self._add_source_item(arg)
            self._render_source_list()
            self._log(f"Sağ tık menüsü ile {len(sys.argv)-1} öğe eklendi.")

        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drag_drop)
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)

    def _on_app_close(self):
        if self.current_job and self.is_copying:
            try:
                self.current_job.cancel()
            except Exception:
                pass
        self.destroy()

    def _build_ui(self):
        # Single non-scrolling container so the entire UI always fits on one screen
        main = ctk.CTkFrame(self, fg_color=BG_WINDOW)
        main.pack(fill="both", expand=True, padx=14, pady=10)

        # ---------------- HEADER (Compact) ----------------
        header_frame = ctk.CTkFrame(main, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 8))

        logo_box = ctk.CTkFrame(header_frame, width=34, height=34, corner_radius=9, fg_color=ACCENT_GREEN)
        logo_box.pack(side="left", padx=(0, 10))
        logo_box.pack_propagate(False)
        ctk.CTkLabel(logo_box, text="⚡", font=ctk.CTkFont(size=18), text_color="#ffffff").place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            header_frame,
            text="TurboCopy Pro",
            font=ctk.CTkFont(family="Segoe UI", size=19, weight="bold"),
            text_color=TEXT_PRIMARY
        ).pack(side="left")

        ctk.CTkLabel(
            header_frame,
            text=" v2.0 Turbo ",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=ACCENT_GREEN,
            fg_color=ACCENT_BADGE_BG,
            corner_radius=8
        ).pack(side="left", padx=8)

        ctk.CTkLabel(
            header_frame,
            text="Doğrudan Donanım Hızı Dosya & Klasör Kopyalayıcı",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_MUTED
        ).pack(side="left", padx=4)

        self.btn_context_menu = ctk.CTkButton(
            header_frame,
            text="⚡ Sağ Tık Menüsü",
            height=26,
            corner_radius=13,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            fg_color=BTN_SEC_BG,
            hover_color=ACCENT_GREEN,
            command=self._toggle_context_menu
        )
        self.btn_context_menu.pack(side="right")
        self._update_context_menu_button()

        # ---------------- ROW 1: SIDE-BY-SIDE KAYNAK & HEDEF ----------------
        top_grid = ctk.CTkFrame(main, fg_color="transparent")
        top_grid.pack(fill="x", pady=4)
        top_grid.grid_columnconfigure(0, weight=3)
        top_grid.grid_columnconfigure(1, weight=2)

        # LEFT: KAYNAK LİSTESİ
        src_card = ctk.CTkFrame(top_grid, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        src_card.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        src_top = ctk.CTkFrame(src_card, fg_color="transparent")
        src_top.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkLabel(
            src_top,
            text="📁 KAYNAK",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=TEXT_PRIMARY
        ).pack(side="left")

        self.lbl_src_summary = ctk.CTkLabel(
            src_top,
            text="0 Öğe (0 B)",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=ACCENT_GREEN,
            fg_color=ACCENT_BADGE_BG,
            corner_radius=8,
            padx=6,
            pady=1
        )
        self.lbl_src_summary.pack(side="left", padx=8)

        ctk.CTkButton(
            src_top,
            text="Temizle",
            width=56,
            height=24,
            corner_radius=12,
            fg_color=BTN_SEC_BG,
            hover_color=BTN_SEC_HOVER,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            command=self._clear_sources
        ).pack(side="right", padx=(3, 0))

        ctk.CTkButton(
            src_top,
            text="+ Klasör",
            width=68,
            height=24,
            corner_radius=12,
            fg_color=BTN_SEC_BG,
            hover_color=BTN_SEC_HOVER,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._add_dir_dialog
        ).pack(side="right", padx=3)

        ctk.CTkButton(
            src_top,
            text="+ Dosya",
            width=68,
            height=24,
            corner_radius=12,
            fg_color=ACCENT_GREEN,
            hover_color=ACCENT_GREEN_HOVER,
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._add_files_dialog
        ).pack(side="right", padx=3)

        self.queue_frame = ctk.CTkScrollableFrame(
            src_card,
            height=82,
            corner_radius=8,
            fg_color=CODE_BG,
            border_width=1,
            border_color=BORDER_COLOR
        )
        self.queue_frame.pack(fill="both", expand=True, padx=12, pady=(2, 10))
        self._render_source_list()

        # RIGHT: HEDEF DİSK & KONUM
        dst_card = ctk.CTkFrame(top_grid, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        dst_card.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        dst_top = ctk.CTkFrame(dst_card, fg_color="transparent")
        dst_top.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkLabel(
            dst_top,
            text="🎯 HEDEF DİSK & KONUM",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=TEXT_PRIMARY
        ).pack(side="left")

        ctk.CTkButton(
            dst_top,
            text="🔄 Yenile",
            width=68,
            height=24,
            corner_radius=12,
            fg_color=BTN_SEC_BG,
            hover_color=BTN_SEC_HOVER,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            command=self._refresh_drives
        ).pack(side="right")

        self.drive_dropdown = ctk.CTkComboBox(
            dst_card,
            values=["Sürücüler taranıyor..."],
            height=30,
            corner_radius=8,
            fg_color=CODE_BG,
            border_color=BORDER_COLOR,
            button_color=BTN_SEC_BG,
            button_hover_color=ACCENT_GREEN,
            text_color=TEXT_PRIMARY,
            dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_PRIMARY,
            dropdown_hover_color=ACCENT_GREEN,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._on_drive_selected
        )
        self.drive_dropdown.pack(fill="x", padx=12, pady=(4, 6))

        dst_input_frame = ctk.CTkFrame(dst_card, fg_color="transparent")
        dst_input_frame.pack(fill="x", padx=12, pady=(2, 10))

        self.dst_entry = ctk.CTkEntry(
            dst_input_frame,
            placeholder_text="Hedef klasör yolu (Örn: E:\\)...",
            placeholder_text_color=TEXT_MUTED,
            height=32,
            corner_radius=8,
            fg_color=CODE_BG,
            border_color=BORDER_COLOR,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=11)
        )
        self.dst_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            dst_input_frame,
            text="📂 Seç",
            width=66,
            height=32,
            corner_radius=16,
            fg_color=BTN_SEC_BG,
            hover_color=BTN_SEC_HOVER,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._browse_dest_dir
        ).pack(side="left")

        # ---------------- ROW 2: MOTOR VE SEÇENEKLER (Compact) ----------------
        settings_card = ctk.CTkFrame(main, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        settings_card.pack(fill="x", pady=4)

        row_m1 = ctk.CTkFrame(settings_card, fg_color="transparent")
        row_m1.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkLabel(row_m1, text="⚙️ Motor:", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=TEXT_PRIMARY).pack(side="left", padx=(0, 8))

        self.mode_var = ctk.StringVar(value="turbo_kernel")
        ctk.CTkRadioButton(
            row_m1,
            text="⚡ Turbo Non-Stop DMA (4MB Doğrudan Disk Yazımı)",
            variable=self.mode_var,
            value="turbo_kernel",
            radiobutton_width=16,
            radiobutton_height=16,
            fg_color=ACCENT_GREEN,
            border_color=BORDER_COLOR,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
        ).pack(side="left", padx=(0, 12))

        ctk.CTkRadioButton(
            row_m1,
            text="🚀 Robocopy Motoru (Klasör)",
            variable=self.mode_var,
            value="robocopy",
            radiobutton_width=16,
            radiobutton_height=16,
            fg_color=ACCENT_GREEN,
            border_color=BORDER_COLOR,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
        ).pack(side="left", padx=(0, 14))

        # Power Action & Speed Limit on the right of Row M1
        self.power_action_var = ctk.StringVar(value="Hiçbir Şey Yapma")
        self.combo_power = ctk.CTkComboBox(
            row_m1,
            values=[
                "Hiçbir Şey Yapma",
                "🔌 Bilgisayarı Kapat",
                "💤 Uykuya Al",
                "🔒 Ekranı Kilitle"
            ],
            variable=self.power_action_var,
            width=145,
            height=26,
            corner_radius=8,
            fg_color=CODE_BG,
            border_color=BORDER_COLOR,
            dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=11)
        )
        self.combo_power.pack(side="right")
        ctk.CTkLabel(row_m1, text="Bitince:", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_SECONDARY).pack(side="right", padx=(8, 4))

        self.speed_limit_var = ctk.StringVar(value="🚀 Sınırsız Hız")
        self.combo_speed = ctk.CTkComboBox(
            row_m1,
            values=[
                "🚀 Sınırsız Hız",
                "⚡ 100 MB/s",
                "🎮 30 MB/s (Oyun Modu)",
                "🐢 10 MB/s"
            ],
            variable=self.speed_limit_var,
            width=145,
            height=26,
            corner_radius=8,
            fg_color=CODE_BG,
            border_color=BORDER_COLOR,
            dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=11)
        )
        self.combo_speed.pack(side="right")
        ctk.CTkLabel(row_m1, text="Hız:", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_SECONDARY).pack(side="right", padx=(4, 4))

        row_m2 = ctk.CTkFrame(settings_card, fg_color="transparent")
        row_m2.pack(fill="x", padx=12, pady=(0, 2))
        ctk.CTkLabel(row_m2, text="", width=56).pack(side="left")

        ctk.CTkRadioButton(
            row_m2,
            text="📦 Paket Modu (Tek Dosya Akışı)",
            variable=self.mode_var,
            value="paket",
            radiobutton_width=16,
            radiobutton_height=16,
            fg_color=ACCENT_GREEN,
            border_color=BORDER_COLOR,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
        ).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            row_m2,
            text="Binlerce küçük dosyayı hedefe tek .tar paketi olarak yazar · "
                 "kaynak olarak .tar seçersen paketi geri açar",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=TEXT_SECONDARY
        ).pack(side="left")

        # Checkboxes Row
        opts_frame = ctk.CTkFrame(settings_card, fg_color="transparent")
        opts_frame.pack(fill="x", padx=12, pady=(2, 8))

        self.chk_sync = ctk.CTkCheckBox(
            opts_frame, text="🔄 Akıllı Senkronizasyon (Sadece Değişen)", checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT_GREEN, border_color=BORDER_COLOR, text_color=TEXT_PRIMARY, font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
        )
        self.chk_sync.pack(side="left", padx=(0, 14))

        self.chk_mini_bar = ctk.CTkCheckBox(
            opts_frame, text="Sağ Altta Mini İlerleme", checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT_GREEN, border_color=BORDER_COLOR, text_color=TEXT_PRIMARY, font=ctk.CTkFont(family="Segoe UI", size=11)
        )
        self.chk_mini_bar.select()
        self.chk_mini_bar.pack(side="left", padx=8)

        self.chk_overwrite = ctk.CTkCheckBox(
            opts_frame, text="Üzerine Yaz", checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT_GREEN, border_color=BORDER_COLOR, text_color=TEXT_PRIMARY, font=ctk.CTkFont(family="Segoe UI", size=11)
        )
        self.chk_overwrite.select()
        self.chk_overwrite.pack(side="left", padx=8)

        self.chk_verify = ctk.CTkCheckBox(
            opts_frame, text="MD5 Doğrula", checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT_GREEN, border_color=BORDER_COLOR, text_color=TEXT_PRIMARY, font=ctk.CTkFont(family="Segoe UI", size=11)
        )
        self.chk_verify.pack(side="left", padx=8)

        self.chk_open_dest = ctk.CTkCheckBox(
            opts_frame, text="Bitince Klasörü Aç", checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT_GREEN, border_color=BORDER_COLOR, text_color=TEXT_PRIMARY, font=ctk.CTkFont(family="Segoe UI", size=11)
        )
        self.chk_open_dest.select()
        self.chk_open_dest.pack(side="left", padx=8)

        # ---------------- ROW 3: CANLI PERFORMANS & İLERLEME ----------------
        dashboard_card = ctk.CTkFrame(main, corner_radius=12, fg_color=CARD_INNER, border_width=1, border_color=BORDER_COLOR)
        dashboard_card.pack(fill="x", pady=4)

        dash_top = ctk.CTkFrame(dashboard_card, fg_color="transparent")
        dash_top.pack(fill="x", padx=14, pady=(8, 2))

        self.lbl_status_badge = ctk.CTkLabel(
            dash_top, text="● HAZIR", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=ACCENT_GREEN
        )
        self.lbl_status_badge.pack(side="left")

        self.lbl_current_file = ctk.CTkLabel(
            dash_top, text="Dosyaları ekleyin ve 'Kopyalamayı Başlat'a tıklayın.",
            font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_MUTED, anchor="w"
        )
        self.lbl_current_file.pack(side="left", padx=8, fill="x", expand=True)

        metrics_frame = ctk.CTkFrame(dashboard_card, fg_color="transparent")
        metrics_frame.pack(fill="x", padx=10, pady=2)

        m_speed = ctk.CTkFrame(metrics_frame, fg_color=CARD_BG, corner_radius=8, border_width=1, border_color=BORDER_COLOR)
        m_speed.pack(side="left", fill="both", expand=True, padx=4, pady=2)
        ctk.CTkLabel(m_speed, text="ANLIK HIZ", font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"), text_color=TEXT_MUTED).pack(pady=(4, 0))
        self.lbl_speed = ctk.CTkLabel(m_speed, text="0.0 MB/s", font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"), text_color=ACCENT_GREEN)
        self.lbl_speed.pack(pady=(0, 4))

        m_bytes = ctk.CTkFrame(metrics_frame, fg_color=CARD_BG, corner_radius=8, border_width=1, border_color=BORDER_COLOR)
        m_bytes.pack(side="left", fill="both", expand=True, padx=4, pady=2)
        ctk.CTkLabel(m_bytes, text="AKTARILAN / TOPLAM", font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"), text_color=TEXT_MUTED).pack(pady=(4, 0))
        self.lbl_transferred = ctk.CTkLabel(m_bytes, text="0 B / 0 B", font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"), text_color=TEXT_PRIMARY)
        self.lbl_transferred.pack(pady=(0, 4))

        m_time = ctk.CTkFrame(metrics_frame, fg_color=CARD_BG, corner_radius=8, border_width=1, border_color=BORDER_COLOR)
        m_time.pack(side="left", fill="both", expand=True, padx=4, pady=2)
        ctk.CTkLabel(m_time, text="KALAN SÜRE", font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"), text_color=TEXT_MUTED).pack(pady=(4, 0))
        self.lbl_time = ctk.CTkLabel(m_time, text="Kalan: --:--", font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"), text_color=WARN_AMBER)
        self.lbl_time.pack(pady=(0, 4))

        pbar_frame = ctk.CTkFrame(dashboard_card, fg_color="transparent")
        pbar_frame.pack(fill="x", padx=14, pady=(4, 8))

        self.progress_bar = ctk.CTkProgressBar(pbar_frame, height=12, corner_radius=6, progress_color=ACCENT_GREEN, fg_color=CODE_BG)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.progress_bar.set(0)

        self.lbl_percent = ctk.CTkLabel(pbar_frame, text="%0.0", font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"), text_color=ACCENT_GREEN, width=48)
        self.lbl_percent.pack(side="right")

        # ---------------- ROW 4: KONTROL BUTONLARI ----------------
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill="x", pady=4)

        self.btn_start = ctk.CTkButton(
            btn_frame,
            text="🚀 KOPYALAMAYI BAŞLAT",
            height=40,
            corner_radius=20,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=ACCENT_GREEN,
            hover_color=ACCENT_GREEN_HOVER,
            text_color="#ffffff",
            command=self._start_copy
        )
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_pause = ctk.CTkButton(
            btn_frame,
            text="⏸️ Duraklat",
            width=110,
            height=40,
            corner_radius=20,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color=BTN_SEC_BG,
            hover_color=BTN_SEC_HOVER,
            text_color=TEXT_PRIMARY,
            state="disabled",
            command=self._toggle_pause
        )
        self.btn_pause.pack(side="left", padx=3)

        self.btn_cancel = ctk.CTkButton(
            btn_frame,
            text="⏹️ İptal Et",
            width=100,
            height=40,
            corner_radius=20,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color=BTN_DANGER,
            hover_color=BTN_DANGER_HOVER,
            text_color="#ffffff",
            state="disabled",
            command=self._cancel_copy
        )
        self.btn_cancel.pack(side="left", padx=3)

        self.btn_open_dst = ctk.CTkButton(
            btn_frame,
            text="📂 Klasörü Aç",
            width=110,
            height=40,
            corner_radius=20,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color=BTN_SEC_BG,
            hover_color=BTN_SEC_HOVER,
            text_color=TEXT_PRIMARY,
            command=self._open_destination
        )
        self.btn_open_dst.pack(side="left", padx=(3, 0))

        # ---------------- ROW 5: LOG KONSOLU ----------------
        log_card = ctk.CTkFrame(main, corner_radius=12, fg_color=CODE_BG, border_width=1, border_color=BORDER_COLOR)
        log_card.pack(fill="both", expand=True, pady=(4, 0))

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.pack(fill="x", padx=12, pady=(6, 2))

        ctk.CTkLabel(log_header, text="📋 İşlem Kayıtları", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=TEXT_MUTED).pack(side="left")

        self.btn_view_errors = ctk.CTkButton(
            log_header,
            text="⚠️ Atlanan (0)",
            height=20,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            fg_color=CARD_BG,
            hover_color=WARN_AMBER,
            text_color=TEXT_MUTED,
            state="disabled",
            command=self._show_failed_items_dialog
        )
        self.btn_view_errors.pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            log_header,
            text="Temizle",
            width=60,
            height=20,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            fg_color=BTN_SEC_BG,
            hover_color=BTN_SEC_HOVER,
            text_color=TEXT_MUTED,
            command=self._clear_log
        ).pack(side="right")

        self.log_text = ctk.CTkTextbox(
            log_card,
            height=68,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=CODE_BG,
            text_color="#d1d5db"
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.log_text.configure(state="disabled")

        self._log("TurboCopy Pro hazır. Dosya veya klasörlerinizi sürükleyip bırakarak kopyalamaya başlayabilirsiniz.")

    # ---------------- CONTEXT MENU HANDLERS ----------------
    def _update_context_menu_button(self):
        installed = is_context_menu_installed()
        if installed:
            self.btn_context_menu.configure(
                text="✅ Sağ Tık Aktif (Kaldır)",
                fg_color=ACCENT_BADGE_BG,
                text_color=ACCENT_GREEN
            )
        else:
            self.btn_context_menu.configure(
                text="➕ Sağ Tık Menüsüne Ekle",
                fg_color=BTN_SEC_BG,
                text_color=TEXT_PRIMARY
            )

    def _toggle_context_menu(self):
        installed = is_context_menu_installed()
        if installed:
            uninstall_context_menu()
            self._log("Sağ tık menü entegrasyonu kaldırıldı.")
        else:
            install_context_menu()
            self._log("Sağ tık menü entegrasyonu eklendi.")
        self._update_context_menu_button()

    # ---------------- SOURCE QUEUE MANAGEMENT ----------------
    def _add_files_dialog(self):
        files = filedialog.askopenfilenames(title="Kopyalanacak Dosyaları Seçin (Çoklu Seçim)")
        if files:
            for f in files:
                self._add_source_item(f)
            self._render_source_list()

    def _add_dir_dialog(self):
        d = filedialog.askdirectory(title="Kopyalanacak Klasörü Seçin")
        if d:
            self._add_source_item(d)
            self._render_source_list()

    def _add_source_item(self, path: str):
        path = os.path.normpath(path)
        if any(item["path"] == path for item in self.selected_sources):
            return
        if not os.path.exists(path):
            return

        is_dir = os.path.isdir(path)
        size, _ = get_path_size(path)
        name = os.path.basename(path.rstrip("/\\"))
        if not name:
            name = path

        self.selected_sources.append({
            "path": path,
            "is_dir": is_dir,
            "bytes": size,
            "name": name
        })

    def _remove_source_item(self, index: int):
        if 0 <= index < len(self.selected_sources):
            self.selected_sources.pop(index)
            self._render_source_list()

    def _clear_sources(self):
        self.selected_sources.clear()
        self._render_source_list()

    def _render_source_list(self):
        for widget in self.queue_frame.winfo_children():
            widget.destroy()

        total_bytes = sum(item["bytes"] for item in self.selected_sources)
        count = len(self.selected_sources)
        self.lbl_src_summary.configure(text=f"{count} Öğe ({format_bytes(total_bytes)})")

        if not self.selected_sources:
            empty_box = ctk.CTkFrame(self.queue_frame, fg_color="transparent")
            empty_box.pack(fill="both", expand=True, pady=14)
            ctk.CTkLabel(
                empty_box,
                text="📂 Dosya veya klasörleri buraya sürükleyip bırakın",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=TEXT_MUTED
            ).pack()
            return

        for idx, item in enumerate(self.selected_sources):
            row = ctk.CTkFrame(self.queue_frame, fg_color=CARD_BG, corner_radius=6, height=28)
            row.pack(fill="x", padx=2, pady=2)

            icon = "📁" if item["is_dir"] else "📄"
            ctk.CTkLabel(row, text=icon, font=ctk.CTkFont(size=12)).pack(side="left", padx=(8, 4))

            ctk.CTkLabel(
                row,
                text=item["name"],
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                text_color=TEXT_PRIMARY,
                anchor="w"
            ).pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(
                row,
                text=format_bytes(item["bytes"]),
                font=ctk.CTkFont(family="Segoe UI", size=10),
                text_color=ACCENT_GREEN
            ).pack(side="left", padx=8)

            ctk.CTkButton(
                row,
                text="✕",
                width=20,
                height=20,
                corner_radius=10,
                fg_color="transparent",
                hover_color=BTN_DANGER,
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=10),
                command=lambda i=idx: self._remove_source_item(i)
            ).pack(side="right", padx=4)

    def _on_drag_drop(self, event):
        raw_data = event.data
        if not raw_data:
            return
        try:
            paths = self.tk.splitlist(raw_data)
            added_count = 0
            for p in paths:
                p = p.strip()
                if os.path.exists(p):
                    self._add_source_item(p)
                    added_count += 1
            if added_count > 0:
                self._render_source_list()
                self._log(f"Sürükle-Bırak ile {added_count} öğe eklendi.")
        except Exception as e:
            self._log(f"Sürükle-bırak hatası: {e}", "warning")

    # ---------------- DRIVES & DESTINATION ----------------
    def _refresh_drives(self):
        self.detected_drives = get_system_drives()
        if not self.detected_drives:
            self.drive_dropdown.configure(values=["Disk algılanamadı"])
            return

        drive_labels = [d["label"] for d in self.detected_drives]
        self.drive_dropdown.configure(values=drive_labels)

        if not self.dst_entry.get().strip():
            non_c = [d for d in self.detected_drives if not d["mountpoint"].upper().startswith("C:")]
            chosen = non_c[0] if non_c else self.detected_drives[0]
            self.drive_dropdown.set(chosen["label"])
            self.dst_entry.delete(0, "end")
            self.dst_entry.insert(0, chosen["mountpoint"])
        else:
            self.drive_dropdown.set(drive_labels[0])

    def _on_drive_selected(self, choice):
        for d in self.detected_drives:
            if d["label"] == choice:
                self.dst_entry.delete(0, "end")
                self.dst_entry.insert(0, d["mountpoint"])
                break

    def _browse_dest_dir(self):
        d = filedialog.askdirectory(title="Hedef Klasörü / Diski Seçin")
        if d:
            self.dst_entry.delete(0, "end")
            self.dst_entry.insert(0, os.path.normpath(d))

    def _log(self, text: str, level: str = "info"):
        self.log_text.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ---------------- COPY EXECUTION & CONTROLS ----------------
    def _get_speed_limit_value(self) -> int:
        choice = self.speed_limit_var.get()
        if "100" in choice:
            return 100
        elif "30" in choice:
            return 30
        elif "10" in choice:
            return 10
        return None

    def _check_disk_space(self, total_bytes: int, dst: str) -> bool:
        try:
            dst_root = os.path.splitdrive(os.path.abspath(dst))[0] + "\\"
            usage = psutil.disk_usage(dst_root)
            if usage.free < total_bytes:
                req_str = format_bytes(total_bytes)
                free_str = format_bytes(usage.free)
                answer = messagebox.askyesno(
                    "Disk Alanı Uyarısı",
                    f"Hedef diskte ({dst_root}) yeterli boş alan bulunmuyor!\n\n"
                    f"Gereken Boyut: {req_str}\n"
                    f"Mevcut Boş Alan: {free_str}\n\n"
                    f"Yine de kopyalamayı denemek istiyor musunuz?",
                    icon="warning"
                )
                return answer
        except Exception:
            pass
        return True

    def _start_copy(self):
        if not self.selected_sources:
            messagebox.showerror("Hata", "Lütfen kopyalamak için en az bir dosya veya klasör ekleyin!")
            return

        dst = self.dst_entry.get().strip()
        if not dst:
            messagebox.showerror("Hata", "Lütfen geçerli bir hedef konum veya disk seçin!")
            return

        total_bytes = sum(item["bytes"] for item in self.selected_sources)
        if not self.chk_sync.get() and not self._check_disk_space(total_bytes, dst):
            return

        self.is_copying = True
        self.btn_start.configure(state="disabled")
        self.btn_pause.configure(state="normal", text="⏸️ Duraklat", fg_color=BTN_SEC_BG, text_color=TEXT_PRIMARY)
        self.btn_cancel.configure(state="normal")
        self.lbl_status_badge.configure(text="● KOPYALANIYOR...", text_color=ACCENT_GREEN)
        self.progress_bar.configure(progress_color=ACCENT_GREEN)
        self.progress_bar.set(0)
        self.lbl_percent.configure(text="%0.0", text_color=ACCENT_GREEN)
        self.lbl_speed.configure(text="Başlatılıyor...", text_color=ACCENT_GREEN)

        if self.chk_mini_bar.get():
            if not self.mini_widget or not self.mini_widget.winfo_exists():
                self.mini_widget = MiniFloatingBar(self)
            else:
                self.mini_widget.deiconify()
                self.mini_widget.lift()
            self.mini_widget.set_paused(False)

        sources_paths = [item["path"] for item in self.selected_sources]

        self.current_job = CopyJob(
            sources=sources_paths,
            destination=dst,
            mode=self.mode_var.get(),
            overwrite=bool(self.chk_overwrite.get()),
            verify=bool(self.chk_verify.get()),
            sync_mode=bool(self.chk_sync.get()),
            speed_limit_mbps=self._get_speed_limit_value(),
            threads=32,
            on_progress=self._on_job_progress,
            on_log=self._on_job_log,
            on_finished=self._on_job_finished,
            precomputed_items=list(self.selected_sources)
        )

        thread = threading.Thread(target=self.current_job.run, daemon=True)
        thread.start()

    def _toggle_pause(self):
        if not self.current_job or not self.is_copying:
            return
        if self.current_job.is_paused:
            self.current_job.resume()
            self.btn_pause.configure(text="⏸️ Duraklat", fg_color=BTN_SEC_BG, text_color=TEXT_PRIMARY)
            self.lbl_status_badge.configure(text="● KOPYALANIYOR...", text_color=ACCENT_GREEN)
            self.progress_bar.configure(progress_color=ACCENT_GREEN)
            self.lbl_percent.configure(text_color=ACCENT_GREEN)
            self.lbl_speed.configure(text="Devam Ediyor...", text_color=ACCENT_GREEN)
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.set_paused(False)
        else:
            self.current_job.pause()
            self.btn_pause.configure(text="▶️ Devam Et", fg_color=WARN_AMBER, text_color="#111111")
            self.lbl_status_badge.configure(text="⏸️ DURAKLATILDI", text_color=WARN_AMBER)
            self.progress_bar.configure(progress_color=WARN_AMBER)
            self.lbl_percent.configure(text_color=WARN_AMBER)
            self.lbl_speed.configure(text="⏸ DURAKLATILDI", text_color=WARN_AMBER)
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.set_paused(True)

    def _cancel_copy(self):
        if self.current_job and self.is_copying:
            self.btn_cancel.configure(state="disabled")
            self.btn_pause.configure(state="disabled")
            self.lbl_status_badge.configure(text="● İPTAL EDİLİYOR...", text_color=WARN_AMBER)
            self.current_job.cancel()

    def _on_job_progress(self, p):
        self.after(0, self._update_progress_ui, p)

    def _update_progress_ui(self, p):
        pct = p.get("percent", 0)
        self.progress_bar.set(pct / 100.0)
        self.lbl_percent.configure(text=f"%{pct:.1f}")

        is_paused = p.get("is_paused", False)
        if is_paused:
            self.lbl_speed.configure(text="⏸ DURAKLATILDI", text_color=WARN_AMBER)
            self.progress_bar.configure(progress_color=WARN_AMBER)
            self.lbl_percent.configure(text_color=WARN_AMBER)
        else:
            speed = p.get("speed", 0)
            self.lbl_speed.configure(text=f"{format_bytes(speed)}/s", text_color=ACCENT_GREEN)
            self.progress_bar.configure(progress_color=ACCENT_GREEN)
            self.lbl_percent.configure(text_color=ACCENT_GREEN)

        transferred = p.get("bytes_transferred", 0)
        total = p.get("total_bytes", 0)
        self.lbl_transferred.configure(text=f"{format_bytes(transferred)} / {format_bytes(total)}")

        eta = p.get("eta", 0)
        self.lbl_time.configure(text="Duraklatıldı" if is_paused else f"Kalan: {format_time(eta)}")

        cur_file = p.get("current_file", "")
        if cur_file:
            prefix = "⏸ [DURAKLATILDI] " if is_paused else "Aktarılıyor: "
            self.lbl_current_file.configure(text=f"{prefix}{cur_file}")

        if self.mini_widget and self.mini_widget.winfo_exists() and self.mini_widget.winfo_ismapped():
            self.mini_widget.update_data(p)

    def _on_job_log(self, msg, level):
        self.after(0, self._log, msg, level)

    def _on_job_finished(self, success, message, stats):
        self.after(0, self._handle_finish_ui, success, message, stats)

    def _handle_finish_ui(self, success, message, stats):
        self.is_copying = False
        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="disabled", text="⏸️ Duraklat", fg_color=BTN_SEC_BG, text_color=TEXT_PRIMARY)
        self.btn_cancel.configure(state="disabled")

        duration = stats.get("duration", 0)
        avg_speed = stats.get("avg_speed", 0)
        total_bytes = stats.get("total_bytes", 0)
        failed_items = stats.get("failed_items", [])

        if self.mini_widget and self.mini_widget.winfo_exists():
            self.mini_widget.set_finished(success, message)

        if failed_items:
            self.btn_view_errors.configure(
                state="normal",
                text=f"⚠️ Atlanan ({len(failed_items)})",
                fg_color=BTN_DANGER,
                text_color="#ffffff"
            )
            self.failed_items_cache = failed_items
        else:
            self.btn_view_errors.configure(
                state="disabled",
                text="⚠️ Atlanan (0)",
                fg_color=CARD_BG,
                text_color=TEXT_MUTED
            )

        if success:
            self.lbl_status_badge.configure(text="● TAMAMLANDI", text_color=ACCENT_GREEN)
            self.progress_bar.set(1.0)
            self.lbl_percent.configure(text="%100.0")
            self.lbl_speed.configure(text=f"Ort: {format_bytes(avg_speed)}/s")
            self.lbl_time.configure(text=f"Süre: {format_time(duration)}")
            self.lbl_current_file.configure(text="✅ Tüm öğeler başarıyla tamamlandı!")

            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass

            if self.chk_open_dest.get():
                self._open_destination()

            self._execute_power_action()

            archive = getattr(self.current_job, "archive_path", None)
            archive_line = f"Paket: {archive}\n" if archive else ""

            if not failed_items:
                messagebox.showinfo(
                    "Tamamlandı",
                    f"{message}\n\n"
                    f"{archive_line}"
                    f"Kopyalanan: {len(self.selected_sources)} Öğe\n"
                    f"Toplam Boyut: {format_bytes(total_bytes)}\n"
                    f"Geçen Süre: {format_time(duration)}\n"
                    f"Ortalama Hız: {format_bytes(avg_speed)}/s"
                )
            else:
                messagebox.showwarning(
                    "Tamamlandı (Bazı Öğeler Atlandı)",
                    f"Kopyalama tamamlandı ancak {len(failed_items)} dosya kilitli veya erişilemez olduğu için atlandı.\n\n"
                    f"Detayları görmek için 'Atlanan' butonuna tıklayabilirsiniz."
                )
        else:
            self.lbl_status_badge.configure(text="● DURDURULDU", text_color=BTN_DANGER)
            self.lbl_current_file.configure(text=f"Durduruldu / Hata: {message}")
            if getattr(self.current_job, "device_lost", False):
                # The disk left the bus mid-copy; what was already written is suspect,
                # so say that outright instead of only reporting a failed copy.
                messagebox.showerror(
                    "Hedef Disk Bağlantısı Koptu",
                    f"{message}\n\n"
                    f"Aktarılan: {format_bytes(total_bytes)}\n\n"
                    f"Yapılması gerekenler:\n"
                    f"• Diskin USB kablosunu ve varsa güç adaptörünü yeniden takın.\n"
                    f"• Mümkünse ön panel yerine anakart arkasındaki USB portunu kullanın.\n"
                    f"• Tekrar yazmadan önce 'chkdsk' ile diski kontrol edin; "
                    f"yarım kalan dosyalar bozuk olabilir."
                )
            elif "iptal" not in message.lower():
                messagebox.showerror("Hata", f"Kopyalama tamamlanamadı:\n{message}")

    def _show_failed_items_dialog(self):
        if not hasattr(self, "failed_items_cache") or not self.failed_items_cache:
            return
        lines = [f"• {item['source']}\n  Hata: {item['error']}" for item in self.failed_items_cache[:20]]
        msg = "\n\n".join(lines)
        if len(self.failed_items_cache) > 20:
            msg += f"\n\n... ve {len(self.failed_items_cache) - 20} dosya daha."
        messagebox.showinfo("Atlanan Dosyalar Raporu", msg)

    def _execute_power_action(self):
        action = self.power_action_var.get()
        if "Kapat" in action:
            self._log("⚡ Güç Eylemi: Bilgisayar 30 saniye içinde kapatılacak...")
            subprocess.run(["shutdown.exe", "/s", "/t", "30", "/c", "TurboCopy kopyalama islemi bitti. Bilgisayar kapaniyor..."])
        elif "Uyku" in action:
            self._log("💤 Güç Eylemi: Bilgisayar uyku moduna alınıyor...")
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        elif "Kilitle" in action:
            self._log("🔒 Güç Eylemi: Ekran kilitleniyor...")
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])

    def _open_destination(self):
        dst = self.dst_entry.get().strip()
        if dst and os.path.exists(dst):
            try:
                os.startfile(dst)
            except Exception as e:
                self._log(f"Hedef klasör açılamadı: {e}", "error")


if __name__ == "__main__":
    try:
        app = TurboCopyApp()
        app.mainloop()
    except Exception as e:
        with open("crash.log", "w", encoding="utf-8") as f:
            import traceback
            traceback.print_exc(file=f)
