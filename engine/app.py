"""
Bot de Opciones Binarias con IA — Plataforma Profesional de Trading.
"""

import csv
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from types import SimpleNamespace

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import ai_filter as ai_filter_module
import backtest
import bot
import moneda
from ai_filter import LABELS, PROVIDERS, get_key, save_key
from broker import SimBroker
from moneda import fmt
from strategies import STRATEGIES, custom_rule_strategy, get_strategy_description

BG, PANEL, CARD, LINE = "#0e1318", "#151c24", "#1c2530", "#2a3745"
FG, MUTED = "#e8eef4", "#8a97a6"
ACCENT, GOOD, BAD, WARN = "#3b82f6", "#22c55e", "#ef4444", "#f59e0b"
FONT = "Segoe UI"


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self.show_tip)
        widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + 25
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw, text=self.text, justify="left", bg="#2a3745", fg="#ffffff",
            relief="solid", borderwidth=1, font=(FONT, 8), padx=8, pady=4
        )
        label.pack()

    def hide_tip(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class ToastNotification:
    @staticmethod
    def show(root, message, tag="info"):
        color = GOOD if tag == "good" else (BAD if tag == "bad" else ACCENT)
        toast = tk.Toplevel(root)
        toast.wm_overrideredirect(True)
        toast.configure(bg=color)

        rx = root.winfo_rootx() + root.winfo_width() - 290
        ry = root.winfo_rooty() + root.winfo_height() - 75
        toast.wm_geometry(f"270x50+{rx}+{ry}")

        lbl = tk.Label(toast, text=message, bg=color, fg="#ffffff", font=(FONT, 9, "bold"), padx=12, pady=10)
        lbl.pack(fill="both", expand=True)
        root.after(3500, toast.destroy)


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.running = False
        self.all_trades = []
        bot.ON_EVENT = lambda kind, **d: self.q.put((kind, d))

        root.title("Bot de Opciones Binarias con IA — Plataforma Completa de Trading")
        h = min(920, max(720, root.winfo_screenheight() - 50))
        root.geometry(f"1360x{h}")
        root.minsize(1120, 700)
        root.configure(bg=BG)

        self.style()
        self.vars()
        self.build()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)
        root.after(300, self.restore_previous_session)
        root.after(500, lambda: self.fetch_rate(force=False))

    def style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 8), font=(FONT, 10), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", FG)])
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, rowheight=26, borderwidth=0, font=(FONT, 9))
        s.configure("Treeview.Heading", background=PANEL, foreground=MUTED, relief="flat", font=(FONT, 9, "bold"), padding=6)
        s.map("Treeview", background=[("selected", "#27415f")])
        s.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=FG, arrowcolor=FG, bordercolor=LINE, padding=5)

    def vars(self):
        email, pw = bot.load_login()
        self.v_mode = tk.StringVar(value="IQ Option (demo)")
        self.v_email = tk.StringVar(value=email or "")
        self.v_pass = tk.StringVar(value=pw or "")
        self.v_assets = tk.StringVar(value=",".join(bot.load_tickers()))
        self.v_amount = tk.StringVar(value="5000")
        self.v_rate = tk.StringVar(value="")
        self.v_expiry = tk.StringVar(value="1")
        self.v_history = tk.StringVar(value="15000")
        self.v_strat = tk.StringVar(value="auto")
        self.v_ai = tk.StringVar(value=LABELS["none"])
        self.v_key = tk.StringVar(value="")
        self.v_remember = tk.BooleanVar(value=True)

        self.v_loss_day = tk.StringVar(value="50000")
        self.v_loss_week = tk.StringVar(value="150000")
        self.v_max_streak = tk.StringVar(value="4")
        self.v_max_trades = tk.StringVar(value="30")
        self.v_start_hour = tk.StringVar(value="0")
        self.v_end_hour = tk.StringVar(value="24")
        self.v_risk_pct = tk.StringVar(value="50")

        self.ed_ind = tk.StringVar(value="RSI")
        self.ed_cond = tk.StringVar(value="<")
        self.ed_val = tk.StringVar(value="30")
        self.ed_name = tk.StringVar(value="custom_rsi30")

        self.f_asset = tk.StringVar(value="Todos")
        self.f_strat = tk.StringVar(value="Todas")
        self.f_search = tk.StringVar(value="")

    def label(self, parent, text, size=9, color=MUTED, bold=False, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", parent["bg"]), fg=color, font=(FONT, size, "bold" if bold else "normal"), **kw)

    def entry(self, parent, var, show=None, width=None, tooltip=""):
        e = tk.Entry(parent, textvariable=var, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                     font=(FONT, 10), highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT, show=show or "", width=width or 10)
        if tooltip:
            ToolTip(e, tooltip)
        return e

    def button(self, parent, text, cmd, color, fg="#ffffff"):
        return tk.Button(parent, text=text, command=cmd, bg=color, fg=fg, activebackground=color,
                         activeforeground=fg, relief="flat", bd=0, cursor="hand2", font=(FONT, 9, "bold"), pady=6)

    def build(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=20, pady=(12, 6))
        self.label(head, "Bot de Opciones Binarias", 16, FG, True).pack(side="left")
        self.label(head, "  con IA & Analítica Completa", 16, ACCENT, True).pack(side="left")

        self.bot_badge = tk.Label(head, text="● DETENIDO", bg="#3a1c1c", fg=BAD, font=(FONT, 9, "bold"), padx=10, pady=3)
        self.bot_badge.pack(side="right", padx=6)

        self.conn_badge = tk.Label(head, text="● DESCONECTADO", bg="#3a1c1c", fg=BAD, font=(FONT, 9, "bold"), padx=10, pady=3)
        self.conn_badge.pack(side="right", padx=6)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        left = tk.Frame(body, bg=PANEL, width=330)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self.build_left_accordion(left)
        self.build_right(right)

    def build_left_accordion(self, p):
        bottom = tk.Frame(p, bg=PANEL)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(8, 12))

        self.btn_analyze = self.button(bottom, "Analizar Estrategias", self.do_analyze, ACCENT)
        self.btn_analyze.pack(fill="x", pady=(0, 4))

        self.btn_start = self.button(bottom, "▶ Iniciar Bot", self.do_start, "#16a34a")
        self.btn_start.pack(fill="x", pady=(0, 4))

        self.btn_stop_split = tk.Menubutton(bottom, text="■ Detener Sesión ▼", bg="#b91c1c", fg="#fff", font=(FONT, 9, "bold"), relief="flat", pady=6)
        stop_menu = tk.Menu(self.btn_stop_split, tearoff=0, bg=CARD, fg=FG)
        stop_menu.add_command(label="Detener ahora mismo", command=self.do_stop_now)
        stop_menu.add_command(label="Detener al terminar ops abiertas", command=self.do_stop_soft)
        self.btn_stop_split.configure(menu=stop_menu, state="disabled")
        self.btn_stop_split.pack(fill="x")

        # Contenedor con scroll: al agregar campos el panel ya no se corta en pantallas pequeñas
        holder = tk.Frame(p, bg=PANEL)
        holder.pack(fill="both", expand=True, padx=8, pady=4)
        canvas_left = tk.Canvas(holder, bg=PANEL, highlightthickness=0)
        sb_left = ttk.Scrollbar(holder, orient="vertical", command=canvas_left.yview)
        canvas_left.configure(yscrollcommand=sb_left.set)
        sb_left.pack(side="right", fill="y")
        canvas_left.pack(side="left", fill="both", expand=True)
        acc_frame = tk.Frame(canvas_left, bg=PANEL)
        win_left = canvas_left.create_window((0, 0), window=acc_frame, anchor="nw")
        acc_frame.bind("<Configure>", lambda e: canvas_left.configure(scrollregion=canvas_left.bbox("all")))
        canvas_left.bind("<Configure>", lambda e: canvas_left.itemconfigure(win_left, width=e.width))
        holder.bind("<Enter>", lambda e: canvas_left.bind_all(
            "<MouseWheel>", lambda ev: canvas_left.yview_scroll(int(-ev.delta / 120), "units")))
        holder.bind("<Leave>", lambda e: canvas_left.unbind_all("<MouseWheel>"))

        sec1 = tk.LabelFrame(acc_frame, text=" Operación & Bróker ", bg=PANEL, fg=FG, font=(FONT, 9, "bold"), bd=1, relief="solid")
        sec1.pack(fill="x", pady=4, ipady=4)

        self.label(sec1, "Modo de Conexión:", 8).pack(anchor="w", padx=8, pady=(4, 1))
        self.mode_cb = ttk.Combobox(sec1, textvariable=self.v_mode, state="readonly", values=["IQ Option (demo)", "Simulación (sin conexión)"])
        self.mode_cb.pack(fill="x", padx=8, pady=2)
        self.mode_cb.bind("<<ComboboxSelected>>", lambda e: self.toggle_login())

        self.login_frame = tk.Frame(sec1, bg=PANEL)
        lf = self.login_frame
        self.label(lf, "Correo de IQ Option:", 8).pack(anchor="w", pady=(3, 1))
        self.entry(lf, self.v_email, tooltip="Tu correo de IQ Option").pack(fill="x", ipady=2)
        self.label(lf, "Contraseña:", 8).pack(anchor="w", pady=(3, 1))
        self.entry(lf, self.v_pass, show="•", tooltip="Tu contraseña").pack(fill="x", ipady=2)
        self.login_frame.pack(fill="x", padx=8, pady=2)

        self.label(sec1, "Activos (separados por coma):", 8).pack(anchor="w", padx=8, pady=(4, 1))
        self.entry(sec1, self.v_assets, tooltip="Ejemplo: EURUSD, GBPUSD").pack(fill="x", padx=8, pady=2)

        row1 = tk.Frame(sec1, bg=PANEL)
        row1.pack(fill="x", padx=8, pady=2)
        c1 = tk.Frame(row1, bg=PANEL)
        c1.pack(side="left", fill="x", expand=True)
        self.label(c1, "Importe (COP):", 8).pack(anchor="w")
        self.entry(c1, self.v_amount, width=8, tooltip="Monto en COP por operación").pack(fill="x")

        c2 = tk.Frame(row1, bg=PANEL)
        c2.pack(side="right", padx=(4, 0))
        self.label(c2, "Minutos:", 8).pack(anchor="w")
        self.entry(c2, self.v_expiry, width=5, tooltip="Duración de la vela (1-5)").pack(fill="x")

        sec2 = tk.LabelFrame(acc_frame, text=" Filtro de IA ", bg=PANEL, fg=FG, font=(FONT, 9, "bold"), bd=1, relief="solid")
        sec2.pack(fill="x", pady=4, ipady=4)
        self.label(sec2, "Proveedor de IA:", 8).pack(anchor="w", padx=8, pady=(4, 1))
        self.ai_cb = ttk.Combobox(sec2, textvariable=self.v_ai, state="readonly", values=list(LABELS.values()))
        self.ai_cb.pack(fill="x", padx=8, pady=2)
        self.ai_cb.bind("<<ComboboxSelected>>", lambda e: self.load_ai_key())

        self.label(sec2, "Clave API (Groq o Gemini):", 8).pack(anchor="w", padx=8, pady=(4, 1))
        self.entry(sec2, self.v_key, show="•",
                   tooltip="Clave gratuita de console.groq.com/keys o aistudio.google.com/apikey").pack(fill="x", padx=8, pady=2, ipady=2)
        row_ai = tk.Frame(sec2, bg=PANEL)
        row_ai.pack(fill="x", padx=8, pady=(2, 0))
        tk.Checkbutton(row_ai, text="Recordar clave", variable=self.v_remember, bg=PANEL, fg=MUTED,
                       selectcolor=CARD, activebackground=PANEL, activeforeground=FG,
                       font=(FONT, 8), bd=0, highlightthickness=0).pack(side="left")
        self.btn_ai_test = self.button(row_ai, "Probar conexión", self.do_ai_test, ACCENT)
        self.btn_ai_test.configure(pady=2, font=(FONT, 8, "bold"))
        self.btn_ai_test.pack(side="right")

        sec3 = tk.LabelFrame(acc_frame, text=" Límites & Riesgo Avanzado ", bg=PANEL, fg=FG, font=(FONT, 9, "bold"), bd=1, relief="solid")
        sec3.pack(fill="x", pady=4, ipady=4)

        self.label(sec3, "Pérdida Máxima por Sesión (COP):", 8).pack(anchor="w", padx=8, pady=(4, 1))
        self.entry(sec3, self.v_loss_day, tooltip="Detiene el bot si la sesión actual pierde este monto (se reinicia al pulsar Iniciar Bot)").pack(fill="x", padx=8, pady=2)

        r_hr = tk.Frame(sec3, bg=PANEL)
        r_hr.pack(fill="x", padx=8, pady=4)
        self.label(r_hr, "Horario Operación (0-24h):", 8).pack(anchor="w")
        self.entry(r_hr, self.v_start_hour, width=4, tooltip="Hora inicio").pack(side="left")
        self.label(r_hr, "a", 8).pack(side="left", padx=4)
        self.entry(r_hr, self.v_end_hour, width=4, tooltip="Hora fin").pack(side="left")

        r_lim = tk.Frame(sec3, bg=PANEL)
        r_lim.pack(fill="x", padx=8, pady=4)
        for i, (txt, var, tip) in enumerate([
            ("Racha máx. pérdidas", self.v_max_streak, "Se detiene tras esta cantidad de pérdidas seguidas"),
            ("Ops máximas", self.v_max_trades, "Máximo de operaciones por sesión"),
            ("Reducir riesgo %", self.v_risk_pct, "Porcentaje que baja el importe tras 2 pérdidas seguidas"),
        ]):
            col = tk.Frame(r_lim, bg=PANEL)
            col.grid(row=0, column=i, padx=(0, 6), sticky="w")
            self.label(col, txt, 7).pack(anchor="w")
            self.entry(col, var, width=6, tooltip=tip).pack(fill="x", ipady=1)

    def toggle_login(self):
        if hasattr(self, 'login_frame'):
            if self.v_mode.get().startswith("Sim"):
                self.login_frame.pack_forget()
            else:
                self.login_frame.pack(fill="x", padx=8, pady=2, after=self.mode_cb)

    def build_right(self, p):
        cards = tk.Frame(p, bg=BG)
        cards.pack(fill="x")
        self.stat = {}
        for i, (key, title) in enumerate([
            ("balance", "SALDO DEMO"), ("pnl", "RESULTADO (COP)"),
            ("trades", "OPERACIONES"), ("rate", "WIN RATE"),
            ("ev", "EXPECTATIVA / OP"), ("drawdown", "MAX DRAWDOWN"),
            ("best_streak", "MEJOR RACHA"), ("worst_streak", "PEOR RACHA")
        ]):
            r, c = i // 4, i % 4
            card = tk.Frame(cards, bg=PANEL, padx=10, pady=8)
            card.grid(row=r, column=c, sticky="ew", padx=2, pady=2)
            cards.columnconfigure(c, weight=1)
            self.label(card, title, 7, MUTED, True).pack(anchor="w")
            v = self.label(card, "Esperando conexión...", 11, FG, True)
            v.pack(anchor="w", pady=(1, 0))
            self.stat[key] = v

        realtime_bar = tk.Frame(p, bg=CARD, padx=10, pady=5)
        realtime_bar.pack(fill="x", pady=(6, 0))
        self.lbl_streak_live = self.label(realtime_bar, "Racha Actual: 0", 8, FG, True)
        self.lbl_streak_live.pack(side="left", padx=10)
        self.lbl_trades_rem = self.label(realtime_bar, "Ops Restantes: 30", 8, ACCENT, True)
        self.lbl_trades_rem.pack(side="left", padx=10)

        nb = ttk.Notebook(p)
        nb.pack(fill="both", expand=True, pady=(6, 0))

        t1 = tk.Frame(nb, bg=CARD)
        nb.add(t1, text="Operaciones")

        filter_bar = tk.Frame(t1, bg=CARD, padx=8, pady=6)
        filter_bar.pack(fill="x")
        self.label(filter_bar, "Buscar:", 8).pack(side="left", padx=(0, 4))
        self.entry(filter_bar, self.f_search, width=15, tooltip="Buscar por activo o estrategia...").pack(side="left", padx=4)
        self.button(filter_bar, "Filtrar", self.apply_filters, ACCENT).pack(side="left", padx=2)
        self.button(filter_bar, "Exportar a CSV", self.export_to_csv, "#16a34a").pack(side="right", padx=4)

        cols = ("time", "asset", "dir", "strategy", "result", "profit")
        self.trades_tree = ttk.Treeview(t1, columns=cols, show="headings")
        for c, h in zip(cols, ("Hora", "Activo", "Dirección", "Estrategia", "Resultado", "Ganancia (COP)")):
            self.trades_tree.heading(c, text=h, command=lambda _c=c: self.sort_treeview(_c))
            self.trades_tree.column(c, width=100, anchor="w")
        self.trades_tree.tag_configure("win", foreground=GOOD)
        self.trades_tree.tag_configure("loss", foreground=BAD)
        self.trades_tree.pack(fill="both", expand=True, padx=2, pady=2)
        self.trades_tree.bind("<Double-1>", self.open_trade_modal)

        t2 = tk.Frame(nb, bg=CARD)
        nb.add(t2, text="Rendimiento")
        self.fig_eq = Figure(figsize=(5, 3), dpi=95, facecolor=CARD)
        self.ax_eq = self.fig_eq.add_subplot(111)
        self.ax_eq.set_facecolor(CARD)
        self.canvas_equity = FigureCanvasTkAgg(self.fig_eq, master=t2)
        self.canvas_equity.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=8)

        t3 = tk.Frame(nb, bg=CARD)
        nb.add(t3, text="Estrategias & Backtest")

        top_t3 = tk.Frame(t3, bg=PANEL, padx=8, pady=6)
        top_t3.pack(fill="x", padx=6, pady=6)
        self.label(top_t3, "EDITOR VISUAL DE REGLAS Y ESTRATEGIAS", 8, ACCENT, True).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 4))

        self.entry(top_t3, self.ed_name, width=12, tooltip="Nombre de la nueva estrategia").grid(row=1, column=0, padx=2)
        ttk.Combobox(top_t3, textvariable=self.ed_ind, values=["RSI", "EMA_Fast", "Precio"], width=10, state="readonly").grid(row=1, column=1, padx=2)
        ttk.Combobox(top_t3, textvariable=self.ed_cond, values=["<", ">", "cruce"], width=8, state="readonly").grid(row=1, column=2, padx=2)
        self.entry(top_t3, self.ed_val, width=8, tooltip="Valor límite").grid(row=1, column=3, padx=2)
        self.button(top_t3, "Guardar Regla", self.save_custom_rule, ACCENT).grid(row=1, column=4, padx=6)

        self.lbl_desc = self.label(top_t3, "Lógica: Selecciona una estrategia para ver su explicación.", 8, FG)
        self.lbl_desc.grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))

        bt_bar = tk.Frame(t3, bg=CARD, padx=6)
        bt_bar.pack(fill="x")
        self.button(bt_bar, "📊 Probar Estrategia Seleccionada con Histórico", self.run_single_backtest, "#16a34a").pack(side="left", pady=4)

        bt_res = tk.Frame(t3, bg=CARD)
        bt_res.pack(fill="both", expand=True, padx=6, pady=2)
        self.lbl_bt_metrics = self.label(bt_res, "Reporte Backtest: Sin ejecutar", 8, MUTED, justify="left", wraplength=760)
        self.lbl_bt_metrics.pack(anchor="w")

        self.fig_bt = Figure(figsize=(5, 2), dpi=85, facecolor=CARD)
        self.ax_bt = self.fig_bt.add_subplot(111)
        self.ax_bt.set_facecolor(CARD)
        self.canvas_bt = FigureCanvasTkAgg(self.fig_bt, master=bt_res)
        self.canvas_bt.get_tk_widget().pack(fill="both", expand=True, pady=2)

        t4 = tk.Frame(nb, bg=CARD)
        nb.add(t4, text="Transparencia IA")
        self.txt_ai = tk.Text(t4, bg=CARD, fg=FG, font=(FONT, 9), padx=12, pady=12, relief="flat", wrap="word")
        self.txt_ai.configure(state="disabled")
        self.txt_ai.pack(fill="both", expand=True)

        t5 = tk.Frame(nb, bg=CARD)
        nb.add(t5, text="Registro")
        self.log = tk.Text(t5, bg=CARD, fg=FG, relief="flat", wrap="word", font=("Consolas", 9), padx=10, pady=10, state="disabled")
        self.log.pack(fill="both", expand=True)

        self.nb = nb

    def validate_cross_risks(self):
        try:
            amt = float(moneda.parse_num(self.v_amount.get()))
            loss_day = float(moneda.parse_num(self.v_loss_day.get()))
            if amt >= loss_day:
                messagebox.showwarning("Advertencia de Riesgo", f"El importe por operación (${amt:,.0f}) es mayor o igual a la pérdida máxima permitida (${loss_day:,.0f}).")
                return False
        except Exception:
            pass
        return True

    def save_custom_rule(self):
        name = self.ed_name.get().strip()
        ind = self.ed_ind.get()
        cond = self.ed_cond.get()
        try:
            val = float(self.ed_val.get())
        except ValueError:
            messagebox.showerror("Error", "Ingresa un número válido como valor.")
            return

        if not name:
            messagebox.showerror("Error", "Ingresa un nombre para la regla.")
            return

        STRATEGIES[name] = custom_rule_strategy(ind, cond, val)
        self.lbl_desc.configure(text=f"Lógica ({name}): Regla dinámicamente registrada [{ind} {cond} {val}]")
        ToastNotification.show(self.root, f"Estrategia '{name}' registrada", "good")

    def load_history(self, asset, count):
        """Devuelve (df, payout, sintetico). Si no hay mercado real (modo simulación o activo cerrado)
        usa datos sintéticos y lo marca, porque en ellos ninguna estrategia puede tener ventaja real."""
        sim_mode = self.v_mode.get().startswith("Sim")
        b = bot.make_broker(sim_mode, on_status=lambda state, msg: self.q.put(("conn_status", {"state": state, "msg": msg})),
                            email=self.v_email.get().strip() or None,
                            password=self.v_pass.get() or None)
        if not sim_mode:
            try:
                return b.candles_history(asset, 60, count), b.get_payout(asset), False
            except Exception as err:
                self.q.put(("log", {"text": f"⚠ Sin datos reales para {asset} ({err}). Se usan datos SINTÉTICOS: el resultado no vale como evidencia."}))
        return SimBroker().candles_history(asset, 60, count), 0.85, True

    def run_single_backtest(self):
        strat = self.v_strat.get()
        if strat not in STRATEGIES:
            messagebox.showinfo("Backtest", "Primero pulsa «Analizar Estrategias» o guarda una regla personalizada "
                                            "para tener una estrategia que probar.")
            return
        asset = ([x.strip().upper() for x in self.v_assets.get().split(",") if x.strip()] or ["EURUSD"])[0]
        try:
            count, expiry = int(self.v_history.get()), int(self.v_expiry.get())
        except ValueError:
            messagebox.showerror("Error", "Minutos e histórico deben ser números enteros.")
            return
        self.lbl_bt_metrics.configure(text=f"⏳ Probando «{strat}» con el histórico...", fg=ACCENT)

        def work():
            try:
                df, pay, synthetic = self.load_history(asset, count)
                n = len(df)
                cut = int(n * 0.75)          # solo el 25 % final, que ninguna selección vio
                sig = STRATEGIES[strat](df)
                res = backtest.evaluate(df.iloc[cut:], sig.iloc[cut:], expiry_candles=expiry, payout=pay)
                self.q.put(("bt_res", {"strat": strat, "res": res, "asset": asset, "payout": pay,
                                       "synthetic": synthetic, "tested": None}))
            except Exception as e:
                self.q.put(("log", {"text": f"✖ Error en el backtest: {e}"}))

        threading.Thread(target=work, daemon=True).start()

    def update_bt_report(self, d):
        s = d["res"]
        asset = d.get("asset", "EURUSD")
        pay = d.get("payout", 0.85)
        synthetic = d.get("synthetic", False)
        text_v, level = backtest.verdict(s, pay)
        if synthetic:
            text_v = "DATOS SINTÉTICOS (ruido aleatorio): este resultado no dice nada sobre el mercado real."
            level = "bad"
        color = {"good": GOOD, "warn": WARN, "bad": BAD}[level]

        tested = d.get("tested")
        head = (f"Estrategia ({asset}): {d['strat']}"
                + (f" — mejor de {tested} probadas" if tested else "")
                + " · evaluada en el tramo de PRUEBA (25 % final, datos no usados para elegirla)")
        stats = (f"Operaciones: {s['n']} | Win Rate: {s['winrate']:.1%} (equilibrio {backtest.breakeven(pay):.1%}) | "
                 f"Profit Factor: {s['profit_factor']:.2f} | Racha Máx. Negativa: {s['max_loss_streak']}")
        self.lbl_bt_metrics.configure(text=f"{head}\n{stats}\n{text_v}", fg=color)

        if d["strat"] in STRATEGIES:
            self.v_strat.set(d["strat"])
            self.lbl_desc.configure(text="Lógica: " + get_strategy_description(d["strat"]))

        self.ax_bt.clear()
        self.ax_bt.set_facecolor(CARD)
        self.ax_bt.tick_params(colors=FG, labelsize=7)
        for spine in self.ax_bt.spines.values():
            spine.set_color(LINE)

        if s.get("equity_curve"):
            self.ax_bt.plot(s["equity_curve"], color=color, linewidth=1.5)
            self.ax_bt.axhline(0, color=LINE, linewidth=0.8)
            self.ax_bt.set_title(f"Resultado acumulado en datos de prueba — {d['strat']} (en unidades de apuesta)",
                                 color=FG, fontsize=8)
        else:
            self.ax_bt.text(0.5, 0.5, "Sin operaciones en el tramo de prueba", color=MUTED, ha="center",
                            va="center", transform=self.ax_bt.transAxes, fontsize=9)

        self.canvas_bt.draw()

    def open_trade_modal(self, event):
        item = self.trades_tree.selection()
        if not item:
            return
        trade = self.all_trades[int(item[0])]   # el iid de cada fila es su índice real en all_trades

        modal = tk.Toplevel(self.root)
        modal.title(f"Detalle Operación - {trade['asset']} ({trade['timestamp']})")
        modal.geometry("580x480")
        modal.configure(bg=CARD)

        tk.Label(modal, text=f"Inspección Técnica: {trade['asset']}", bg=CARD, fg=FG, font=(FONT, 11, "bold")).pack(pady=8)
        info_frame = tk.Frame(modal, bg=PANEL, padx=10, pady=6)
        info_frame.pack(fill="x", padx=12)

        tk.Label(info_frame, text=f"Estrategia: {trade['strategy']} | Dirección: {'CALL ▲' if trade['direction'] == 1 else 'PUT ▼'}", bg=PANEL, fg=FG, font=(FONT, 8, "bold")).pack(anchor="w")
        inds = trade.get("indicators", {})
        tk.Label(info_frame, text=f"Precio Entrada: {inds.get('price', '—')}", bg=PANEL, fg=MUTED, font=(FONT, 8)).pack(anchor="w")
        tk.Label(info_frame, text=f"Filtro IA / Razón: {inds.get('reason', 'Sin filtro')}", bg=PANEL, fg=ACCENT, font=(FONT, 8)).pack(anchor="w")

        snapshot = trade.get("snapshot", [])
        if snapshot:
            fig = Figure(figsize=(4.5, 2.2), dpi=85, facecolor=PANEL)
            ax = fig.add_subplot(111)
            ax.set_facecolor(PANEL)
            closes = [c["close"] for c in snapshot]
            ax.plot(closes, color=GOOD if trade["won"] else BAD, marker="o", linewidth=1.5)
            ax.set_title("Snapshot de Velas Previas", color=FG, fontsize=8)
            canvas = FigureCanvasTkAgg(fig, master=modal)
            canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=6)

        tk.Button(modal, text="Cerrar", command=modal.destroy, bg=ACCENT, fg="#fff", relief="flat", font=(FONT, 8, "bold"), pady=3).pack(pady=6)

    def do_analyze(self):
        try:
            history_count, expiry = int(self.v_history.get()), int(self.v_expiry.get())
        except ValueError:
            messagebox.showerror("Error", "Minutos e histórico deben ser números enteros.")
            return
        assets = [x.strip().upper() for x in self.v_assets.get().split(",") if x.strip()]
        asset = assets[0] if assets else "EURUSD"

        self.nb.select(2)
        self.lbl_bt_metrics.configure(text="⏳ Descargando velas y evaluando todas las estrategias...", fg=ACCENT)
        self.add_log("━━ Iniciando análisis multivariado de estrategias... ━━")

        def work():
            try:
                df, pay, synthetic = self.load_history(asset, history_count)
                name, table, info = backtest.search(df, expiry_candles=expiry, payout=pay)
                te = info["test"]          # tramo final que NO se usó para elegir la estrategia

                self.q.put(("bt_res", {"strat": name, "res": te, "asset": asset, "payout": pay,
                                       "synthetic": synthetic, "tested": info.get("tested")}))
                self.q.put(("log", {"text": f"✔ Análisis de {asset}: estrategia elegida '{name}'. "
                                            f"Win Rate en datos no vistos: {te['winrate']:.1%} ({te['n']} operaciones)."
                                            + (" ⚠ Datos sintéticos." if synthetic else "")}))
            except Exception as e:
                self.q.put(("log", {"text": f"✖ Error durante el análisis: {e}"}))
            finally:
                self.q.put(("analyze_done", {}))

        self.btn_analyze.configure(state="disabled")
        threading.Thread(target=work, daemon=True).start()

    def do_start(self):
        if not self.validate_cross_risks():
            return
        bot.STOP.clear()
        bot.STOP_SOFT.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop_split.configure(state="normal")
        self.update_bot_badge("RUNNING", "Operando...")

        ai_provider = self.provider_key()
        self.apply_ai_key(ai_provider)
        self.session_start_idx = len(self.all_trades)

        a = SimpleNamespace(
            sim=self.v_mode.get().startswith("Sim"),
            assets=[x.strip().upper() for x in self.v_assets.get().split(",") if x.strip()],
            amount=moneda.parse_num(self.v_amount.get()),
            expiry=int(self.v_expiry.get()),
            history=int(self.v_history.get()),
            strategy=self.v_strat.get(),
            ai=ai_provider,
            max_loss=moneda.parse_num(self.v_loss_day.get()),
            max_streak=int(self.v_max_streak.get()),
            max_trades=int(self.v_max_trades.get()),
            start_hour=int(self.v_start_hour.get()),
            end_hour=int(self.v_end_hour.get()),
            risk_streak_trigger=2,
            risk_reduction_pct=float(self.v_risk_pct.get()),
            cop_rate=None,
            email=self.v_email.get().strip() or None,
            password=self.v_pass.get() or None
        )

        def work():
            try:
                bot.cmd_run(a)
            except Exception as e:
                self.q.put(("log", {"text": f"✖ No se pudo iniciar el bot: {e}"}))
            finally:
                self.q.put(("done", {}))

        threading.Thread(target=work, daemon=True).start()

    # ---------- IA: proveedor y clave ----------
    def provider_key(self):
        """Etiqueta de la UI -> 'groq', 'gemini' o 'none'."""
        label = self.v_ai.get()
        for k, v in LABELS.items():
            if v == label:
                return k
        return "none"

    def load_ai_key(self):
        prov = self.provider_key()
        self.v_key.set(get_key(prov) if prov in PROVIDERS else "")

    def apply_ai_key(self, prov):
        """Deja la clave disponible para esta sesión; la guarda en disco solo si 'Recordar clave' está marcado."""
        if prov not in PROVIDERS:
            return
        key = self.v_key.get().strip()
        if not key:
            return
        if self.v_remember.get():
            save_key(prov, key)
        else:
            os.environ[PROVIDERS[prov]["env"]] = key

    def do_ai_test(self):
        prov = self.provider_key()
        if prov not in PROVIDERS:
            messagebox.showinfo("Filtro de IA", "Elige primero Groq o Google Gemini.")
            return
        self.apply_ai_key(prov)
        self.btn_ai_test.configure(state="disabled")
        self.add_log(f"🔌 Probando conexión con {PROVIDERS[prov]['label']}...")

        def work():
            ok, msg = ai_filter_module.ai_ping(prov)
            self.q.put(("ai_test", {"ok": ok, "msg": msg}))

        threading.Thread(target=work, daemon=True).start()

    # ---------- Métricas en vivo ----------
    def refresh_metrics(self):
        trades = self.all_trades
        m = bot.compute_metrics(trades)
        n = m["total"]
        if n == 0:
            for k, txt in (("pnl", "$0"), ("trades", "0"), ("rate", "—"), ("ev", "$0"),
                           ("drawdown", "$0"), ("best_streak", "0"), ("worst_streak", "0")):
                self.set_stat(k, txt)
        else:
            self.set_stat("pnl", fmt(m["pnl"], True), GOOD if m["pnl"] >= 0 else BAD)
            self.set_stat("trades", str(n))
            self.set_stat("rate", f"{m['winrate']:.1%}", GOOD if m["winrate"] >= 0.5405 else WARN)
            self.set_stat("ev", fmt(m["ev"], True), GOOD if m["ev"] >= 0 else BAD)
            self.set_stat("drawdown", fmt(-m["max_drawdown"]) if m["max_drawdown"] else "$0",
                          BAD if m["max_drawdown"] else FG)
            self.set_stat("best_streak", str(m["best_streak"]), GOOD)
            self.set_stat("worst_streak", str(m["worst_streak"]), BAD if m["worst_streak"] else FG)

        # Racha actual (ganadas o perdidas seguidas, al final de la lista)
        cur, kind = 0, None
        for t in reversed(trades):
            if kind is None:
                kind = bool(t["won"])
            if bool(t["won"]) == kind:
                cur += 1
            else:
                break
        if cur:
            self.lbl_streak_live.configure(
                text=f"Racha Actual: {cur} {'ganadas' if kind else 'perdidas'}", fg=GOOD if kind else BAD)
        else:
            self.lbl_streak_live.configure(text="Racha Actual: 0", fg=FG)

        # Operaciones restantes de la sesión en curso
        try:
            max_trades = int(self.v_max_trades.get())
        except ValueError:
            max_trades = 0
        done = max(0, len(trades) - getattr(self, "session_start_idx", 0))
        self.lbl_trades_rem.configure(text=f"Ops Restantes: {max(0, max_trades - done)}")

        self.draw_equity()
        self.refresh_ai_report()

    def refresh_ai_report(self):
        """Reporte calculado con las operaciones reales registradas (nada estimado)."""
        def stat(ts):
            n = len(ts)
            w = sum(1 for t in ts if t["won"])
            return n, (w / n if n else None)

        with_ai = [t for t in self.all_trades if t.get("ai", "none") != "none"]
        without = [t for t in self.all_trades if t.get("ai", "none") == "none"]
        n1, w1 = stat(with_ai)
        n0, w0 = stat(without)
        fmt_wr = lambda w: f"{w:.1%}" if w is not None else "sin datos"
        vetoes = getattr(self, "ai_vetoes", 0)
        txt = (
            "MÓDULO DE TRANSPARENCIA DEL FILTRO DE IA\n\n"
            "• Cómo opera: la IA NO genera entradas. Solo puede vetar una señal que ya produjo una estrategia, "
            "mirando las últimas 20 velas de 1 minuto. Si falla, tarda o no hay clave, no veta.\n\n"
            "• Resultados REALES registrados por este programa:\n"
            f"  - Operaciones con IA activa: {n1} — win rate {fmt_wr(w1)}\n"
            f"  - Operaciones sin IA (o sin respuesta de la IA): {n0} — win rate {fmt_wr(w0)}\n"
            f"  - Señales vetadas por la IA en esta sesión: {vetoes}\n\n"
            "• Cómo leerlo: las señales vetadas no se operan, así que no se sabe si habrían ganado; por eso "
            "esta comparación NO prueba que la IA mejore el resultado. Además, con menos de ~100 operaciones por "
            "grupo la diferencia puede ser puro azar. El punto de equilibrio con payout 85 % es 54,1 %.\n"
        )
        self.txt_ai.configure(state="normal")
        self.txt_ai.delete("1.0", "end")
        self.txt_ai.insert("end", txt)
        self.txt_ai.configure(state="disabled")

    def draw_equity(self):
        ax = self.ax_eq
        ax.clear()
        ax.set_facecolor(CARD)
        ax.tick_params(colors=FG, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(LINE)
        profits = [t["profit"] for t in self.all_trades]
        if profits:
            curve = [0.0]
            for p in profits:
                curve.append(curve[-1] + p)
            color = GOOD if curve[-1] >= 0 else BAD
            ax.plot(range(len(curve)), curve, color=color, linewidth=1.8)
            ax.fill_between(range(len(curve)), curve, 0, color=color, alpha=0.12)
            ax.axhline(0, color=LINE, linewidth=0.8)
            ax.set_title("Curva de equity — resultado acumulado (COP)", color=FG, fontsize=9)
            ax.set_xlabel("Operación #", color=MUTED, fontsize=8)
        else:
            ax.text(0.5, 0.5, "Aún no hay operaciones", color=MUTED, ha="center", va="center",
                    transform=ax.transAxes, fontsize=10)
            ax.set_xticks([])
            ax.set_yticks([])
        self.fig_eq.tight_layout()
        self.canvas_equity.draw_idle()

    def update_bot_badge(self, state, text):
        colors = {"RUNNING": (GOOD, "#12301f"), "WAITING": (WARN, "#3a2e1c"),
                  "TRADE_IN_PROGRESS": (ACCENT, "#1c2e3a"), "PAUSED_LIMIT": (BAD, "#3a1c1c")}
        fg, bg = colors.get(state, (BAD, "#3a1c1c"))
        self.bot_badge.configure(text=f"● {text.upper()}", fg=fg, bg=bg)

    def update_conn_badge(self, state, msg):
        if state == "CONNECTED": self.conn_badge.configure(text="● CONECTADO", bg="#12301f", fg=GOOD)
        elif state == "RECONNECTING": self.conn_badge.configure(text="● RECONECTANDO...", bg="#3a2e1c", fg=WARN)
        else: self.conn_badge.configure(text="● DESCONECTADO", bg="#3a1c1c", fg=BAD)

    def do_stop_now(self):
        bot.STOP.set()
        self.update_bot_badge("STOPPED", "Detenido")

    def do_stop_soft(self):
        bot.STOP_SOFT.set()
        ToastNotification.show(self.root, "Deteniendo al terminar órdenes activas", "info")

    def export_to_csv(self):
        if not self.all_trades: return
        file_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if file_path:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["timestamp", "asset", "strategy", "direction", "won", "profit", "balance"])
                writer.writeheader()
                for t in self.all_trades:
                    writer.writerow({k: t.get(k, "") for k in writer.fieldnames})
            ToastNotification.show(self.root, "Exportación completada", "good")

    def sort_treeview(self, col):
        state = getattr(self, "_sort_state", {})
        reverse = state.get(col, False)

        def key(iid):
            t = self.all_trades[int(iid)]
            if col == "profit":
                return t["profit"]            # número real, no el texto "+$4.250"
            if col == "time":
                return t["timestamp"]
            return str(self.trades_tree.set(iid, col)).lower()

        for pos, iid in enumerate(sorted(self.trades_tree.get_children(""), key=key, reverse=reverse)):
            self.trades_tree.move(iid, "", pos)
        state[col] = not reverse
        self._sort_state = state

    def apply_filters(self):
        query = self.f_search.get().lower()
        self.trades_tree.delete(*self.trades_tree.get_children())
        for idx, t in enumerate(self.all_trades):
            if query in t["asset"].lower() or query in t["strategy"].lower():
                self.trades_tree.insert("", 0, iid=str(idx), values=(
                    t["timestamp"], t["asset"], "CALL ▲" if t["direction"] == 1 else "PUT ▼",
                    t["strategy"], "GANADA" if t["won"] else "PERDIDA", fmt(t["profit"], True)
                ), tags=("win" if t["won"] else "loss",))

    def restore_previous_session(self):
        saved = bot.load_session_state()
        if saved and "trades" in saved:
            self.all_trades = saved["trades"]
            self.apply_filters()
        self.refresh_metrics()

    def fetch_rate(self, force=True):
        def work():
            try:
                self.q.put(("rate", {"value": moneda.usd_cop(), "force": force}))
            except Exception: pass
        threading.Thread(target=work, daemon=True).start()

    def set_stat(self, key, text, color=None):
        if key in self.stat:
            self.stat[key].configure(text=text, fg=color or FG)

    def add_log(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_close(self):
        bot.STOP.set()
        self.root.destroy()

    def poll(self):
        try:
            while True:
                kind, d = self.q.get_nowait()
                try:
                    self.handle_event(kind, d)
                except Exception as e:
                    self.add_log(f"⚠ Error de interfaz en evento '{kind}': {e}")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def handle_event(self, kind, d):
        if kind == "log":
            self.add_log(d["text"])
        elif kind == "balance":
            self.set_stat("balance", fmt(d["value"]))
            self.refresh_metrics()
        elif kind == "trade":
            self.all_trades.append(d)
            self.set_stat("balance", fmt(d["balance"]))
            self.apply_filters()
            self.refresh_metrics()
            ToastNotification.show(self.root, f"Operación {d['asset']}: {'GANADA' if d['won'] else 'PERDIDA'}", "good" if d['won'] else "bad")
        elif kind == "bt_res":
            self.update_bt_report(d)
        elif kind == "badge_status":
            self.update_bot_badge(d["state"], d["text"])
        elif kind == "conn_status":
            self.update_conn_badge(d["state"], d["msg"])
        elif kind == "ai_veto":
            self.ai_vetoes = getattr(self, "ai_vetoes", 0) + 1
            self.refresh_ai_report()
        elif kind == "ai_test":
            self.btn_ai_test.configure(state="normal")
            self.add_log(("✔ " if d["ok"] else "✖ ") + d["msg"])
            ToastNotification.show(self.root, "IA conectada correctamente" if d["ok"] else "Falló la conexión con la IA",
                                   "good" if d["ok"] else "bad")
        elif kind == "analyze_done":
            self.btn_analyze.configure(state="normal")
        elif kind == "done":
            self.btn_start.configure(state="normal")
            self.btn_stop_split.configure(state="disabled")
            self.update_bot_badge("STOPPED", "Detenido")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()