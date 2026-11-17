"""PyQt6 desktop GUI for the multi-lottery analysis and number selection assistant."""

from __future__ import annotations

import re
import sys
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.analysis.engine import AnalysisEngine
from src.core.models import DrawRecord, LotteryRule, NumberStat, Ticket, ZoneRule
from src.core.registry import list_lottery_rules
from src.data.adapters import AdapterManager, DataAdapterError
from src.data.storage import SQLiteStorage
from src.strategy.backtest import BacktestEngine
from src.strategy.generator import FilterConfig, StrategyType, TicketGenerator


class NumericTableItem(QTableWidgetItem):
    """Table item that sorts numerically when possible."""

    def __init__(self, text: str, sort_value: float) -> None:
        super().__init__(text)
        self._sort_value = sort_value
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def __lt__(self, other: object) -> bool:
        if isinstance(other, NumericTableItem):
            return self._sort_value < other._sort_value
        return super().__lt__(other)


class FrequencyChartWidget(QWidget):
    """Custom QPainter bar chart comparing empirical frequency vs theoretical expectation."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._stats: List[NumberStat] = []
        self._zone: Optional[ZoneRule] = None
        self.setMinimumHeight(250)

    def set_data(self, stats: List[NumberStat], zone: ZoneRule) -> None:
        self._stats = stats
        self._zone = zone
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))

        w, h = self.width(), self.height()
        if not self._stats or self._zone is None or self._stats[0].total_draws == 0:
            painter.setPen(QColor("#888888"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "暂无历史数据，请先在「数据中心」同步或导入数据",
            )
            return

        pad_l, pad_r, pad_t, pad_b = 38, 16, 22, 30
        plot_w = max(w - pad_l - pad_r, 10)
        plot_h = max(h - pad_t - pad_b, 10)

        max_freq = max(max(s.frequency for s in self._stats), 1)
        expected_freq = self._stats[0].total_draws * self._zone.theory_prob
        y_max = max(max_freq, expected_freq) * 1.15

        # Axes
        painter.setPen(QPen(QColor("#D0D7DE"), 1))
        painter.drawLine(pad_l, pad_t, pad_l, h - pad_b)
        painter.drawLine(pad_l, h - pad_b, w - pad_r, h - pad_b)

        n = len(self._stats)
        step = plot_w / n
        bar_w = max(step * 0.68, 3.0)

        painter.setFont(QFont("Menlo", 8))
        for idx, s in enumerate(self._stats):
            x0 = pad_l + idx * step + (step - bar_w) / 2.0
            bar_h = (s.frequency / y_max) * plot_h
            y0 = h - pad_b - bar_h
            color = QColor(self._zone.color) if s.frequency >= expected_freq else QColor("#90A4AE")
            painter.fillRect(int(x0), int(y0), max(int(bar_w), 2), int(bar_h), color)

            if n <= 20 or idx % 2 == 0:
                painter.setPen(QColor("#555555"))
                painter.drawText(
                    int(x0 - 4),
                    h - pad_b + 4,
                    int(bar_w + 8),
                    20,
                    Qt.AlignmentFlag.AlignCenter,
                    self._zone.format_num(s.number),
                )

        # Theoretical expectation dashed line
        exp_y = int(h - pad_b - (expected_freq / y_max) * plot_h)
        pen = QPen(QColor("#2E7D32"), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(pad_l, exp_y, w - pad_r, exp_y)

        painter.setFont(QFont("PingFang SC", 9))
        painter.drawText(
            w - pad_r - 120,
            max(exp_y - 18, pad_t),
            116,
            16,
            Qt.AlignmentFlag.AlignRight,
            f"理论均值 {expected_freq:.1f}",
        )


class SyncWorker(QObject):
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, adapters: AdapterManager, storage: SQLiteStorage, code: str, limit: int = 100) -> None:
        super().__init__()
        self.adapters = adapters
        self.storage = storage
        self.code = code
        self.limit = limit

    def run(self) -> None:
        try:
            records = self.adapters.fetch_online(self.code, limit=self.limit)
            saved = self.storage.save_draws(records)
            self.finished.emit(saved)
        except Exception as exc:
            self.error.emit(str(exc))


class BacktestWorker(QObject):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(
        self,
        engine: BacktestEngine,
        rule: LotteryRule,
        draws: List[DrawRecord],
        strategy: StrategyType,
        train_window: int,
        test_periods: int,
        tickets_per_draw: int,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.rule = rule
        self.draws = draws
        self.strategy = strategy
        self.train_window = train_window
        self.test_periods = test_periods
        self.tickets_per_draw = tickets_per_draw

    def run(self) -> None:
        try:
            res = self.engine.run(
                rule=self.rule,
                draws=self.draws,
                strategy=self.strategy,
                train_window=self.train_window,
                test_periods=self.test_periods,
                tickets_per_draw=self.tickets_per_draw,
            )
            self.finished.emit(res)
        except Exception as exc:
            self.error.emit(str(exc))


class LotteryMainWindow(QMainWindow):
    """Main PyQt6 application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("彩票选号与历史概率分析助手 (PyQt6)")
        self.resize(1180, 780)
        self.setMinimumSize(980, 660)

        self.storage = SQLiteStorage()
        self.adapters = AdapterManager()
        self.analysis_engine = AnalysisEngine()
        self.generator = TicketGenerator()
        self.backtest_engine = BacktestEngine()

        self.rules: List[LotteryRule] = list_lottery_rules()
        self.current_rule: LotteryRule = self.rules[0]
        self.current_draws: List[DrawRecord] = []
        self.current_stats: Dict[str, List[NumberStat]] = {}
        self.last_tickets: List[Ticket] = []

        self._active_thread: Optional[QThread] = None
        self._active_worker: Optional[QObject] = None

        self._apply_stylesheet()
        self._build_ui()
        self._load_current_lottery_data()

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background-color: #F5F7FA; }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #DCE1E8;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 12px;
                background-color: #FFFFFF;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #1A73E8;
                color: #FFFFFF;
                border: none;
                border-radius: 5px;
                padding: 6px 14px;
                font-weight: 500;
            }
            QPushButton:hover { background-color: #1557B0; }
            QPushButton:disabled { background-color: #B0BEC5; }
            QPushButton#SecondaryBtn {
                background-color: #EEF2F6;
                color: #333333;
                border: 1px solid #D0D7DE;
            }
            QPushButton#SecondaryBtn:hover { background-color: #E2E8F0; }
            QPushButton#DangerBtn {
                background-color: #D93025;
                color: #FFFFFF;
            }
            QPushButton#DangerBtn:hover { background-color: #B31412; }
            QTableWidget {
                background-color: #FFFFFF;
                border: 1px solid #DCE1E8;
                gridline-color: #EDF0F5;
            }
            QHeaderView::section {
                background-color: #F0F3F8;
                padding: 5px;
                border: none;
                border-bottom: 1px solid #DCE1E8;
                font-weight: bold;
            }
            """
        )

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 12, 14, 8)
        root_layout.setSpacing(10)

        # Header
        header_layout = QHBoxLayout()
        title_lbl = QLabel("彩票选号与历史数据分析助手")
        title_lbl.setFont(QFont("PingFang SC", 16, QFont.Weight.Bold))
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()

        header_layout.addWidget(QLabel("当前彩种:"))
        self.lottery_combo = QComboBox()
        self.lottery_combo.addItems([r.name for r in self.rules])
        self.lottery_combo.setMinimumWidth(110)
        self.lottery_combo.currentIndexChanged.connect(self._on_lottery_changed)
        header_layout.addWidget(self.lottery_combo)

        header_layout.addSpacing(12)
        header_layout.addWidget(QLabel("分析窗口:"))
        self.window_combo = QComboBox()
        self.window_combo.addItems(["近30期", "近50期", "近100期", "近200期", "全部"])
        self.window_combo.setCurrentText("近100期")
        self.window_combo.currentIndexChanged.connect(lambda _i: self._refresh_analysis_views())
        header_layout.addWidget(self.window_combo)

        header_layout.addSpacing(12)
        self.rule_desc_lbl = QLabel(self.current_rule.description)
        self.rule_desc_lbl.setStyleSheet("color: #555555;")
        header_layout.addWidget(self.rule_desc_lbl)

        root_layout.addLayout(header_layout)

        # Tabs
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs, 1)

        self.tab_data = QWidget()
        self.tab_analysis = QWidget()
        self.tab_generator = QWidget()
        self.tab_backtest = QWidget()

        self.tabs.addTab(self.tab_data, "数据中心")
        self.tabs.addTab(self.tab_analysis, "概率与统计分析")
        self.tabs.addTab(self.tab_generator, "智能选号与评估")
        self.tabs.addTab(self.tab_backtest, "策略回测")

        self._build_data_tab()
        self._build_analysis_tab()
        self._build_generator_tab()
        self._build_backtest_tab()

        # Status Bar
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        self.status_lbl = QLabel("就绪")
        status_bar.addWidget(self.status_lbl, 1)
        disclaimer = QLabel("提示：历史统计仅反映经验分布，彩票开奖为独立随机事件，请理性购彩。")
        disclaimer.setStyleSheet("color: #777777;")
        status_bar.addPermanentWidget(disclaimer)

    # ------------------------------------------------------------------ Tab 1: Data
    def _build_data_tab(self) -> None:
        layout = QVBoxLayout(self.tab_data)

        toolbar = QHBoxLayout()
        self.btn_sync = QPushButton("在线采集同步 (100期)")
        self.btn_sync.clicked.connect(self._on_sync_online)
        toolbar.addWidget(self.btn_sync)

        btn_import = QPushButton("导入 CSV / Excel")
        btn_import.setObjectName("SecondaryBtn")
        btn_import.clicked.connect(self._on_import_file)
        toolbar.addWidget(btn_import)

        btn_mock = QPushButton("生成模拟历史数据 (演示用)")
        btn_mock.setObjectName("SecondaryBtn")
        btn_mock.clicked.connect(self._on_generate_mock)
        toolbar.addWidget(btn_mock)

        toolbar.addStretch()
        self.data_count_lbl = QLabel("当前本地已存储: 0 期")
        self.data_count_lbl.setStyleSheet("font-weight: bold; color: #333333;")
        toolbar.addWidget(self.data_count_lbl)

        toolbar.addSpacing(12)
        btn_clear = QPushButton("清空当前彩种数据")
        btn_clear.setObjectName("DangerBtn")
        btn_clear.clicked.connect(self._on_clear_data)
        toolbar.addWidget(btn_clear)

        layout.addLayout(toolbar)

        self.data_table = QTableWidget(0, 3)
        self.data_table.setHorizontalHeaderLabels(["期号", "开奖日期", "开奖号码"])
        self.data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.data_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = self.data_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.data_table, 1)

    # ------------------------------------------------------------------ Tab 2: Analysis
    def _build_analysis_tab(self) -> None:
        layout = QVBoxLayout(self.tab_analysis)

        top = QHBoxLayout()
        top.addWidget(QLabel("查看分区:"))
        self.zone_combo = QComboBox()
        self.zone_combo.setMinimumWidth(110)
        self.zone_combo.currentIndexChanged.connect(lambda _i: self._render_zone_analysis())
        top.addWidget(self.zone_combo)
        top.addSpacing(12)
        note = QLabel("说明：欲出度 = 当前遗漏 / 平均遗漏（>1 表示超出平均遗漏周期）；点击表头可排序。")
        note.setStyleSheet("color: #666666;")
        top.addWidget(note)
        top.addStretch()
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # Left table
        self.stat_table = QTableWidget(0, 9)
        self.stat_table.setHorizontalHeaderLabels(
            ["号码", "次数", "历史概率", "理论概率", "当前遗漏", "平均遗漏", "最大遗漏", "欲出度", "综合分"]
        )
        self.stat_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.stat_table.setSortingEnabled(True)
        self.stat_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self.stat_table)

        # Right panel: Chart + Pattern summary
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 0, 0, 0)

        chart_grp = QGroupBox("号码出现频次 vs 理论期望线")
        chart_v = QVBoxLayout(chart_grp)
        self.chart_widget = FrequencyChartWidget()
        chart_v.addWidget(self.chart_widget)
        right_layout.addWidget(chart_grp, 3)

        pat_grp = QGroupBox("历史形态分布概览")
        pat_v = QVBoxLayout(pat_grp)
        self.pattern_text = QTextEdit()
        self.pattern_text.setReadOnly(True)
        self.pattern_text.setMaximumHeight(170)
        pat_v.addWidget(self.pattern_text)
        right_layout.addWidget(pat_grp, 2)

        splitter.addWidget(right_widget)
        splitter.setSizes([640, 480])

    # ------------------------------------------------------------------ Tab 3: Generator & Evaluator
    def _build_generator_tab(self) -> None:
        layout = QVBoxLayout(self.tab_generator)

        cfg_grp = QGroupBox("智能选号配置")
        cfg_layout = QHBoxLayout(cfg_grp)

        cfg_layout.addWidget(QLabel("选号策略:"))
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems([s.value for s in StrategyType])
        self.strategy_combo.setMinimumWidth(165)
        cfg_layout.addWidget(self.strategy_combo)

        cfg_layout.addSpacing(8)
        cfg_layout.addWidget(QLabel("推荐模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["确定性 Top-N 排序", "加权随机采样"])
        self.mode_combo.setMinimumWidth(145)
        cfg_layout.addWidget(self.mode_combo)

        cfg_layout.addSpacing(8)
        cfg_layout.addWidget(QLabel("注数:"))
        self.gen_count_spin = QSpinBox()
        self.gen_count_spin.setRange(1, 20)
        self.gen_count_spin.setValue(5)
        cfg_layout.addWidget(self.gen_count_spin)

        cfg_layout.addSpacing(10)
        cfg_layout.addWidget(QLabel("和值范围(可选):"))
        self.sum_min_edit = QLineEdit()
        self.sum_min_edit.setFixedWidth(56)
        self.sum_min_edit.setPlaceholderText("最小")
        cfg_layout.addWidget(self.sum_min_edit)
        cfg_layout.addWidget(QLabel("~"))
        self.sum_max_edit = QLineEdit()
        self.sum_max_edit.setFixedWidth(56)
        self.sum_max_edit.setPlaceholderText("最大")
        cfg_layout.addWidget(self.sum_max_edit)

        cfg_layout.addSpacing(12)
        btn_gen = QPushButton("生成推荐号码")
        btn_gen.clicked.connect(self._on_generate_tickets)
        cfg_layout.addWidget(btn_gen)

        btn_copy_text = QPushButton("复制文字")
        btn_copy_text.setObjectName("SecondaryBtn")
        btn_copy_text.clicked.connect(self._on_copy_tickets_text)
        cfg_layout.addWidget(btn_copy_text)

        btn_copy_img = QPushButton("复制图片")
        btn_copy_img.setObjectName("SecondaryBtn")
        btn_copy_img.clicked.connect(self._on_copy_tickets_image)
        cfg_layout.addWidget(btn_copy_img)
        cfg_layout.addStretch()

        layout.addWidget(cfg_grp)

        # Scrollable tickets area
        list_grp = QGroupBox("推荐号码列表")
        list_v = QVBoxLayout(list_grp)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.tickets_container = QWidget()
        self.tickets_layout = QVBoxLayout(self.tickets_container)
        self.tickets_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.tickets_container)
        list_v.addWidget(scroll)
        layout.addWidget(list_grp, 1)

        # Manual evaluator
        eval_grp = QGroupBox("自选号码概率与形态评估")
        eval_v = QVBoxLayout(eval_grp)

        e_row = QHBoxLayout()
        e_row.addWidget(QLabel("输入号码:"))
        self.manual_input_edit = QLineEdit()
        self.manual_input_edit.setMinimumWidth(300)
        self.manual_input_edit.setFont(QFont("Menlo", 11))
        e_row.addWidget(self.manual_input_edit)

        btn_eval = QPushButton("评估当前选号")
        btn_eval.clicked.connect(self._on_evaluate_manual)
        e_row.addWidget(btn_eval)

        self.manual_hint_lbl = QLabel("示例: 03 08 15 21 27 32 + 09")
        self.manual_hint_lbl.setStyleSheet("color: #666666;")
        e_row.addWidget(self.manual_hint_lbl)
        e_row.addStretch()
        eval_v.addLayout(e_row)

        self.eval_result_lbl = QLabel(
            "请输入一注号码并点击“评估当前选号”，查看其在历史开奖中的形态分布与经验契合度。"
        )
        self.eval_result_lbl.setWordWrap(True)
        eval_v.addWidget(self.eval_result_lbl)

        layout.addWidget(eval_grp)

    # ------------------------------------------------------------------ Tab 4: Backtest
    def _build_backtest_tab(self) -> None:
        layout = QVBoxLayout(self.tab_backtest)

        ctrl_grp = QGroupBox("历史滚动回测参数")
        ctrl = QHBoxLayout(ctrl_grp)

        ctrl.addWidget(QLabel("回测策略:"))
        self.bt_strategy_combo = QComboBox()
        self.bt_strategy_combo.addItems([s.value for s in StrategyType])
        self.bt_strategy_combo.setMinimumWidth(180)
        ctrl.addWidget(self.bt_strategy_combo)

        ctrl.addSpacing(10)
        ctrl.addWidget(QLabel("训练窗口(期):"))
        self.bt_train_spin = QSpinBox()
        self.bt_train_spin.setRange(20, 300)
        self.bt_train_spin.setValue(50)
        ctrl.addWidget(self.bt_train_spin)

        ctrl.addSpacing(10)
        ctrl.addWidget(QLabel("回测期数:"))
        self.bt_periods_spin = QSpinBox()
        self.bt_periods_spin.setRange(5, 100)
        self.bt_periods_spin.setValue(20)
        ctrl.addWidget(self.bt_periods_spin)

        ctrl.addSpacing(10)
        ctrl.addWidget(QLabel("每期注数:"))
        self.bt_tickets_spin = QSpinBox()
        self.bt_tickets_spin.setRange(1, 20)
        self.bt_tickets_spin.setValue(5)
        ctrl.addWidget(self.bt_tickets_spin)

        ctrl.addSpacing(12)
        self.btn_backtest = QPushButton("开始回测")
        self.btn_backtest.clicked.connect(self._on_run_backtest)
        ctrl.addWidget(self.btn_backtest)
        ctrl.addStretch()

        layout.addWidget(ctrl_grp)

        self.bt_output = QTextEdit()
        self.bt_output.setReadOnly(True)
        self.bt_output.setFont(QFont("Menlo", 11))
        self.bt_output.setText(
            "回测说明：系统将按时间顺序滚动模拟——仅使用每期开奖前的历史数据训练选号权重，再与当期真实开奖结果比对。\n"
            "请先在「数据中心」确保当前彩种至少有 (训练窗口 + 回测期数) 条历史记录。\n"
        )
        layout.addWidget(self.bt_output, 1)

    # ------------------------------------------------------------------ Helpers & Logic
    def _get_window_size(self) -> Optional[int]:
        mapping = {"近30期": 30, "近50期": 50, "近100期": 100, "近200期": 200, "全部": None}
        return mapping.get(self.window_combo.currentText(), 100)

    def _on_lottery_changed(self, idx: int) -> None:
        if 0 <= idx < len(self.rules):
            self.current_rule = self.rules[idx]
        self.rule_desc_lbl.setText(self.current_rule.description)
        hints = {
            "ssq": "示例: 03 08 15 21 27 32 + 09",
            "dlt": "示例: 02 11 18 25 33 + 04 10",
            "fc3d": "示例: 3 5 8 (百位 十位 个位)",
        }
        self.manual_hint_lbl.setText(hints.get(self.current_rule.code, ""))
        self.manual_input_edit.clear()
        self.last_tickets = []
        self._render_tickets([])
        self._load_current_lottery_data()

    def _load_current_lottery_data(self) -> None:
        self.current_draws = self.storage.get_draws(self.current_rule.code)
        self.data_count_lbl.setText(f"当前本地已存储: {len(self.current_draws)} 期")

        self.data_table.setRowCount(0)
        display_draws = self.current_draws[:500]
        self.data_table.setRowCount(len(display_draws))
        for row_idx, d in enumerate(display_draws):
            item_issue = QTableWidgetItem(d.issue)
            item_issue.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_date = QTableWidgetItem(d.draw_date)
            item_date.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_nums = QTableWidgetItem(d.format_display(self.current_rule))
            self.data_table.setItem(row_idx, 0, item_issue)
            self.data_table.setItem(row_idx, 1, item_date)
            self.data_table.setItem(row_idx, 2, item_nums)

        self.zone_combo.blockSignals(True)
        self.zone_combo.clear()
        self.zone_combo.addItems([z.label for z in self.current_rule.zones])
        self.zone_combo.blockSignals(False)

        self._refresh_analysis_views()

    def _refresh_analysis_views(self) -> None:
        window = self._get_window_size()
        self.current_stats = self.analysis_engine.analyze_all_zones(
            self.current_draws, self.current_rule, window=window
        )
        self._render_zone_analysis()
        self._render_pattern_summary(window)

    def _render_zone_analysis(self) -> None:
        zone_label = self.zone_combo.currentText()
        zone = next((z for z in self.current_rule.zones if z.label == zone_label), self.current_rule.zones[0])
        stats = self.current_stats.get(zone.key, [])

        self.stat_table.setSortingEnabled(False)
        self.stat_table.setRowCount(len(stats))
        for row_idx, s in enumerate(stats):
            self.stat_table.setItem(row_idx, 0, NumericTableItem(zone.format_num(s.number), s.number))
            self.stat_table.setItem(row_idx, 1, NumericTableItem(str(s.frequency), s.frequency))
            self.stat_table.setItem(
                row_idx, 2, NumericTableItem(f"{s.empirical_prob * 100:.2f}%", s.empirical_prob)
            )
            self.stat_table.setItem(
                row_idx, 3, NumericTableItem(f"{s.theory_prob * 100:.2f}%", s.theory_prob)
            )
            self.stat_table.setItem(row_idx, 4, NumericTableItem(str(s.current_omission), s.current_omission))
            self.stat_table.setItem(row_idx, 5, NumericTableItem(f"{s.avg_omission:.1f}", s.avg_omission))
            self.stat_table.setItem(row_idx, 6, NumericTableItem(str(s.max_omission), s.max_omission))
            self.stat_table.setItem(row_idx, 7, NumericTableItem(f"{s.desire_index:.2f}", s.desire_index))
            self.stat_table.setItem(row_idx, 8, NumericTableItem(f"{s.score:.1f}", s.score))
        self.stat_table.setSortingEnabled(True)

        self.chart_widget.set_data(stats, zone)

    def _render_pattern_summary(self, window: Optional[int]) -> None:
        pat = self.analysis_engine.analyze_patterns(self.current_draws, self.current_rule, window=window)
        if pat["total"] == 0:
            self.pattern_text.setText("暂无历史数据。")
            return
        lines = [
            f"统计样本：最近 {pat['total']} 期",
            f"和值均值：{pat['sum_avg']}（范围 {pat['sum_min']}~{pat['sum_max']}，核心区间 P25-P75: [{pat['sum_p25']}, {pat['sum_p75']}]）",
            f"平均跨度：{pat['span_avg']}",
            "高频奇偶比：" + "，".join(f"{k} ({pct}%)" for k, _, pct in pat["odd_even_top"]),
            "高频大小比：" + "，".join(f"{k} ({pct}%)" for k, _, pct in pat["size_ratio_top"]),
        ]
        self.pattern_text.setText("\n".join(lines))

    # ------------------------------------------------------------------ Data Actions
    def _on_sync_online(self) -> None:
        self.btn_sync.setEnabled(False)
        self.status_lbl.setText(f"正在从公开接口采集 {self.current_rule.name} 历史数据...")

        thread = QThread()
        worker = SyncWorker(self.adapters, self.storage, self.current_rule.code, limit=100)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_sync_finished)
        worker.error.connect(self._on_sync_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._active_thread = thread
        self._active_worker = worker
        thread.start()

    def _on_sync_finished(self, saved: int) -> None:
        self.btn_sync.setEnabled(True)
        self.status_lbl.setText(f"在线同步完成，写入/更新 {saved} 期数据。")
        self._load_current_lottery_data()

    def _on_sync_error(self, err: str) -> None:
        self.btn_sync.setEnabled(True)
        self.status_lbl.setText("在线采集失败")
        QMessageBox.critical(
            self,
            "采集失败",
            f"{err}\n\n您也可以使用「导入 CSV / Excel」或「生成模拟历史数据」进行本地分析。",
        )

    def _on_import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择历史开奖数据文件",
            "",
            "CSV / Excel 文件 (*.csv *.xlsx *.xls);;所有文件 (*)",
        )
        if not path:
            return
        try:
            records = self.adapters.import_file(self.current_rule.code, path)
            saved = self.storage.save_draws(records)
            self.status_lbl.setText(f"成功导入 {saved} 期数据。")
            self._load_current_lottery_data()
            QMessageBox.information(
                self, "导入成功", f"已导入并保存 {saved} 期 {self.current_rule.name} 历史数据。"
            )
        except DataAdapterError as exc:
            QMessageBox.critical(self, "导入失败", str(exc))

    def _on_generate_mock(self) -> None:
        records = self.adapters.generate_mock(self.current_rule.code, limit=150)
        saved = self.storage.save_draws(records)
        self.status_lbl.setText(f"已生成并保存 {saved} 期 {self.current_rule.name} 模拟数据。")
        self._load_current_lottery_data()

    def _on_clear_data(self) -> None:
        reply = QMessageBox.question(
            self,
            "确认清空",
            f"确定要清空 {self.current_rule.name} 的所有本地历史数据吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted = self.storage.clear_draws(self.current_rule.code)
        self.status_lbl.setText(f"已清空 {deleted} 期数据。")
        self._load_current_lottery_data()

    # ------------------------------------------------------------------ Generator Actions
    def _on_generate_tickets(self) -> None:
        strategy_val = self.strategy_combo.currentText()
        strategy = next((s for s in StrategyType if s.value == strategy_val), StrategyType.COMPOSITE)
        count = self.gen_count_spin.value()

        s_min = self.sum_min_edit.text().strip()
        s_max = self.sum_max_edit.text().strip()
        filter_cfg = FilterConfig(
            sum_min=int(s_min) if s_min.isdigit() else None,
            sum_max=int(s_max) if s_max.isdigit() else None,
        )

        is_deterministic = self.mode_combo.currentText() == "确定性 Top-N 排序"
        try:
            tickets = self.generator.generate(
                rule=self.current_rule,
                draws=self.current_draws,
                strategy=strategy,
                count=count,
                window=self._get_window_size(),
                filter_cfg=filter_cfg,
                deterministic=is_deterministic,
            )
        except Exception as exc:
            QMessageBox.critical(self, "生成失败", str(exc))
            return

        if not tickets:
            QMessageBox.warning(
                self, "无满足条件的组合", "当前和值过滤条件过严，未能生成符合条件的号码，请放宽条件。"
            )
            return

        self.last_tickets = tickets
        self._render_tickets(tickets)
        mode_tag = "确定性 Top-N 排序" if is_deterministic else "加权随机采样"
        self.status_lbl.setText(f"已使用「{strategy.value} · {mode_tag}」生成 {len(tickets)} 注推荐号码。")

    def _render_tickets(self, tickets: List[Ticket]) -> None:
        while self.tickets_layout.count():
            item = self.tickets_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not tickets:
            lbl = QLabel("点击上方「生成推荐号码」生成彩球组合")
            lbl.setStyleSheet("color: #888888; padding: 20px;")
            self.tickets_layout.addWidget(lbl)
            return

        for idx, tk_obj in enumerate(tickets, start=1):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(6, 4, 6, 4)

            idx_lbl = QLabel(f"Top {idx:02d}")
            idx_lbl.setFixedWidth(56)
            idx_lbl.setStyleSheet("font-weight: bold; color: #333333;")
            row_layout.addWidget(idx_lbl)

            for z in self.current_rule.zones:
                for num in tk_obj.numbers[z.key]:
                    ball = QLabel(z.format_num(num))
                    ball.setFixedSize(32, 32)
                    ball.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    ball.setStyleSheet(
                        f"background-color: {z.color}; color: #FFFFFF; "
                        "border-radius: 16px; font-family: Menlo; font-weight: bold; font-size: 13px;"
                    )
                    row_layout.addWidget(ball)
                row_layout.addSpacing(8)

            info_lbl = QLabel(f"概率评分 {tk_obj.score:.1f}  |  {tk_obj.pattern_summary}")
            info_lbl.setStyleSheet("color: #555555;")
            row_layout.addWidget(info_lbl)
            row_layout.addStretch()

            self.tickets_layout.addWidget(row_widget)

    @staticmethod
    def format_tickets_text(rule: LotteryRule, tickets: List[Ticket]) -> str:
        """Format tickets as plain text containing only zone colors/labels and ball numbers."""
        lines: List[str] = []
        for tk_obj in tickets:
            parts = [
                f"{z.label}: {' '.join(z.format_num(n) for n in tk_obj.numbers[z.key])}"
                for z in rule.zones
            ]
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    @staticmethod
    def render_tickets_image(rule: LotteryRule, tickets: List[Ticket]) -> QImage:
        """Render tickets as an image containing only colored balls and their numbers."""
        ball_d = 38
        ball_gap = 10
        zone_gap = 16
        pad_x = 20
        pad_y = 18
        row_gap = 14

        total_balls = sum(z.count for z in rule.zones)
        total_zone_gaps = max(len(rule.zones) - 1, 0)
        img_w = pad_x * 2 + total_balls * ball_d + max(total_balls - 1, 0) * ball_gap + total_zone_gaps * zone_gap
        img_h = pad_y * 2 + len(tickets) * ball_d + max(len(tickets) - 1, 0) * row_gap

        scale = 2  # HiDPI crisp rendering
        image = QImage(img_w * scale, img_h * scale, QImage.Format.Format_ARGB32)
        image.setDevicePixelRatio(scale)
        image.fill(QColor("#FFFFFF"))

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Menlo", 13, QFont.Weight.Bold))

        y = pad_y
        for tk_obj in tickets:
            x = pad_x
            for z_idx, z in enumerate(rule.zones):
                ball_color = QColor(z.color)
                for num in tk_obj.numbers[z.key]:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(ball_color)
                    painter.drawEllipse(x, y, ball_d, ball_d)

                    painter.setPen(QColor("#FFFFFF"))
                    painter.drawText(
                        x, y, ball_d, ball_d, Qt.AlignmentFlag.AlignCenter, z.format_num(num)
                    )
                    x += ball_d + ball_gap
                if z_idx < len(rule.zones) - 1:
                    x += zone_gap
            y += ball_d + row_gap

        painter.end()
        return image

    def _on_copy_tickets_text(self) -> None:
        if not self.last_tickets:
            QMessageBox.information(self, "无内容", "请先生成推荐号码。")
            return
        text = self.format_tickets_text(self.current_rule, self.last_tickets)
        QApplication.clipboard().setText(text)
        self.status_lbl.setText("已复制推荐号码（纯文字）到剪贴板。")

    def _on_copy_tickets_image(self) -> None:
        if not self.last_tickets:
            QMessageBox.information(self, "无内容", "请先生成推荐号码。")
            return
        image = self.render_tickets_image(self.current_rule, self.last_tickets)
        QApplication.clipboard().setImage(image)
        self.status_lbl.setText("已复制推荐号码（彩球图片）到剪贴板。")

    def _on_evaluate_manual(self) -> None:
        raw = self.manual_input_edit.text().strip()
        if not raw:
            QMessageBox.warning(self, "输入为空", "请先输入要评估的号码。")
            return

        try:
            numbers = self._parse_manual_input(raw)
            res = self.analysis_engine.evaluate_ticket(
                self.current_draws, self.current_rule, numbers, window=self._get_window_size()
            )
        except Exception as exc:
            QMessageBox.critical(self, "号码格式或规则错误", str(exc))
            return

        feat = res["features"]
        diag = "\n• ".join(res["diagnostics"])
        self.eval_result_lbl.setText(
            f"综合经验契合度：{res['overall_fit']} / 100  （单号均分 {res['avg_num_score']} | "
            f"和值={feat['sum']} | 奇偶={feat['odd_even']} | 大小={feat['size_ratio']} | 跨度={feat['span']}）\n"
            f"• {diag}"
        )

    def _parse_manual_input(self, raw: str) -> Dict[str, List[int]]:
        if self.current_rule.code == "fc3d":
            tokens = (
                [int(t) for t in re.findall(r"\d", raw)]
                if len(re.findall(r"\d+", raw)) == 1 and len(raw.strip()) == 3
                else [int(t) for t in re.findall(r"\d+", raw)]
            )
            if len(tokens) != 3:
                raise ValueError("福彩3D 请输入 3 个 0-9 的数字，例如：3 5 8")
            return {"hundreds": [tokens[0]], "tens": [tokens[1]], "units": [tokens[2]]}

        if "+" in raw:
            left, right = raw.split("+", 1)
        elif "|" in raw:
            left, right = raw.split("|", 1)
        else:
            raise ValueError("请使用 '+' 分隔前后区，例如：03 08 15 21 27 32 + 09")

        left_nums = [int(t) for t in re.findall(r"\d+", left)]
        right_nums = [int(t) for t in re.findall(r"\d+", right)]
        z0, z1 = self.current_rule.zones[0], self.current_rule.zones[1]
        return {z0.key: sorted(left_nums), z1.key: sorted(right_nums)}

    # ------------------------------------------------------------------ Backtest Actions
    def _on_run_backtest(self) -> None:
        strategy_val = self.bt_strategy_combo.currentText()
        strategy = next((s for s in StrategyType if s.value == strategy_val), StrategyType.COMPOSITE)

        self.btn_backtest.setEnabled(False)
        self.status_lbl.setText("正在执行历史滚动回测...")

        thread = QThread()
        worker = BacktestWorker(
            engine=self.backtest_engine,
            rule=self.current_rule,
            draws=list(self.current_draws),
            strategy=strategy,
            train_window=self.bt_train_spin.value(),
            test_periods=self.bt_periods_spin.value(),
            tickets_per_draw=self.bt_tickets_spin.value(),
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_backtest_finished)
        worker.error.connect(self._on_backtest_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._active_thread = thread
        self._active_worker = worker
        thread.start()

    def _on_backtest_finished(self, res: dict) -> None:
        self.btn_backtest.setEnabled(True)
        lines = [
            f"=== {res['lottery_name']} 回测报告 ===",
            f"策略：{res['strategy_name']}",
            f"回测期数：{res['test_periods']} 期 × 每期 {res['tickets_per_draw']} 注 = 共 {res['total_tickets']} 注",
            f"平均主区命中数：{res['avg_primary_hits']}",
            "",
            "--- 命中分布统计 ---",
        ]
        for k, v in res["hit_distribution"].items():
            pct = v / max(res["total_tickets"], 1) * 100
            lines.append(f"  {k:<16} : {v:>4} 注 ({pct:.1f}%)")

        if res["highlights"]:
            lines.append("")
            lines.append("--- 高命中记录示例 ---")
            lines.extend(f"  {h}" for h in res["highlights"])

        self.bt_output.setText("\n".join(lines))
        self.status_lbl.setText("回测完成。")

    def _on_backtest_error(self, err: str) -> None:
        self.btn_backtest.setEnabled(True)
        self.status_lbl.setText("回测失败")
        QMessageBox.critical(self, "回测失败", err)


def run_app() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = LotteryMainWindow()
    window.show()
    sys.exit(app.exec())
