import os
import json
import math
import subprocess
import threading
import sys
import shlex
from pathlib import Path
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox

APP_TITLE = "Codex Token Checker"
APP_VERSION = "1.0.0"
RUN_KEY_NAME = "CodexTokenChecker"
USER_HOME = Path(os.environ.get("USERPROFILE", str(Path.home())))
SESSIONS_DIR = USER_HOME / ".codex" / "sessions"
SETTINGS_DIR = Path(os.environ.get("APPDATA", str(USER_HOME))) / "CodexTokenChecker"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "tray_enabled": False,
    "tray_mode": "five_hour",   # five_hour / max_rate
    "notify_reset": True,
    "notify_cache": True,
    "auto_refresh": True,
    "refresh_minutes": 5,
    "startup_enabled": False,
}


def empty_usage():
    return {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }


def normalize_usage(raw):
    out = empty_usage()
    if not isinstance(raw, dict):
        return out
    for key in out:
        try:
            out[key] = int(raw.get(key, 0) or 0)
        except (TypeError, ValueError):
            out[key] = 0
    return out


def add_usage(total, usage):
    for key in total:
        total[key] += int(usage.get(key, 0) or 0)


def usage_delta(newer, older):
    result = empty_usage()
    for key in result:
        result[key] = max(0, int(newer.get(key, 0) or 0) - int(older.get(key, 0) or 0))
    return result


def number(value):
    return f"{int(value):,}"


def compact_number(value):
    value = int(value)
    for suffix, divisor in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if abs(value) >= divisor:
            return f"{value / divisor:.1f}{suffix}"
    return str(value)


def parse_timestamp(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            # Unix seconds / milliseconds both tolerate
            if value > 10_000_000_000:
                value = value / 1000
            return datetime.fromtimestamp(value)
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def get_session_date(file: Path):
    try:
        parts = file.parts
        idx = parts.index("sessions")
        return datetime(int(parts[idx + 1]), int(parts[idx + 2]), int(parts[idx + 3])).date()
    except Exception:
        try:
            return datetime.fromtimestamp(file.stat().st_mtime).date()
        except OSError:
            return datetime.now().date()


def basename_project(cwd):
    if not cwd:
        return "(プロジェクト不明)"
    try:
        name = Path(str(cwd)).name
        return name or str(cwd)
    except Exception:
        return str(cwd)


def extract_metadata(data, payload, current):
    candidates = []
    if isinstance(data, dict):
        candidates.append(data)
    if isinstance(payload, dict):
        candidates.append(payload)
        for key in ("metadata", "meta", "session", "info"):
            obj = payload.get(key)
            if isinstance(obj, dict):
                candidates.append(obj)
    for obj in candidates:
        if not current.get("cwd"):
            for key in ("cwd", "working_directory", "workspace", "workspace_path", "project_path"):
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    current["cwd"] = value.strip(); break
        if not current.get("model"):
            for key in ("model", "model_name", "model_slug"):
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    current["model"] = value.strip(); break
    return current


def _rate_values(rate_limits):
    vals = []
    if not isinstance(rate_limits, dict):
        return vals
    stack = [rate_limits]
    while stack:
        obj = stack.pop()
        if isinstance(obj, dict):
            if obj.get("used_percent") is not None:
                try: vals.append(float(obj["used_percent"]))
                except Exception: pass
            stack.extend(v for v in obj.values() if isinstance(v, (dict, list)))
        elif isinstance(obj, list):
            stack.extend(obj)
    return vals


def load_sessions():
    if not SESSIONS_DIR.exists():
        return []
    sessions = []
    for file in SESSIONS_DIR.rglob("*.jsonl"):
        latest_usage = None
        latest_rate = None
        latest_plan = None
        max_rate = None
        reset_at = None
        metadata = {"cwd": None, "model": None}
        samples = []
        try:
            with open(file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    try: data = json.loads(line)
                    except json.JSONDecodeError: continue
                    payload = data.get("payload")
                    metadata = extract_metadata(data, payload if isinstance(payload, dict) else {}, metadata)
                    if not isinstance(payload, dict) or payload.get("type") != "token_count":
                        continue
                    info = payload.get("info")
                    usage = info.get("total_token_usage") if isinstance(info, dict) else None
                    if isinstance(usage, dict):
                        latest_usage = normalize_usage(usage)
                        ts = parse_timestamp(data.get("timestamp") or payload.get("timestamp"))
                        if ts is not None: samples.append((ts, latest_usage.copy()))
                    rate_limits = payload.get("rate_limits")
                    if isinstance(rate_limits, dict):
                        vals = _rate_values(rate_limits)
                        if vals: max_rate = max(vals)
                        primary = rate_limits.get("primary")
                        if isinstance(primary, dict):
                            if primary.get("used_percent") is not None:
                                try: latest_rate = float(primary.get("used_percent"))
                                except Exception: pass
                            for k in ("resets_at", "reset_at", "reset_time", "resetsAt"):
                                if primary.get(k):
                                    reset_at = parse_timestamp(primary.get(k)); break
                        if rate_limits.get("plan_type"):
                            latest_plan = rate_limits.get("plan_type")
        except (OSError, PermissionError):
            continue
        if latest_usage is None: continue
        try: mtime = file.stat().st_mtime
        except OSError: mtime = 0
        samples.sort(key=lambda x: x[0])
        sessions.append({
            "file": file, "usage": latest_usage, "rate": latest_rate,
            "max_rate": max_rate, "reset_at": reset_at, "plan": latest_plan,
            "date": get_session_date(file), "mtime": mtime,
            "cwd": metadata.get("cwd"), "project": basename_project(metadata.get("cwd")),
            "model": metadata.get("model") or "(モデル不明)", "samples": samples,
        })
    return sessions


def recent_usage_from_samples(sessions, since_dt):
    total = empty_usage()
    for session in sessions:
        previous = empty_usage()
        for ts, cumulative in session.get("samples") or []:
            delta = usage_delta(cumulative, previous)
            if ts >= since_dt: add_usage(total, delta)
            previous = cumulative
    return total


def load_settings():
    out = DEFAULT_SETTINGS.copy()
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict): out.update({k: data[k] for k in out if k in data})
    except Exception:
        pass
    return out


def save_settings(settings):
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


class StatCard(ttk.Frame):
    def __init__(self, parent, title):
        super().__init__(parent, style="Card.TFrame", padding=10)
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.total = ttk.Label(self, text="0", style="BigNumber.TLabel"); self.total.grid(row=1, column=0, sticky="w")
        self.total_full = ttk.Label(self, text="0 tokens", style="Muted.TLabel"); self.total_full.grid(row=2, column=0, sticky="w", pady=(0, 5))
        self.detail_labels = {}
        for r, (label, key) in enumerate((("Input","input_tokens"),("Cached Input","cached_input_tokens"),("Output","output_tokens"),("Reasoning","reasoning_output_tokens"),("Sessions","sessions")), start=3):
            row = ttk.Frame(self, style="Card.TFrame"); row.grid(row=r, column=0, sticky="ew")
            row.columnconfigure(1, weight=1)
            ttk.Label(row, text=label, style="Muted.TLabel").grid(row=0,column=0,sticky="w")
            val = ttk.Label(row, text="0", style="Value.TLabel"); val.grid(row=0,column=1,sticky="e")
            self.detail_labels[key] = val
    def update_values(self, usage, sessions):
        self.total.config(text=compact_number(usage["total_tokens"]))
        self.total_full.config(text=f"{number(usage['total_tokens'])} tokens")
        for key in ("input_tokens","cached_input_tokens","output_tokens","reasoning_output_tokens"):
            self.detail_labels[key].config(text=number(usage[key]))
        self.detail_labels["sessions"].config(text=number(sessions))


class MiniCard(ttk.Frame):
    def __init__(self, parent, title):
        super().__init__(parent, style="Card.TFrame", padding=10)
        ttk.Label(self, text=title, style="Muted.TLabel").pack(anchor="w")
        self.value = ttk.Label(self, text="0", style="MediumNumber.TLabel"); self.value.pack(anchor="w")
        self.caption = ttk.Label(self, text="tokens", style="Muted.TLabel"); self.caption.pack(anchor="w")
    def set_value(self, value):
        self.value.config(text=compact_number(value)); self.caption.config(text=f"{number(value)} tokens")


class SevenDayChart(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#FFFFFF", highlightthickness=0, **kwargs); self.values=[]
        self.bind("<Configure>", lambda e: self.redraw())
    def set_values(self, values): self.values=values; self.redraw()
    def redraw(self):
        self.delete("all")
        if not self.values: return
        w=max(200,self.winfo_width()); h=max(120,self.winfo_height()); left,right,top,bottom=35,12,12,28
        pw,ph=w-left-right,h-top-bottom; maxv=max([v for _,v in self.values]+[1]); slot=pw/max(len(self.values),1); bw=min(34,slot*.55)
        self.create_line(left,top+ph,w-right,top+ph,fill="#D1D5DB")
        for i,(label,val) in enumerate(self.values):
            x=left+slot*i+slot/2; bh=ph*(val/maxv); y=top+ph-bh
            self.create_rectangle(x-bw/2,y,x+bw/2,top+ph,fill="#4F46E5",outline="")
            self.create_text(x,top+ph+14,text=label,fill="#6B7280",font=("Segoe UI",7))
            if val: self.create_text(x,max(8,y-7),text=compact_number(val),fill="#374151",font=("Segoe UI",7,"bold"))


class ToggleSwitch(tk.Canvas):
    """Small modern ON/OFF switch bound to a BooleanVar."""
    def __init__(self, parent, variable, command=None, width=46, height=25, **kwargs):
        bg = kwargs.pop("bg", "#FFFFFF")
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0, cursor="hand2", **kwargs)
        self.variable = variable
        self.command = command
        self.switch_width = width
        self.switch_height = height
        self.bind("<Button-1>", self._toggle)
        self.variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _toggle(self, _event=None):
        self.variable.set(not bool(self.variable.get()))
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        on = bool(self.variable.get())
        h = self.switch_height - 5
        y0, y1 = 2, 2 + h
        x0, x1 = 2, self.switch_width - 2
        r = h / 2
        fill = "#22C55E" if on else "#D1D5DB"
        self.create_rectangle(x0+r, y0, x1-r, y1, fill=fill, outline=fill)
        self.create_oval(x0, y0, x0+2*r, y1, fill=fill, outline=fill)
        self.create_oval(x1-2*r, y0, x1, y1, fill=fill, outline=fill)
        knob_r = r - 2
        cx = (x1-r) if on else (x0+r)
        self.create_oval(cx-knob_r, (y0+y1)/2-knob_r, cx+knob_r, (y0+y1)/2+knob_r, fill="#FFFFFF", outline="#FFFFFF")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("880x610")
        self.minsize(800, 540)
        self.configure(bg="#F6F7FB")
        self.sessions=[]; self.settings=load_settings(); self._auto_job=None
        self._previous_rate=None; self._cache_notified=False; self._tray_icon=None
        self._configure_style(); self._build_ui(); self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self.update_data)
        self.after(500, self._apply_saved_runtime_settings)

    def _configure_style(self):
        s=ttk.Style(self)
        try: s.theme_use("clam")
        except tk.TclError: pass
        s.configure("Root.TFrame",background="#F6F7FB"); s.configure("Header.TFrame",background="#F6F7FB"); s.configure("Card.TFrame",background="#FFFFFF")
        s.configure("Title.TLabel",background="#F6F7FB",foreground="#111827",font=("Segoe UI",17,"bold")); s.configure("Subtitle.TLabel",background="#F6F7FB",foreground="#6B7280",font=("Segoe UI",8))
        s.configure("Rate.TLabel",background="#FFFFFF",foreground="#111827",font=("Segoe UI",25,"bold")); s.configure("RateCaption.TLabel",background="#FFFFFF",foreground="#6B7280",font=("Segoe UI",9))
        s.configure("Plan.TLabel",background="#FFFFFF",foreground="#374151",font=("Segoe UI",9,"bold")); s.configure("CardTitle.TLabel",background="#FFFFFF",foreground="#374151",font=("Segoe UI",10,"bold"))
        s.configure("BigNumber.TLabel",background="#FFFFFF",foreground="#111827",font=("Segoe UI",17,"bold")); s.configure("MediumNumber.TLabel",background="#FFFFFF",foreground="#111827",font=("Segoe UI",14,"bold"))
        s.configure("Muted.TLabel",background="#FFFFFF",foreground="#6B7280",font=("Segoe UI",8)); s.configure("Value.TLabel",background="#FFFFFF",foreground="#111827",font=("Segoe UI",8,"bold"))
        s.configure("Footer.TLabel",background="#F6F7FB",foreground="#6B7280",font=("Segoe UI",8)); s.configure("Accent.TButton",font=("Segoe UI",9,"bold"),padding=(11,6))
        s.configure("TNotebook",background="#F6F7FB",borderwidth=0); s.configure("TNotebook.Tab",padding=(14,7),font=("Segoe UI",9,"bold")); s.configure("Treeview",rowheight=25,font=("Segoe UI",8)); s.configure("Treeview.Heading",font=("Segoe UI",8,"bold"))
        s.configure("Section.TLabelframe",background="#FFFFFF",padding=10); s.configure("Section.TLabelframe.Label",background="#FFFFFF",foreground="#374151",font=("Segoe UI",10,"bold"))
        s.configure("SettingsTitle.TLabel",background="#F6F7FB",foreground="#111827",font=("Segoe UI",14,"bold"))
        s.configure("SettingsSection.TLabel",background="#FFFFFF",foreground="#374151",font=("Segoe UI",10,"bold"))
        s.configure("SettingsMain.TLabel",background="#FFFFFF",foreground="#111827",font=("Segoe UI",10,"bold"))
        s.configure("SettingsDesc.TLabel",background="#FFFFFF",foreground="#9CA3AF",font=("Segoe UI",8))
        s.configure("Choice.TButton",font=("Segoe UI",9,"bold"),padding=(12,9))
        s.configure("ChoiceSelected.TButton",font=("Segoe UI",9,"bold"),padding=(12,9),foreground="#2563EB")
        s.configure("Small.TButton",font=("Segoe UI",8,"bold"),padding=(9,5))

    def _build_ui(self):
        root=ttk.Frame(self,style="Root.TFrame",padding=10); root.pack(fill="both",expand=True)
        header=ttk.Frame(root,style="Header.TFrame"); header.pack(fill="x"); header.columnconfigure(0,weight=1)
        ttk.Label(header,text=APP_TITLE,style="Title.TLabel").grid(row=0,column=0,sticky="w")
        controls=ttk.Frame(header,style="Header.TFrame"); controls.grid(row=0,column=1,sticky="e")
        self.updated_header=ttk.Label(controls,text="最終更新: --",style="Subtitle.TLabel"); self.updated_header.pack(side="left",padx=(0,8))
        ttk.Button(controls,text="↻ 更新",style="Accent.TButton",command=self.update_data).pack(side="left")
        ttk.Label(header,text="CodexのローカルJSONLログを読み取り専用で分析",style="Subtitle.TLabel").grid(row=1,column=0,columnspan=2,sticky="w",pady=(0,4))
        self.notebook=ttk.Notebook(root); self.notebook.pack(fill="both",expand=True)
        self.summary_tab=ttk.Frame(self.notebook,style="Root.TFrame",padding=(0,5,0,0)); self.projects_tab=ttk.Frame(self.notebook,style="Root.TFrame",padding=(0,5,0,0)); self.sessions_tab=ttk.Frame(self.notebook,style="Root.TFrame",padding=(0,5,0,0)); self.settings_tab=ttk.Frame(self.notebook,style="Root.TFrame",padding=(0,5,0,0))
        for tab,text in ((self.summary_tab,"サマリ"),(self.projects_tab,"プロジェクト別"),(self.sessions_tab,"セッション別"),(self.settings_tab,"⚙ 設定")): self.notebook.add(tab,text=text)
        self._build_summary(); self._build_projects(); self._build_sessions(); self._build_settings()
        self.footer=ttk.Label(root,text="※ Rate Limitはログ内の used_percent。契約上の残りトークンそのものではありません。",style="Footer.TLabel"); self.footer.pack(fill="x",pady=(5,0))

    def _build_summary(self):
        top=ttk.Frame(self.summary_tab,style="Root.TFrame"); top.pack(fill="x",pady=(0,7)); top.columnconfigure(0,weight=1); top.columnconfigure(1,weight=1)
        left=ttk.Frame(top,style="Card.TFrame",padding=11); left.grid(row=0,column=0,sticky="nsew",padx=(0,4)); left.columnconfigure(0,weight=1); left.columnconfigure(1,weight=1)
        rb=ttk.Frame(left,style="Card.TFrame"); rb.grid(row=0,column=0,sticky="nsew")
        ttk.Label(rb,text="Rate Limit 使用率",style="RateCaption.TLabel").pack(anchor="w"); self.rate_label=ttk.Label(rb,text="-- %",style="Rate.TLabel"); self.rate_label.pack(anchor="w"); self.plan_label=ttk.Label(rb,text="プラン: --",style="Plan.TLabel"); self.plan_label.pack(anchor="w")
        sb=ttk.Frame(left,style="Card.TFrame"); sb.grid(row=0,column=1,sticky="nsew",padx=(8,0)); ttk.Label(sb,text="安全性",style="RateCaption.TLabel").pack(anchor="w"); ttk.Label(sb,text="読み取り専用",style="MediumNumber.TLabel").pack(anchor="w"); ttk.Label(sb,text=str(SESSIONS_DIR),style="Muted.TLabel",wraplength=190).pack(anchor="w")
        right=ttk.Frame(top,style="Card.TFrame",padding=11); right.grid(row=0,column=1,sticky="nsew",padx=(4,0)); right.columnconfigure(0,weight=1); right.columnconfigure(1,weight=1)
        self.hour_card=MiniCard(right,"直近1時間"); self.hour_card.grid(row=0,column=0,sticky="nsew",padx=(0,4)); self.five_hour_card=MiniCard(right,"直近5時間"); self.five_hour_card.grid(row=0,column=1,sticky="nsew",padx=(4,0))
        cards=ttk.Frame(self.summary_tab,style="Root.TFrame"); cards.pack(fill="x",pady=(0,7)); cards.columnconfigure(0,weight=1); cards.columnconfigure(1,weight=1)
        self.today_card=StatCard(cards,"今日"); self.week_card=StatCard(cards,"過去7日"); self.month_card=StatCard(cards,"過去30日"); self.all_card=StatCard(cards,"全期間")
        for i,c in enumerate((self.today_card,self.week_card,self.month_card,self.all_card)):
            r=i//2; col=i%2; c.grid(row=r,column=col,sticky="nsew",padx=(0 if col==0 else 4,4 if col==0 else 0),pady=(0 if r==0 else 4,4 if r==0 else 0))
        lower=ttk.Frame(self.summary_tab,style="Root.TFrame"); lower.pack(fill="both",expand=True); lower.columnconfigure(0,weight=2); lower.columnconfigure(1,weight=1); lower.rowconfigure(0,weight=1)
        cc=ttk.Frame(lower,style="Card.TFrame",padding=10); cc.grid(row=0,column=0,sticky="nsew",padx=(0,4)); ttk.Label(cc,text="日別（過去7日）",style="CardTitle.TLabel").pack(anchor="w"); self.chart=SevenDayChart(cc,height=125); self.chart.pack(fill="both",expand=True)
        ic=ttk.Frame(lower,style="Card.TFrame",padding=10); ic.grid(row=0,column=1,sticky="nsew",padx=(4,0)); ttk.Label(ic,text="最新セッション",style="CardTitle.TLabel").pack(anchor="w"); self.latest_name=ttk.Label(ic,text="--",style="Value.TLabel",wraplength=230); self.latest_name.pack(anchor="w",pady=(7,3)); self.latest_meta=ttk.Label(ic,text="--",style="Muted.TLabel",wraplength=230,justify="left"); self.latest_meta.pack(anchor="w")

    def _build_projects(self):
        card=ttk.Frame(self.projects_tab,style="Card.TFrame",padding=9); card.pack(fill="both",expand=True); ttk.Label(card,text="プロジェクト別トークン使用量",style="CardTitle.TLabel").pack(anchor="w",pady=(0,6))
        cols=("project","total","input","cached","output","reasoning","sessions"); self.project_tree=ttk.Treeview(card,columns=cols,show="headings")
        heads={"project":"プロジェクト","total":"Total","input":"Input","cached":"Cached Input","output":"Output","reasoning":"Reasoning","sessions":"Sessions"}; widths={"project":190,"total":100,"input":100,"cached":110,"output":85,"reasoning":90,"sessions":65}
        for c in cols: self.project_tree.heading(c,text=heads[c]); self.project_tree.column(c,width=widths[c],anchor="w" if c=="project" else "e")
        sy=ttk.Scrollbar(card,orient="vertical",command=self.project_tree.yview); sx=ttk.Scrollbar(card,orient="horizontal",command=self.project_tree.xview); self.project_tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set); self.project_tree.pack(fill="both",expand=True); sx.pack(fill="x"); sy.place(relx=1,rely=.04,relheight=.90,anchor="ne")

    def _build_sessions(self):
        card=ttk.Frame(self.sessions_tab,style="Card.TFrame",padding=9); card.pack(fill="both",expand=True); ttk.Label(card,text="セッション一覧",style="CardTitle.TLabel").pack(anchor="w",pady=(0,6))
        cols=("date","project","model","total","input","cached","output","reasoning"); self.session_tree=ttk.Treeview(card,columns=cols,show="headings")
        heads={"date":"日時","project":"プロジェクト","model":"モデル","total":"Total","input":"Input","cached":"Cached Input","output":"Output","reasoning":"Reasoning"}; widths={"date":120,"project":130,"model":115,"total":90,"input":90,"cached":100,"output":75,"reasoning":75}
        for c in cols: self.session_tree.heading(c,text=heads[c]); self.session_tree.column(c,width=widths[c],anchor="w" if c in ("date","project","model") else "e")
        sy=ttk.Scrollbar(card,orient="vertical",command=self.session_tree.yview); sx=ttk.Scrollbar(card,orient="horizontal",command=self.session_tree.xview); self.session_tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set); self.session_tree.pack(fill="both",expand=True); sx.pack(fill="x"); sy.place(relx=1,rely=.04,relheight=.90,anchor="ne")

    def _build_settings(self):
        outer=ttk.Frame(self.settings_tab,style="Root.TFrame")
        outer.pack(fill="both",expand=True)
        self.var_tray=tk.BooleanVar(value=self.settings["tray_enabled"])
        self.var_tray_mode=tk.StringVar(value=self.settings["tray_mode"])
        self.var_reset=tk.BooleanVar(value=self.settings["notify_reset"])
        self.var_cache=tk.BooleanVar(value=self.settings["notify_cache"])
        self.var_auto=tk.BooleanVar(value=self.settings["auto_refresh"])
        self.var_interval=tk.IntVar(value=int(self.settings["refresh_minutes"]))
        self.var_startup=tk.BooleanVar(value=self.settings.get("startup_enabled", False))
        self._settings_save_job=None

        ttk.Label(outer,text="⚙  設定",style="SettingsTitle.TLabel").pack(anchor="w",pady=(0,4))

        # Scrollable settings body, so the compact 880x620 window still works.
        canvas=tk.Canvas(outer,bg="#F6F7FB",highlightthickness=0)
        scroll=ttk.Scrollbar(outer,orient="vertical",command=canvas.yview)
        body=tk.Frame(canvas,bg="#F6F7FB")
        win=canvas.create_window((0,0),window=body,anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left",fill="both",expand=True)
        scroll.pack(side="right",fill="y")
        body.bind("<Configure>",lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e: canvas.itemconfigure(win,width=e.width))
        canvas.bind_all("<MouseWheel>",lambda e: canvas.yview_scroll(int(-1*(e.delta/120)),"units") if self.notebook.index(self.notebook.select())==3 else None)

        def card(title):
            box=tk.Frame(body,bg="#FFFFFF",bd=0,highlightthickness=1,highlightbackground="#E5E7EB")
            box.pack(fill="x",padx=2,pady=(0,5),ipadx=10,ipady=5)
            tk.Label(box,text=title,bg="#FFFFFF",fg="#374151",font=("Segoe UI",10,"bold")).pack(anchor="w",padx=10,pady=(5,3))
            return box

        def setting_row(parent,title,desc,var):
            row=tk.Frame(parent,bg="#FFFFFF")
            row.pack(fill="x",padx=10,pady=3)
            text=tk.Frame(row,bg="#FFFFFF")
            text.pack(side="left",fill="x",expand=True)
            tk.Label(text,text=title,bg="#FFFFFF",fg="#111827",font=("Segoe UI",10,"bold")).pack(anchor="w")
            tk.Label(text,text=desc,bg="#FFFFFF",fg="#9CA3AF",font=("Segoe UI",8),justify="left",wraplength=630).pack(anchor="w",pady=(2,0))
            ToggleSwitch(row,var,command=self._settings_changed,bg="#FFFFFF").pack(side="right",padx=(10,2))
            return row

        display=card("▣  表示")
        setting_row(display,"タスクトレイに表示","ウィンドウを閉じても、通知領域からCodexの使用状況を確認できます。",self.var_tray)
        tk.Frame(display,bg="#E5E7EB",height=1).pack(fill="x",padx=10,pady=2)
        tk.Label(display,text="タスクトレイの表示内容",bg="#FFFFFF",fg="#111827",font=("Segoe UI",10,"bold")).pack(anchor="w",padx=10,pady=(2,1))
        tk.Label(display,text="トレイアイコンのツールチップに表示する内容を選びます。",bg="#FFFFFF",fg="#9CA3AF",font=("Segoe UI",8)).pack(anchor="w",padx=10)
        choices=tk.Frame(display,bg="#FFFFFF"); choices.pack(fill="x",padx=10,pady=(5,6))
        choices.columnconfigure(0,weight=1); choices.columnconfigure(1,weight=1)
        self.tray_five_btn=ttk.Button(choices,text="◔  5時間セッション",command=lambda:self._select_tray_mode("five_hour"))
        self.tray_five_btn.grid(row=0,column=0,sticky="ew",padx=(0,5))
        self.tray_max_btn=ttk.Button(choices,text="◔  最大使用率",command=lambda:self._select_tray_mode("max_rate"))
        self.tray_max_btn.grid(row=0,column=1,sticky="ew",padx=(5,0))
        self._refresh_tray_mode_buttons()

        notify=card("♧  通知")
        tk.Label(notify,text="通知はCodexログからの推定です。実際のリセット時刻・キャッシュ期限を直接取得しているわけではありません。",bg="#FFFFFF",fg="#9CA3AF",font=("Segoe UI",8),wraplength=760,justify="left").pack(anchor="w",padx=10,pady=(0,2))
        setting_row(notify,"セッションがリセットされたら通知","Rate Limit使用率が前回更新から大きく下がった場合に通知します。",self.var_reset)
        tk.Frame(notify,bg="#E5E7EB",height=1).pack(fill="x",padx=10,pady=2)
        setting_row(notify,"キャッシュ期限の目安を通知","実際のキャッシュ期限ではなく、最後のCodexログ更新から30分経過を目安に通知します。",self.var_cache)

        other=card("□  その他")
        row=tk.Frame(other,bg="#FFFFFF"); row.pack(fill="x",padx=10,pady=3)
        text=tk.Frame(row,bg="#FFFFFF"); text.pack(side="left",fill="x",expand=True)
        tk.Label(text,text="ログフォルダ",bg="#FFFFFF",fg="#111827",font=("Segoe UI",10,"bold")).pack(anchor="w")
        tk.Label(text,text=str(SESSIONS_DIR),bg="#FFFFFF",fg="#9CA3AF",font=("Segoe UI",8),wraplength=620,justify="left").pack(anchor="w",pady=(2,0))
        ttk.Button(row,text="開く",style="Small.TButton",command=self._open_log_folder).pack(side="right",padx=(10,2))
        tk.Frame(other,bg="#E5E7EB",height=1).pack(fill="x",padx=10,pady=2)
        setting_row(other,"自動更新","指定した間隔でCodexログを自動的に再集計します。",self.var_auto)
        interval=tk.Frame(other,bg="#FFFFFF"); interval.pack(fill="x",padx=10,pady=(0,5))
        tk.Label(interval,text="更新間隔",bg="#FFFFFF",fg="#6B7280",font=("Segoe UI",9)).pack(side="left")
        combo=ttk.Combobox(interval,textvariable=self.var_interval,values=(1,2,5,10,15,30),width=8,state="readonly")
        combo.pack(side="right")
        combo.bind("<<ComboboxSelected>>",lambda e:self._settings_changed())
        tk.Label(interval,text="分",bg="#FFFFFF",fg="#6B7280",font=("Segoe UI",9)).pack(side="right",padx=(0,5))
        tk.Frame(other,bg="#E5E7EB",height=1).pack(fill="x",padx=10,pady=2)
        setting_row(other,"Windows起動時に自動起動","Windowsへのサインイン時にCodex Token Checkerを自動で起動します。",self.var_startup)
        tk.Frame(other,bg="#E5E7EB",height=1).pack(fill="x",padx=10,pady=2)
        version_row=tk.Frame(other,bg="#FFFFFF"); version_row.pack(fill="x",padx=10,pady=(3,5))
        version_text=tk.Frame(version_row,bg="#FFFFFF"); version_text.pack(side="left",fill="x",expand=True)
        tk.Label(version_text,text="バージョン情報",bg="#FFFFFF",fg="#111827",font=("Segoe UI",10,"bold")).pack(anchor="w")
        tk.Label(version_text,text=f"Codex Token Checker v{APP_VERSION}  /  Codexログは読み取り専用",bg="#FFFFFF",fg="#9CA3AF",font=("Segoe UI",8)).pack(anchor="w",pady=(2,0))
        ttk.Button(version_row,text="アプリ情報",style="Small.TButton",command=self._show_about).pack(side="right",padx=(10,2))

        foot=tk.Frame(body,bg="#F6F7FB"); foot.pack(fill="x",padx=3,pady=(0,4))
        self.settings_status=tk.Label(foot,text="設定は変更すると自動保存されます",bg="#F6F7FB",fg="#9CA3AF",font=("Segoe UI",8))
        self.settings_status.pack(side="left")
        ttk.Button(foot,text="設定ファイルを開く",style="Small.TButton",command=self._open_settings_folder).pack(side="right")

    def _select_tray_mode(self, mode):
        self.var_tray_mode.set(mode)
        self._refresh_tray_mode_buttons()
        self._settings_changed()

    def _refresh_tray_mode_buttons(self):
        if not hasattr(self,"tray_five_btn"):
            return
        five=self.var_tray_mode.get()=="five_hour"
        self.tray_five_btn.configure(style="ChoiceSelected.TButton" if five else "Choice.TButton")
        self.tray_max_btn.configure(style="ChoiceSelected.TButton" if not five else "Choice.TButton")

    def _settings_changed(self):
        if getattr(self,"_settings_save_job",None) is not None:
            try:self.after_cancel(self._settings_save_job)
            except Exception:pass
        self._settings_save_job=self.after(180,self._save_settings_silent)

    def _save_settings_silent(self):
        self._settings_save_job=None
        self.settings=self._current_settings_from_ui()
        try:
            save_settings(self.settings)
            if hasattr(self,"settings_status"):
                self.settings_status.config(text=f"保存済み  {datetime.now().strftime('%H:%M:%S')}",fg="#16A34A")
        except Exception as e:
            if hasattr(self,"settings_status"):
                self.settings_status.config(text=f"保存エラー: {e}",fg="#DC2626")
            return
        self._configure_auto_refresh()
        self._configure_tray()
        self._configure_startup()

    def _open_settings_folder(self):
        try:
            SETTINGS_DIR.mkdir(parents=True,exist_ok=True)
            if not SETTINGS_FILE.exists():
                save_settings(self._current_settings_from_ui())
            if os.name=="nt": os.startfile(str(SETTINGS_DIR))
            else: subprocess.Popen(["xdg-open",str(SETTINGS_DIR)])
        except Exception as e:
            messagebox.showerror(APP_TITLE,f"設定フォルダを開けませんでした。\n{e}")

    def _open_log_folder(self):
        try:
            SESSIONS_DIR.mkdir(parents=True,exist_ok=True)
            if os.name=="nt": os.startfile(str(SESSIONS_DIR))
            else: subprocess.Popen(["xdg-open",str(SESSIONS_DIR)])
        except Exception as e: messagebox.showerror(APP_TITLE,f"フォルダを開けませんでした。\n{e}")

    def _current_settings_from_ui(self):
        return {"tray_enabled":bool(self.var_tray.get()),"tray_mode":self.var_tray_mode.get(),"notify_reset":bool(self.var_reset.get()),"notify_cache":bool(self.var_cache.get()),"auto_refresh":bool(self.var_auto.get()),"refresh_minutes":int(self.var_interval.get()),"startup_enabled":bool(self.var_startup.get())}

    def _save_settings_from_ui(self):
        self._save_settings_silent()

    def _apply_saved_runtime_settings(self):
        self._configure_auto_refresh(); self._configure_tray(); self._configure_startup()

    def _launch_command(self):
        """Return the command used for Windows login startup."""
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        script = str(Path(__file__).resolve())
        python_exe = Path(sys.executable)
        pythonw = python_exe.with_name("pythonw.exe")
        runner = pythonw if pythonw.exists() else python_exe
        return f'"{runner}" "{script}"'

    def _configure_startup(self):
        if os.name != "nt":
            return
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if self.settings.get("startup_enabled"):
                    winreg.SetValueEx(key, RUN_KEY_NAME, 0, winreg.REG_SZ, self._launch_command())
                else:
                    try:
                        winreg.DeleteValue(key, RUN_KEY_NAME)
                    except FileNotFoundError:
                        pass
        except Exception as e:
            if hasattr(self, "settings_status"):
                self.settings_status.config(text=f"自動起動設定エラー: {e}", fg="#DC2626")

    def _show_about(self):
        mode = "EXE版" if getattr(sys, "frozen", False) else "Python版"
        messagebox.showinfo(
            "Codex Token Checker",
            f"Codex Token Checker v{APP_VERSION}\n"
            f"{mode}\n\n"
            "CodexのローカルJSONLログを読み取り専用で解析します。\n"
            "ログファイルへの書き込み・変更は行いません。"
        )

    def _configure_auto_refresh(self):
        if self._auto_job is not None:
            try: self.after_cancel(self._auto_job)
            except Exception: pass
            self._auto_job=None
        if self.settings.get("auto_refresh"):
            mins=max(1,int(self.settings.get("refresh_minutes",5))); self._auto_job=self.after(mins*60*1000,self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        self._auto_job=None; self.update_data(); self._configure_auto_refresh()

    def _make_tray_image(self, percent):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return None
        size=64; img=Image.new("RGBA",(size,size),(0,0,0,0)); d=ImageDraw.Draw(img); box=(7,7,57,57); d.ellipse(box,outline=(210,215,225,255),width=9)
        pct=max(0,min(100,float(percent or 0))); end=-90+360*pct/100; d.arc(box,start=-90,end=end,fill=(79,70,229,255),width=9); d.ellipse((23,23,41,41),fill=(255,255,255,255)); return img

    def _tray_tooltip(self):
        now=datetime.now(); five=recent_usage_from_samples(self.sessions,now-timedelta(hours=5))["total_tokens"] if self.sessions else 0
        rates=[s.get("max_rate") for s in self.sessions if s.get("max_rate") is not None]; mr=max(rates) if rates else None
        if self.settings.get("tray_mode")=="max_rate": return f"Codex 最大使用率: {mr:.1f}%" if mr is not None else "Codex 最大使用率: --"
        return f"Codex 直近5時間: {compact_number(five)} tokens"

    def _configure_tray(self):
        enabled=bool(self.settings.get("tray_enabled"))
        if not enabled:
            self._stop_tray(); return
        try:
            import pystray
            from PIL import Image
        except ImportError:
            self.settings["tray_enabled"]=False; self.var_tray.set(False)
            messagebox.showwarning(APP_TITLE,"タスクトレイ機能には pystray と Pillow が必要です。\n\nPowerShell:\npip install pystray pillow")
            return
        self._stop_tray()
        rates=[s.get("max_rate") for s in self.sessions if s.get("max_rate") is not None]; pct=max(rates) if rates else 0
        img=self._make_tray_image(pct)
        menu=pystray.Menu(pystray.MenuItem("表示",lambda icon,item:self.after(0,self._restore_from_tray)),pystray.MenuItem("更新",lambda icon,item:self.after(0,self.update_data)),pystray.MenuItem("終了",lambda icon,item:self.after(0,self._quit_app)))
        self._tray_icon=pystray.Icon("CodexTokenChecker",img,self._tray_tooltip(),menu); threading.Thread(target=self._tray_icon.run,daemon=True).start()

    def _update_tray(self):
        if not self._tray_icon: return
        try:
            rates=[s.get("max_rate") for s in self.sessions if s.get("max_rate") is not None]; pct=max(rates) if rates else 0
            img=self._make_tray_image(pct)
            if img is not None: self._tray_icon.icon=img
            self._tray_icon.title=self._tray_tooltip()
        except Exception: pass

    def _notify(self,title,msg):
        if self._tray_icon:
            try: self._tray_icon.notify(msg,title); return
            except Exception: pass
        # Trayなしでは邪魔にならないようタイトルバーを点滅させる代わりに軽いダイアログ
        try: messagebox.showinfo(title,msg)
        except Exception: pass

    def _stop_tray(self):
        if self._tray_icon:
            try: self._tray_icon.stop()
            except Exception: pass
            self._tray_icon=None

    def _restore_from_tray(self): self.deiconify(); self.lift(); self.focus_force()
    def _quit_app(self): self._stop_tray(); self.destroy()
    def _on_close(self):
        if self.settings.get("tray_enabled") and self._tray_icon: self.withdraw()
        else: self._quit_app()

    def update_data(self):
        try: sessions=load_sessions()
        except Exception as e: messagebox.showerror(APP_TITLE,f"ログの読み込み中にエラーが発生しました。\n\n{e}"); return
        self.sessions=sessions; now=datetime.now(); self.updated_header.config(text=f"最終更新: {now.strftime('%m/%d %H:%M')}")
        if not sessions:
            z=empty_usage(); self.rate_label.config(text="-- %"); self.plan_label.config(text="プラン: --"); self.hour_card.set_value(0); self.five_hour_card.set_value(0)
            for c in (self.today_card,self.week_card,self.month_card,self.all_card): c.update_values(z,0)
            self.chart.set_values([]); self.latest_name.config(text="Codexセッションが見つかりません"); self.latest_meta.config(text=str(SESSIONS_DIR)); self._fill_tables([]); self._update_tray(); return
        today=now.date(); seven=today-timedelta(days=6); thirty=today-timedelta(days=29); buckets={k:[empty_usage(),0] for k in ("today","week","month","all")}; day_map={today-timedelta(days=i):0 for i in range(6,-1,-1)}
        for s in sessions:
            u,d=s["usage"],s["date"]; add_usage(buckets["all"][0],u); buckets["all"][1]+=1
            if d==today: add_usage(buckets["today"][0],u); buckets["today"][1]+=1
            if seven<=d<=today:
                add_usage(buckets["week"][0],u); buckets["week"][1]+=1
                if d in day_map: day_map[d]+=u["total_tokens"]
            if thirty<=d<=today: add_usage(buckets["month"][0],u); buckets["month"][1]+=1
        latest=max(sessions,key=lambda s:s["mtime"]); rate=latest.get("rate"); self.rate_label.config(text=f"{float(rate):.1f} %" if rate is not None else "-- %"); self.plan_label.config(text=f"プラン: {latest.get('plan') or '--'}")
        self.today_card.update_values(*buckets["today"]); self.week_card.update_values(*buckets["week"]); self.month_card.update_values(*buckets["month"]); self.all_card.update_values(*buckets["all"])
        self.hour_card.set_value(recent_usage_from_samples(sessions,now-timedelta(hours=1))["total_tokens"]); self.five_hour_card.set_value(recent_usage_from_samples(sessions,now-timedelta(hours=5))["total_tokens"])
        self.chart.set_values([(f"{d.month}/{d.day}",day_map[d]) for d in day_map]); dt=datetime.fromtimestamp(latest["mtime"]).strftime("%Y/%m/%d %H:%M") if latest["mtime"] else "--"; self.latest_name.config(text=latest["file"].name); self.latest_meta.config(text=f"{dt}\n{latest['project']}\n{number(latest['usage']['total_tokens'])} tokens")
        self._fill_tables(sessions); self._handle_notifications(latest,rate,now); self._update_tray()

    def _handle_notifications(self,latest,rate,now):
        if self.settings.get("notify_reset") and rate is not None and self._previous_rate is not None and self._previous_rate-rate>=5:
            self._notify("Codex Token Checker",f"Rate Limit使用率が {self._previous_rate:.1f}% → {rate:.1f}% に下がりました。リセットされた可能性があります。")
        if rate is not None: self._previous_rate=float(rate)
        if self.settings.get("notify_cache") and latest.get("mtime"):
            idle=(now-datetime.fromtimestamp(latest["mtime"])).total_seconds()
            if idle>=30*60 and not self._cache_notified:
                self._cache_notified=True; self._notify("Codex Token Checker","最後のCodexログ更新から30分経過しました。キャッシュ期限の目安として確認してください（実際の期限ではありません）。")
            elif idle<30*60: self._cache_notified=False

    def _fill_tables(self,sessions):
        for t in (self.project_tree,self.session_tree):
            for item in t.get_children(): t.delete(item)
        groups={}
        for s in sessions:
            k=s.get("project") or "(プロジェクト不明)"
            if k not in groups: groups[k]=[empty_usage(),0]
            add_usage(groups[k][0],s["usage"]); groups[k][1]+=1
        for project,(u,count) in sorted(groups.items(),key=lambda kv:kv[1][0]["total_tokens"],reverse=True):
            self.project_tree.insert("","end",values=(project,number(u["total_tokens"]),number(u["input_tokens"]),number(u["cached_input_tokens"]),number(u["output_tokens"]),number(u["reasoning_output_tokens"]),count))
        for s in sorted(sessions,key=lambda x:x["mtime"],reverse=True):
            dt=datetime.fromtimestamp(s["mtime"]).strftime("%Y/%m/%d %H:%M") if s["mtime"] else str(s["date"]); u=s["usage"]
            self.session_tree.insert("","end",values=(dt,s["project"],s["model"],number(u["total_tokens"]),number(u["input_tokens"]),number(u["cached_input_tokens"]),number(u["output_tokens"]),number(u["reasoning_output_tokens"])))


if __name__ == "__main__":
    App().mainloop()
