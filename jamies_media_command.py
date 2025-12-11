
import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, Menu
from urllib.parse import quote_plus
from difflib import SequenceMatcher

import requests
import customtkinter as ctk
from plexapi.server import PlexServer

# --- CONFIGURATION ---
CONFIG_FILE = "collection_manager_config.json"
COLLECTIONS_DATA_FILE = "collections_data.json"

# Set Theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PlexManagerPro(ctk.CTk):
    def __init__(self):
        super().__init__()

        # --- WINDOW SETUP ---
        self.title("Jamie's Media Command") 
        self.geometry("1300x850") 
        
        # Thread Locking & Control
        self.data_lock = threading.Lock()
        
        # Split Cancel Events (Process vs Monitor)
        self.process_cancel_event = threading.Event()
        self.monitor_cancel_event = threading.Event()
        
        # Concurrency Guard
        self.monitor_running = threading.Event()

        # Load Data
        self.config = self.load_config()
        self.collections_data = self.load_collections_data()
        self.auto_refresh_active = tk.BooleanVar(value=False)

        # Grid Layout
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # --- SIDEBAR ---
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(5, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="JAMIE'S HUB", font=ctk.CTkFont(size=22, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.btn_nav_create = self.create_nav_btn("Create Collection", self.show_create, 1)
        self.btn_nav_monitor = self.create_nav_btn("Active Monitor", self.show_monitor, 2)
        self.btn_nav_trakt = self.create_nav_btn("Trakt Import", self.show_trakt, 3)
        self.btn_nav_settings = self.create_nav_btn("Settings", self.show_settings, 4)

        # --- FRAMES ---
        self.frame_create = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.frame_monitor = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.frame_trakt = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.frame_settings = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")

        self.setup_create_ui()
        self.setup_monitor_ui()
        self.setup_trakt_ui()
        self.setup_settings_ui()

        # Init
        self.select_frame_by_name("create")
        self.after(60000, self.auto_refresh_loop)

    def create_nav_btn(self, text, command, row):
        btn = ctk.CTkButton(self.sidebar_frame, corner_radius=0, height=40, border_spacing=10, text=text,
                            fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                            anchor="w", command=command)
        btn.grid(row=row, column=0, sticky="ew")
        return btn

    def select_frame_by_name(self, name):
        self.btn_nav_create.configure(fg_color=("gray75", "gray25") if name == "create" else "transparent")
        self.btn_nav_monitor.configure(fg_color=("gray75", "gray25") if name == "monitor" else "transparent")
        self.btn_nav_trakt.configure(fg_color=("gray75", "gray25") if name == "trakt" else "transparent")
        self.btn_nav_settings.configure(fg_color=("gray75", "gray25") if name == "settings" else "transparent")

        self.frame_create.grid_forget()
        self.frame_monitor.grid_forget()
        self.frame_trakt.grid_forget()
        self.frame_settings.grid_forget()

        if name == "create": self.frame_create.grid(row=0, column=1, sticky="nsew")
        elif name == "monitor": self.frame_monitor.grid(row=0, column=1, sticky="nsew")
        elif name == "trakt": self.frame_trakt.grid(row=0, column=1, sticky="nsew")
        elif name == "settings": self.frame_settings.grid(row=0, column=1, sticky="nsew")

    def show_create(self): self.select_frame_by_name("create")
    def show_monitor(self): self.select_frame_by_name("monitor")
    def show_trakt(self): self.select_frame_by_name("trakt")
    def show_settings(self): self.select_frame_by_name("settings")

    # ================= UI SETUP =================

    def setup_create_ui(self):
        ctk.CTkLabel(self.frame_create, text="Start New Collection", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20, padx=20, anchor="w")

        form_frame = ctk.CTkFrame(self.frame_create)
        form_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(form_frame, text="Media Type:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=20, pady=10, sticky="w")
        self.var_media_type = tk.StringVar(value="movie")
        radio_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        radio_frame.grid(row=0, column=1, padx=20, pady=10, sticky="w")
        ctk.CTkRadioButton(radio_frame, text="Movies (Radarr)", variable=self.var_media_type, value="movie").pack(side="left", padx=10)
        ctk.CTkRadioButton(radio_frame, text="TV Shows (Sonarr)", variable=self.var_media_type, value="show").pack(side="left", padx=10)

        ctk.CTkLabel(form_frame, text="Collection Name:", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=20, pady=10, sticky="w")
        self.entry_col_name = ctk.CTkEntry(form_frame, width=400, placeholder_text="e.g. 90s Action Movies")
        self.entry_col_name.grid(row=1, column=1, padx=20, pady=10, sticky="w")

        ctk.CTkLabel(self.frame_create, text="Paste List (Title (Year) - one per line):", font=ctk.CTkFont(weight="bold")).pack(padx=20, pady=(20, 5), anchor="w")
        self.text_movie_list = ctk.CTkTextbox(self.frame_create, height=250)
        self.text_movie_list.pack(fill="x", padx=20, pady=5)

        # Progress UI
        btn_frame = ctk.CTkFrame(self.frame_create, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=10)
        
        self.btn_run = ctk.CTkButton(btn_frame, text="Process Collection", height=40, font=ctk.CTkFont(size=16, weight="bold"), command=self.start_initial_run)
        self.btn_run.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        self.btn_cancel = ctk.CTkButton(btn_frame, text="STOP", height=40, width=80, fg_color="red", hover_color="darkred", state="disabled", command=self.trigger_cancel)
        self.btn_cancel.pack(side="right")

        self.lbl_status = ctk.CTkLabel(self.frame_create, text="Ready", text_color="gray")
        self.lbl_status.pack(pady=5)

        self.log_create = ctk.CTkTextbox(self.frame_create, height=120, fg_color="black", text_color="#00FF00", font=("Consolas", 12))
        self.log_create.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.log_create.configure(state="disabled")

    def setup_monitor_ui(self):
        ctk.CTkLabel(self.frame_monitor, text="Active Monitor", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20, padx=20, anchor="w")

        controls = ctk.CTkFrame(self.frame_monitor)
        controls.pack(fill="x", padx=20, pady=10)
        self.btn_refresh = ctk.CTkButton(controls, text="Refresh Status (Plex Scan)", command=self.refresh_monitor_status)
        self.btn_refresh.pack(side="left", padx=10, pady=10)
        ctk.CTkCheckBox(controls, text="Auto-scan every 10 mins", variable=self.auto_refresh_active).pack(side="left", padx=20, pady=10)
        ctk.CTkButton(controls, text="Delete Selected", fg_color="red", hover_color="darkred", command=self.delete_collection_data).pack(side="right", padx=10, pady=10)

        # Split View
        paned = tk.PanedWindow(self.frame_monitor, orient=tk.HORIZONTAL, sashwidth=4, bg="#2b2b2b")
        paned.pack(fill="both", expand=True, padx=20, pady=10)

        # Monitor Tree (Left)
        left_frame = ctk.CTkFrame(paned)
        ctk.CTkLabel(left_frame, text="Collections (Right-click to Force Scan)", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        
        style = ttk.Style()
        style.theme_use("clam")
        bg_color = self._apply_appearance_mode(ctk.ThemeManager.theme["CTkFrame"]["fg_color"])
        text_color = self._apply_appearance_mode(ctk.ThemeManager.theme["CTkLabel"]["text_color"])
        selected_color = self._apply_appearance_mode(ctk.ThemeManager.theme["CTkButton"]["fg_color"])

        style.configure("Treeview", background=bg_color, foreground=text_color, fieldbackground=bg_color, borderwidth=0, rowheight=30)
        style.map('Treeview', background=[('selected', selected_color)], foreground=[('selected', 'white')])
        style.configure("Treeview.Heading", background="#232323", foreground="white", relief="flat", font=('Arial', 10, 'bold'))

        self.monitor_tree = ttk.Treeview(left_frame, columns=("name", "type", "progress"), show="headings")
        self.monitor_tree.heading("name", text="Collection Name")
        self.monitor_tree.heading("type", text="Type")
        self.monitor_tree.heading("progress", text="Progress")
        self.monitor_tree.column("name", width=200)
        self.monitor_tree.column("type", width=80)
        self.monitor_tree.column("progress", width=120)
        
        sb_left = ctk.CTkScrollbar(left_frame, orientation="vertical", command=self.monitor_tree.yview)
        self.monitor_tree.configure(yscrollcommand=sb_left.set)
        self.monitor_tree.pack(side="left", fill="both", expand=True)
        sb_left.pack(side="right", fill="y")
        self.monitor_tree.bind("<<TreeviewSelect>>", self.on_monitor_select)
        
        # Context Menu
        self.monitor_menu = Menu(self, tearoff=0)
        self.monitor_menu.add_command(label="Force Re-scan This Collection", command=self.force_rescan_single)
        self.monitor_tree.bind("<Button-3>", self.show_monitor_context) 

        paned.add(left_frame, width=450)

        # Items Tree (Right)
        right_frame = ctk.CTkFrame(paned)
        right_header = ctk.CTkFrame(right_frame, fg_color="transparent")
        right_header.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(right_header, text="Items Detail", font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkButton(right_header, text="Copy List", width=100, height=24, command=self.copy_monitor_list).pack(side="right")

        self.items_tree = ttk.Treeview(right_frame, columns=("title", "year", "status"), show="headings")
        self.items_tree.heading("title", text="Title")
        self.items_tree.heading("year", text="Year")
        self.items_tree.heading("status", text="Status")
        self.items_tree.column("title", width=250)
        self.items_tree.column("year", width=60)
        self.items_tree.column("status", width=100)
        self.items_tree.tag_configure("complete", foreground="#00FF00")
        self.items_tree.tag_configure("pending", foreground="#FFFF00")

        sb_right = ctk.CTkScrollbar(right_frame, orientation="vertical", command=self.items_tree.yview)
        self.items_tree.configure(yscrollcommand=sb_right.set)
        self.items_tree.pack(side="left", fill="both", expand=True)
        sb_right.pack(side="right", fill="y")

        paned.add(right_frame, width=500)

        self.log_monitor = ctk.CTkTextbox(self.frame_monitor, height=80, fg_color="black", text_color="#00FF00", font=("Consolas", 12))
        self.log_monitor.pack(fill="x", padx=20, pady=10)
        self.log_monitor.configure(state="disabled")
        self.update_monitor_ui()

    def setup_trakt_ui(self):
        ctk.CTkLabel(self.frame_trakt, text="Trakt Discovery", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20, padx=20, anchor="w")
        search_frame = ctk.CTkFrame(self.frame_trakt)
        search_frame.pack(fill="x", padx=20, pady=10)

        self.var_trakt_type = tk.StringVar(value="movie")
        ctk.CTkRadioButton(search_frame, text="Movie Lists", variable=self.var_trakt_type, value="movie").pack(side="left", padx=20, pady=10)
        ctk.CTkRadioButton(search_frame, text="TV Lists", variable=self.var_trakt_type, value="show").pack(side="left", padx=20, pady=10)
        
        self.entry_trakt_search = ctk.CTkEntry(search_frame, width=300, placeholder_text="Search (e.g. Marvel, Best 2024)")
        self.entry_trakt_search.pack(side="left", padx=10, pady=10, fill="x", expand=True)
        ctk.CTkButton(search_frame, text="Search", width=100, command=self.search_trakt).pack(side="left", padx=20, pady=10)

        paned_frame = ctk.CTkFrame(self.frame_trakt, fg_color="transparent")
        paned_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        left_frame = ctk.CTkFrame(paned_frame)
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))
        ctk.CTkLabel(left_frame, text="1. Search Results (Select one)", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        
        self.trakt_tree = ttk.Treeview(left_frame, columns=("name", "likes", "count", "id"), show="headings")
        self.trakt_tree.heading("name", text="List Name")
        self.trakt_tree.heading("likes", text="Likes")
        self.trakt_tree.heading("count", text="Items")
        self.trakt_tree.column("name", width=200)
        self.trakt_tree.column("likes", width=60)
        self.trakt_tree.column("count", width=60)
        self.trakt_tree.column("id", width=0, stretch=tk.NO)
        self.trakt_tree.pack(fill="both", expand=True, padx=5, pady=5)
        self.trakt_tree.bind("<<TreeviewSelect>>", self.on_trakt_list_select)

        right_frame = ctk.CTkFrame(paned_frame)
        right_frame.pack(side="right", fill="both", expand=True, padx=(10, 0))
        ctk.CTkLabel(right_frame, text="2. Preview Content", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        
        self.preview_tree = ttk.Treeview(right_frame, columns=("title", "year"), show="headings")
        self.preview_tree.heading("title", text="Title")
        self.preview_tree.heading("year", text="Year")
        self.preview_tree.column("title", width=250)
        self.preview_tree.column("year", width=80)
        self.preview_tree.pack(fill="both", expand=True, padx=5, pady=5)

        ctk.CTkButton(self.frame_trakt, text="IMPORT SELECTED LIST", height=50, font=ctk.CTkFont(size=16, weight="bold"), command=self.import_trakt_list).pack(fill="x", padx=20, pady=20)

    def setup_settings_ui(self):
        ctk.CTkLabel(self.frame_settings, text="Settings", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20, padx=20, anchor="w")
        container = ctk.CTkScrollableFrame(self.frame_settings)
        container.pack(fill="both", expand=True, padx=20, pady=10)

        def add_section(title):
            ctk.CTkLabel(container, text=title, font=ctk.CTkFont(size=16, weight="bold"), text_color="#1f538d").pack(anchor="w", pady=(20, 10))

        def add_field(label, key, is_pass=False):
            f = ctk.CTkFrame(container, fg_color="transparent")
            f.pack(fill="x", pady=5)
            ctk.CTkLabel(f, text=label, width=150, anchor="w").pack(side="left")
            e = ctk.CTkEntry(f, width=400, show="*" if is_pass else "")
            e.insert(0, self.config.get(key, ""))
            e.pack(side="left", fill="x", expand=True)
            return e

        add_section("Plex")
        self.entry_plex_url = add_field("URL:", "plex_url")
        self.entry_plex_token = add_field("Token:", "plex_token", True)
        self.entry_plex_movie_lib = add_field("Movie Library:", "plex_movie_lib")
        self.entry_plex_tv_lib = add_field("TV Library:", "plex_tv_lib")

        add_section("Radarr (Movies)")
        self.entry_radarr_url = add_field("URL:", "radarr_url")
        self.entry_radarr_key = add_field("API Key:", "radarr_key", True)
        self.entry_radarr_root = add_field("Root Folder:", "radarr_root")
        self.entry_radarr_profile = add_field("Quality ID:", "radarr_profile")

        add_section("Sonarr (TV Shows)")
        self.entry_sonarr_url = add_field("URL:", "sonarr_url")
        self.entry_sonarr_key = add_field("API Key:", "sonarr_key", True)
        self.entry_sonarr_root = add_field("Root Folder:", "sonarr_root")
        self.entry_sonarr_profile = add_field("Quality ID:", "sonarr_profile")

        add_section("Trakt")
        self.entry_trakt_id = add_field("Client ID:", "trakt_client_id")

        ctk.CTkButton(container, text="Save Configuration", height=40, command=self.save_config).pack(pady=30, fill="x")

    # ================= LOGIC =================
    
    def log(self, msg):
        timestamp = time.strftime("[%H:%M:%S] ")
        full = timestamp + msg + "\n"
        def _append(widget):
            try:
                widget.configure(state='normal')
                widget.insert(tk.END, full)
                widget.see(tk.END)
                widget.configure(state='disabled')
            except Exception as e: print(f"Log Error: {e}")
        self.after(0, _append, self.log_create)
        self.after(0, _append, self.log_monitor)

    def load_config(self):
        default = {"plex_url": "http://127.0.0.1:32400", "plex_token": "", "plex_movie_lib": "Movies", "plex_tv_lib": "TV Shows",
                   "radarr_url": "http://127.0.0.1:7878", "radarr_key": "", "radarr_root": "/movies", "radarr_profile": "1",
                   "sonarr_url": "http://127.0.0.1:8989", "sonarr_key": "", "sonarr_root": "/tv", "sonarr_profile": "1", "trakt_client_id": ""}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f: return {**default, **json.load(f)}
            except Exception as e: print(f"Config Error: {e}")
        return default

    def load_collections_data(self):
        if os.path.exists(COLLECTIONS_DATA_FILE):
            try:
                with open(COLLECTIONS_DATA_FILE, 'r') as f:
                    data = json.load(f)
                if "_schema" not in data: data["_schema"] = {"version": 1}
                return data
            except Exception as e: print(f"Data Error: {e}")
        return {"_schema": {"version": 1}}

    def save_config(self):
        for key, entry in {
            "plex_url": self.entry_plex_url, "plex_token": self.entry_plex_token,
            "plex_movie_lib": self.entry_plex_movie_lib, "plex_tv_lib": self.entry_plex_tv_lib,
            "radarr_url": self.entry_radarr_url, "radarr_key": self.entry_radarr_key,
            "radarr_root": self.entry_radarr_root, "radarr_profile": self.entry_radarr_profile,
            "sonarr_url": self.entry_sonarr_url, "sonarr_key": self.entry_sonarr_key,
            "sonarr_root": self.entry_sonarr_root, "sonarr_profile": self.entry_sonarr_profile,
            "trakt_client_id": self.entry_trakt_id
        }.items():
            self.config[key] = entry.get()
        with open(CONFIG_FILE, 'w') as f: json.dump(self.config, f, indent=4)
        messagebox.showinfo("Saved", "Configuration saved!")

    def save_collections_data(self):
        with self.data_lock:
            with open(COLLECTIONS_DATA_FILE, 'w') as f: json.dump(self.collections_data, f, indent=4)

    def trigger_cancel(self):
        self.process_cancel_event.set() # Only cancel processing, not monitoring
        self.log("Stopping... please wait for current item.")
        self.btn_cancel.configure(state="disabled", text="Stopping...")

    def update_status_label(self, text):
        self.lbl_status.configure(text=text)

    def get_plex_lib(self, m_type: str):
        try:
            plex = PlexServer(self.config['plex_url'], self.config['plex_token'])
            section_name = self.config['plex_movie_lib'] if m_type == "movie" else self.config['plex_tv_lib']
            return plex.library.section(section_name)
        except Exception as e:
            self.log(f"[Plex] Error: {e}")
            return None

    # --- PROCESS LOGIC ---
    def start_initial_run(self):
        self.btn_run.configure(state="disabled")
        self.btn_cancel.configure(state="normal", text="STOP")
        self.process_cancel_event.clear()
        threading.Thread(target=self.run_process, daemon=True).start()

    def run_process(self):
        try:
            col = self.entry_col_name.get().strip()
            m_type = self.var_media_type.get()
            raw = self.text_movie_list.get("1.0", tk.END).strip().split('\n')
            
            items = []
            for line in raw:
                line = line.strip()
                if line and '(' in line and line.endswith(')'):
                    try:
                        p = line.rsplit('(', 1)
                        items.append({"title": p[0].strip(), "year": int(p[1].replace(')', '')), "found": False})
                    except (ValueError, TypeError) as e:
                        self.log(f"Skipping malformed line: '{line}' ({e})")
            
            if not col or not items: 
                self.log("Error: Missing Name or valid items.")
                return

            items_to_scan = [] 

            with self.data_lock:
                if col in self.collections_data:
                    existing = self.collections_data[col]
                    if existing.get("type") != m_type:
                        self.log(f"[ERROR] Mismatch! '{col}' is {existing.get('type')}. Cannot add {m_type}.")
                        return 
                    existing_items = existing['items']
                    existing_keys = set((i['title'].lower(), i['year']) for i in existing_items)
                    added_count = 0
                    for new_item in items:
                        if (new_item['title'].lower(), new_item['year']) not in existing_keys:
                            existing_items.append(new_item)
                            items_to_scan.append(new_item)
                            added_count += 1
                    self.log(f"Merging: Added {added_count} new items.")
                else:
                    self.collections_data[col] = {"type": m_type, "items": items}
                    items_to_scan = items
                    self.log(f"Created '{col}' with {len(items)} items.")

            self.save_collections_data()
            self.after(0, self.update_monitor_ui)
            
            if not items_to_scan:
                self.log("No new items to process.")
                return

            self.log(f"Processing {len(items_to_scan)} items...")
            
            lib = self.get_plex_lib(m_type)
            if not lib: return
            
            misses = []
            total = len(items_to_scan)

            for i, item in enumerate(items_to_scan):
                if self.process_cancel_event.is_set(): break
                if item['found']: continue

                self.after(0, lambda t=f"Processing {i+1} / {total}...": self.update_status_label(t))
                
                # Network check outside lock
                found = self.find_plex(lib, item['title'], item['year'])
                
                if found:
                    try: found.addCollection(col)
                    except Exception as e:
                        self.log(f"Plex tag error {item['title']}: {e}")
                        continue 

                with self.data_lock:
                    if found:
                        item['found'] = True
                        self.log(f" -> Found: {item['title']}")
                    else:
                        item['found'] = False
                        misses.append(item)
            
            self.save_collections_data()
            self.after(0, self.update_monitor_ui)
            self.after(0, lambda: self.update_status_label("Ready"))
            
            if misses and not self.process_cancel_event.is_set():
                self.log(f"Sending {len(misses)} missing items to downloader...")
                if m_type == "movie": self.process_radarr(misses)
                else: self.process_sonarr(misses)
            
            if self.process_cancel_event.is_set(): self.log("Operation Cancelled.")
            else: self.log("Complete.")
            
        except Exception as e: self.log(f"Process Error: {e}")
        finally: 
            self.after(0, lambda: self.btn_run.configure(state="normal"))
            self.after(0, lambda: self.btn_cancel.configure(state="disabled"))

    # --- FUZZY SEARCH ---
    def find_plex(self, lib, title, year):
        try:
            results = lib.search(title=title)
            for m in results:
                if hasattr(m, 'year') and m.year == year and m.title.lower() == title.lower(): return m
            for m in results:
                if m.title.lower() == title.lower(): return m
            for m in results:
                if hasattr(m, 'year') and m.year == year:
                    ratio = SequenceMatcher(None, m.title.lower(), title.lower()).ratio()
                    if ratio > 0.9: 
                        self.log(f"[Fuzzy Match] '{title}' -> '{m.title}' ({ratio:.2f})")
                        return m
            return None
        except Exception as e:
            self.log(f"Search Error: {e}")
            return None

    def process_radarr(self, misses):
        url = (self.config.get('radarr_url') or '').rstrip('/')
        head = {"X-Api-Key": self.config.get('radarr_key') or ''}
        if not url: return
        
        for m in misses:
            if self.process_cancel_event.is_set(): break
            try:
                resp = requests.get(f"{url}/api/v3/movie/lookup?term={quote_plus(m['title'] + ' ' + str(m['year']))}", headers=head, timeout=15)
                if resp.status_code != 200:
                    self.log(f"[Radarr Error] Lookup failed: {resp.status_code}")
                    continue
                look = resp.json()
                if not look: 
                    self.log(f"  [Radarr Miss] {m['title']}")
                    continue
                c = next((x for x in look if x.get('year') == m['year']), look[0])
                if c.get("id"): 
                    self.log(f"  [Radarr Skip] {m['title']}")
                    continue
                pl = {"tmdbId": c.get("tmdbId"), "title": c.get("title"), "year": c.get("year"), "qualityProfileId": int(self.config['radarr_profile']), "rootFolderPath": self.config['radarr_root'], "monitored": True, "addOptions": {"searchForMovie": True}}
                
                resp = requests.post(f"{url}/api/v3/movie", headers=head, json=pl, timeout=15)
                if resp.status_code == 201:
                    self.log(f"  [Radarr Added] {m['title']}")
                else:
                    self.log(f"  [Radarr Error] {m['title']} ({resp.status_code}: {resp.text[:100]})")
            except Exception as e: self.log(f"Radarr Error: {e}")

    def process_sonarr(self, misses):
        url = (self.config.get('sonarr_url') or '').rstrip('/')
        head = {"X-Api-Key": self.config.get('sonarr_key') or ''}
        if not url: return

        for s in misses:
            if self.process_cancel_event.is_set(): break
            try:
                resp = requests.get(f"{url}/api/v3/series/lookup?term={quote_plus(s['title'])}", headers=head, timeout=15)
                if resp.status_code != 200:
                    self.log(f"[Sonarr Error] Lookup failed: {resp.status_code}")
                    continue
                look = resp.json()
                if not look: 
                    self.log(f"  [Sonarr Miss] {s['title']}")
                    continue
                c = next((x for x in look if x.get('year') == s['year']), look[0])
                if c.get("id"): 
                    self.log(f"  [Sonarr Skip] {s['title']}")
                    continue
                pl = {"tvdbId": c.get("tvdbId"), "title": c.get("title"), "titleSlug": c.get("titleSlug"), "qualityProfileId": int(self.config['sonarr_profile']), "rootFolderPath": self.config['sonarr_root'], "monitored": True, "addOptions": {"searchForMissingEpisodes": True}}
                resp = requests.post(f"{url}/api/v3/series", headers=head, json=pl, timeout=15)
                if resp.status_code == 201:
                    self.log(f"  [Sonarr Added] {s['title']}")
                else:
                    self.log(f"  [Sonarr Error] {s['title']} ({resp.status_code}: {resp.text[:100]})")
            except Exception as e: self.log(f"Sonarr Error: {e}")

    # --- MONITOR EXTRAS ---
    def show_monitor_context(self, event):
        item = self.monitor_tree.identify_row(event.y)
        if item:
            self.monitor_tree.selection_set(item)
            self.monitor_menu.post(event.x_root, event.y_root)

    def force_rescan_single(self):
        sel = self.monitor_tree.selection()
        if not sel: return
        self.monitor_cancel_event.clear()
        col_name = self.monitor_tree.item(sel[0])['values'][0]
        self.monitor_tree.item(sel[0], tags=("scanning",))
        self.monitor_tree.tag_configure("scanning", background="#004400")
        self.log(f"Forcing re-scan for '{col_name}'...")
        threading.Thread(target=self.run_monitor, args=(col_name,), daemon=True).start()

    def refresh_monitor_status(self): 
        self.monitor_cancel_event.clear()
        self.btn_refresh.configure(state="disabled")
        threading.Thread(target=self.run_monitor, daemon=True).start()
    
    def run_monitor(self, target_col=None):
        if self.monitor_running.is_set():
            self.log("Monitor already running; skipping.")
            return
        
        self.monitor_running.set()
        try:
            self.log("Scanning Plex...")
            plex = PlexServer(self.config['plex_url'], self.config['plex_token'])
            mlib = plex.library.section(self.config['plex_movie_lib'])
            tlib = plex.library.section(self.config['plex_tv_lib'])
            
            with self.data_lock:
                to_scan = [(k,v) for k,v in self.collections_data.items() if not k.startswith('_') and (target_col is None or k == target_col)]

            for name, data in to_scan:
                if self.monitor_cancel_event.is_set(): break
                lib = mlib if data['type'] == 'movie' else tlib
                for item in data['items']:
                    if self.monitor_cancel_event.is_set(): break
                    if not item['found']:
                        f = self.find_plex(lib, item['title'], item['year'])
                        
                        if f:
                            try: f.addCollection(name)
                            except Exception as e: 
                                self.log(f"Tag error: {e}"); continue
                            
                            with self.data_lock:
                                item['found'] = True
                                self.log(f"New Arrival: {item['title']}")
            
            self.save_collections_data()
            self.after(0, self.update_monitor_ui)
            self.log("Scan complete.")
        except Exception as e: self.log(f"Monitor Error: {e}")
        finally: 
            self.monitor_running.clear()
            self.after(0, lambda: self.btn_refresh.configure(state="normal"))

    # --- BOILERPLATE ---
    def update_monitor_ui(self):
        for i in self.monitor_tree.get_children(): self.monitor_tree.delete(i)
        with self.data_lock:
            for n, d in self.collections_data.items():
                if n.startswith("_"): continue
                tot = len(d['items'])
                fnd = sum(1 for x in d['items'] if x['found'])
                perc = int((fnd/tot)*100) if tot else 0
                self.monitor_tree.insert("", tk.END, values=(n, d['type'], f"{fnd} / {tot} ({perc}%)"))

    def on_monitor_select(self, event):
        sel = self.monitor_tree.selection()
        if not sel: return
        col = self.monitor_tree.item(sel[0])['values'][0]
        for i in self.items_tree.get_children(): self.items_tree.delete(i)
        with self.data_lock:
            for item in self.collections_data.get(col, {}).get('items', []):
                status = "Complete" if item['found'] else "Pending"
                tag = "complete" if item['found'] else "pending"
                self.items_tree.insert("", tk.END, values=(item['title'], item['year'], status), tags=(tag,))

    def copy_monitor_list(self):
        sel = self.monitor_tree.selection()
        if not sel: return
        col = self.monitor_tree.item(sel[0])['values'][0]
        with self.data_lock: items = self.collections_data.get(col, {}).get('items', [])
        text = f"Collection: {col}\n" + "\n".join([f"{'[FOUND]' if i['found'] else '[MISSING]'} {i['title']} ({i['year']})" for i in items])
        self.clipboard_clear(); self.clipboard_append(text); messagebox.showinfo("Copied", "Copied to clipboard.")

    def delete_collection_data(self):
        sel = self.monitor_tree.selection()
        if not sel: return
        n = self.monitor_tree.item(sel[0])['values'][0]
        if messagebox.askyesno("Delete", f"Stop monitoring '{n}'?"):
            with self.data_lock: del self.collections_data[n]
            self.save_collections_data(); self.update_monitor_ui(); 
            for i in self.items_tree.get_children(): self.items_tree.delete(i)

    def auto_refresh_loop(self):
        if self.auto_refresh_active.get(): self.refresh_monitor_status()
        self.after(600000, self.auto_refresh_loop)

    # --- TRAKT SEARCH (Thread Safe) ---
    def search_trakt(self):
        query = self.entry_trakt_search.get().strip()
        client_id = self.config.get("trakt_client_id")
        if not query or not client_id: return
        for i in self.trakt_tree.get_children(): self.trakt_tree.delete(i)
        
        def _search():
            try:
                url = f"https://api.trakt.tv/search/list?query={quote_plus(query)}"
                resp = requests.get(url, headers={"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": client_id}, timeout=15)
                if resp.status_code != 200:
                    self.log(f"Trakt Search Error: {resp.status_code}")
                    return
                data = resp.json()
                rows = []
                for item in data:
                    lst = item.get("list", {})
                    rows.append((lst.get("name"), lst.get("likes", 0), lst.get("item_count"), f"{lst.get('user', {}).get('ids', {}).get('slug')}|{lst.get('ids', {}).get('trakt')}"))
                self.after(0, lambda: [self.trakt_tree.insert("", tk.END, values=r) for r in rows])
            except Exception as e: self.log(f"Trakt Error: {e}")
        threading.Thread(target=_search, daemon=True).start()

    def on_trakt_list_select(self, event):
        sel = self.trakt_tree.selection()
        if not sel: return
        comp = self.trakt_tree.item(sel[0])['values'][3]
        for i in self.preview_tree.get_children(): self.preview_tree.delete(i)
        threading.Thread(target=self.load_trakt_preview, args=(comp,), daemon=True).start()

    def load_trakt_preview(self, comp):
        u, l_id = comp.split("|")
        cid = self.config.get("trakt_client_id")
        try:
            url = f"https://api.trakt.tv/users/{u}/lists/{l_id}/items"
            resp = requests.get(url, headers={"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": cid}, timeout=15)
            if resp.status_code != 200:
                self.log(f"Trakt Preview Error: {resp.status_code}")
                return
            data = resp.json()
            items = []
            w_type = self.var_trakt_type.get()
            for e in data:
                if w_type == "movie" and e.get("type") == "movie":
                    m = e.get("movie", {})
                    if m.get("title") and m.get("year"): items.append((m['title'], m['year']))
                elif w_type == "show" and e.get("type") == "show":
                    s = e.get("show", {})
                    if s.get("title") and s.get("year"): items.append((s['title'], s['year']))
            self.after(0, lambda: [self.preview_tree.insert("", tk.END, values=i) for i in items])
        except Exception as e: self.log(f"Trakt Preview Exception: {e}")

    def import_trakt_list(self):
        items = [f"{self.preview_tree.item(c)['values'][0]} ({self.preview_tree.item(c)['values'][1]})" for c in self.preview_tree.get_children()]
        if not items: return
        sel = self.trakt_tree.selection()
        if sel: self.entry_col_name.delete(0, tk.END); self.entry_col_name.insert(0, self.trakt_tree.item(sel[0])['values'][0])
        self.text_movie_list.delete("1.0", tk.END); self.text_movie_list.insert("1.0", "\n".join(items))
        self.var_media_type.set(self.var_trakt_type.get()); self.show_create()

if __name__ == "__main__":
    app = PlexManagerPro()
    app.mainloop()