"""Backtester module — historical simulation with realistic cost modelling."""

from perp_bot.backtest.config import BacktestConfig
from perp_bot.backtest.cost_model import FeeModel, FundingModel, SlippageModel
from perp_bot.backtest.engine import BacktestEngine
from perp_bot.backtest.metrics import compute_all
from perp_bot.backtest.results import BacktestResult, TradeRecord, WalkForwardResult
from perp_bot.backtest.sensitivity import ParameterSensitivityAnalyzer
from perp_bot.backtest.walk_forward import WalkForwardRunner

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "FeeModel",
    "FundingModel",
    "ParameterSensitivityAnalyzer",
    "SlippageModel",
    "TradeRecord",
    "WalkForwardResult",
    "WalkForwardRunner",
    "compute_all",
]
