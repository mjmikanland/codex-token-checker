import csv
import json
import os
import subprocess
import sys
import tkinter as tk
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Manus Usage Checker"
APP_VERSION = "1.0.0"

DATE_KEYS = ("timestamp", "created_at", "createdAt", "date", "datetime", "started_at", "time", "日時", "日付")
TASK_KEYS = ("task", "task_name", "taskName", "name", "title", "prompt", "タスク", "タスク名", "タイトル")
STATUS_KEYS = ("status", "state", "result", "outcome", "ステータス", "状態", "結果")
VALUE_KEYS = ("credits", "credit", "usage", "amount", "cost", "tokens", "credits_used", "credit_usage", "使用量", "クレジット", "利用量", "トークン")
DURATION_KEYS = ("duration_seconds", "duration", "elapsed_seconds", "runtime_seconds", "実行時間", "所要時間")


def _first(record, keys, default=""):
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return default


def parse_datetime(value):
    if isinstance(value, (int, float)):
        try:
            if value > 10_000_000_000:
                value /= 1000
            return datetime.fromtimestamp(value)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
        return result.astimezone().replace(tzinfo=None) if result.tzinfo else result
    except ValueError:
        for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                pass
    return None


def parse_number(value):
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    text = value.strip().replace(",", "").replace("、", "")
    for suffix, multiplier in (("k", 1_000), ("K", 1_000), ("m", 1_000_000), ("M", 1_000_000)):
        if text.endswith(suffix):
            try:
                return float(text[:-1]) * multiplier
            except ValueError:
                return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def normalize_record(record, index=0):
    if not isinstance(record, dict):
        return None
    when = parse_datetime(_first(record, DATE_KEYS))
    if when is None:
        return None
    task = str(_first(record, TASK_KEYS, "(タスク名なし)")).strip() or "(タスク名なし)"
    status = str(_first(record, STATUS_KEYS, "unknown")).strip() or "unknown"
    value = parse_number(_first(record, VALUE_KEYS, 0))
    duration = parse_number(_first(record, DURATION_KEYS, 0))
    return {
        "id": str(record.get("id", index + 1)),
        "datetime": when,
        "task": task,
        "status": status,
        "usage": value,
        "duration": duration,
        "raw": record,
    }


def _json_records(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("records", "data", "items", "history", "usage", "利用履歴"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []


def load_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [normalize_record(row, i) for i, row in enumerate(csv.DictReader(handle))]


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return [normalize_record(row, i) for i, row in enumerate(_json_records(data))]


def load_history(path):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        rows = load_csv(path)
    elif path.suffix.lower() == ".json":
        rows = load_json(path)
    else:
        raise ValueError("CSVまたはJSONファイルを指定してください")
    return [row for row in rows if row is not None]


def aggregate(records, since=None, until=None):
    selected = [r for r in records if (since is None or r["datetime"] >= since) and (until is None or r["datetime"] <= until)]
    return {
        "count": len(selected),
        "usage": sum(r["usage"] for r in selected),
        "duration": sum(r["duration"] for r in selected),
        "success": sum(str(r["status"]).lower() in ("success", "succeeded", "completed", "complete", "成功", "完了") for r in selected),
        "records": selected,
    }


def group_by_task(records):
    groups = defaultdict(lambda: {"count": 0, "usage": 0.0, "duration": 0.0})
    for record in records:
        group = groups[record["task"]]
        group["count"] += 1
        group["usage"] += record["usage"]
        group["duration"] += record["duration"]
    return dict(groups)


def fmt_number(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1000x680")
        self.minsize(820, 560)
        self.configure(bg="#F6F7FB")
        self.records = []
        self.source = ""
        self._build_style()
        self._build_ui()

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Root.TFrame", background="#F6F7FB")
        style.configure("Title.TLabel", background="#F6F7FB", foreground="#111827", font=("Segoe UI", 18, "bold"))
        style.configure("Muted.TLabel", background="#F6F7FB", foreground="#6B7280", font=("Segoe UI", 9))
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("CardTitle.TLabel", background="#FFFFFF", foreground="#374151", font=("Segoe UI", 10, "bold"))
        style.configure("Big.TLabel", background="#FFFFFF", foreground="#111827", font=("Segoe UI", 20, "bold"))
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 9))

    def _build_ui(self):
        root = ttk.Frame(self, style="Root.TFrame", padding=12)
        root.pack(fill="both", expand=True)
        header = ttk.Frame(root, style="Root.TFrame")
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="CSV / JSONの利用履歴をローカルで分析", style="Muted.TLabel").pack(side="left", padx=12, pady=6)
        ttk.Button(header, text="履歴を開く", command=self.open_file).pack(side="right")
        ttk.Button(header, text="CSV出力", command=self.export_csv).pack(side="right", padx=5)
        ttk.Button(header, text="JSON出力", command=self.export_json).pack(side="right", padx=5)

        self.source_label = ttk.Label(root, text="ファイル未選択", style="Muted.TLabel")
        self.source_label.pack(anchor="w", pady=(0, 8))
        cards = ttk.Frame(root, style="Root.TFrame")
        cards.pack(fill="x", pady=(0, 8))
        for i in range(4):
            cards.columnconfigure(i, weight=1)
        self.card_labels = []
        for i, title in enumerate(("利用件数", "合計使用量", "成功率", "実行時間")):
            card = ttk.Frame(cards, style="Card.TFrame", padding=10)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 4, 4 if i < 3 else 0))
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
            label = ttk.Label(card, text="0", style="Big.TLabel")
            label.pack(anchor="w", pady=(4, 0))
            self.card_labels.append(label)

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)
        summary = ttk.Frame(notebook, style="Root.TFrame", padding=4)
        tasks = ttk.Frame(notebook, style="Root.TFrame", padding=4)
        sessions = ttk.Frame(notebook, style="Root.TFrame", padding=4)
        notebook.add(summary, text="日別サマリ")
        notebook.add(tasks, text="タスク別")
        notebook.add(sessions, text="履歴一覧")

        self.chart = tk.Canvas(summary, height=240, bg="#FFFFFF", highlightthickness=0)
        self.chart.pack(fill="both", expand=True)
        self.task_tree = self._make_tree(tasks, (("task", "タスク", 430), ("count", "件数", 90), ("usage", "使用量", 120), ("duration", "実行時間(秒)", 130)))
        self.session_tree = self._make_tree(sessions, (("date", "日時", 150), ("task", "タスク", 350), ("status", "状態", 120), ("usage", "使用量", 110), ("duration", "実行時間(秒)", 130)))

    def _make_tree(self, parent, columns):
        tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings")
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="w" if key in ("task", "status") else "e")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def open_file(self):
        paths = filedialog.askopenfilenames(title="Manus利用履歴を選択", filetypes=(("CSV / JSON", "*.csv *.json"), ("CSV", "*.csv"), ("JSON", "*.json")))
        if not paths:
            return
        try:
            records = []
            for path in paths:
                records.extend(load_history(path))
            self.records = sorted(records, key=lambda r: r["datetime"], reverse=True)
            self.source = ", ".join(paths)
            self.source_label.config(text=f"読み込み済み: {self.source}  /  {len(self.records)}件")
            self.refresh()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror(APP_TITLE, f"履歴を読み込めませんでした。\n\n{exc}")

    def refresh(self):
        result = aggregate(self.records)
        self.card_labels[0].config(text=f"{result['count']:,}")
        self.card_labels[1].config(text=fmt_number(result["usage"]))
        rate = result["success"] / result["count"] * 100 if result["count"] else 0
        self.card_labels[2].config(text=f"{rate:.1f}%")
        self.card_labels[3].config(text=f"{result['duration']:,.0f} 秒")
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)
        for task, values in sorted(group_by_task(self.records).items(), key=lambda x: x[1]["usage"], reverse=True):
            self.task_tree.insert("", "end", values=(task, values["count"], fmt_number(values["usage"]), fmt_number(values["duration"])))
        for item in self.session_tree.get_children():
            self.session_tree.delete(item)
        for record in self.records:
            self.session_tree.insert("", "end", values=(record["datetime"].strftime("%Y/%m/%d %H:%M"), record["task"], record["status"], fmt_number(record["usage"]), fmt_number(record["duration"])))
        self.draw_chart()

    def draw_chart(self):
        self.chart.delete("all")
        if not self.records:
            self.chart.create_text(200, 100, text="CSVまたはJSONを開いてください", fill="#6B7280")
            return
        today = max(r["datetime"] for r in self.records).date()
        days = [today - timedelta(days=i) for i in range(13, -1, -1)]
        values = [sum(r["usage"] for r in self.records if r["datetime"].date() == day) for day in days]
        width = max(self.chart.winfo_width(), 400)
        height = max(self.chart.winfo_height(), 200)
        left, bottom, top = 45, 35, 20
        plot_h = height - bottom - top
        slot = (width - left - 15) / len(days)
        max_value = max(max(values), 1)
        self.chart.create_line(left, top + plot_h, width - 15, top + plot_h, fill="#D1D5DB")
        for i, (day, value) in enumerate(zip(days, values)):
            x = left + slot * i + slot / 2
            bar_h = plot_h * value / max_value
            self.chart.create_rectangle(x - min(18, slot * .3), top + plot_h - bar_h, x + min(18, slot * .3), top + plot_h, fill="#4F46E5", outline="")
            self.chart.create_text(x, top + plot_h + 15, text=f"{day.month}/{day.day}", fill="#6B7280", font=("Segoe UI", 8))
            if value:
                self.chart.create_text(x, max(top + 8, top + plot_h - bar_h - 8), text=fmt_number(value), fill="#374151", font=("Segoe UI", 8))

    def export_csv(self):
        if not self.records:
            messagebox.showinfo(APP_TITLE, "先に利用履歴を読み込んでください")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=(("CSV", "*.csv"),))
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("datetime", "task", "status", "usage", "duration_seconds"))
            for record in self.records:
                writer.writerow((record["datetime"].isoformat(), record["task"], record["status"], record["usage"], record["duration"]))

    def export_json(self):
        if not self.records:
            messagebox.showinfo(APP_TITLE, "先に利用履歴を読み込んでください")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("JSON", "*.json"),))
        if not path:
            return
        output = [{"datetime": r["datetime"].isoformat(), "task": r["task"], "status": r["status"], "usage": r["usage"], "duration_seconds": r["duration"]} for r in self.records]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    App().mainloop()
