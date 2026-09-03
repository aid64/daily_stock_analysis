"""截面数据面板（date × symbol 矩阵）——查询式数据服务的最小实现

重构 R5（2026-08-29）：痛点 3（backtrader feed 装不下全量 849 只转债）
的治本改造第一步。qlib 查询式数据服务的启示：数据服务按 (品种, 字段,
日期区间) 取截面矩阵，而不是要求引擎全量注册 feed——849 只转债在
矩阵范式下只是 849 列，横截面过滤/排序全是向量化运算。

输入是 PIT 长表（as_of/symbol/...），经 pivot 变为字段矩阵。
面板是不可变快照：研究计算（因子/选债/IC）在矩阵上完成，
执行模拟只消费目标权重序列（见 backtest/two_stage.py）。
"""

from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

# PIT 长表的数值字段（可 pivot 成矩阵的字段）
PANEL_FIELDS = ("price", "premium_rate")


class Panel:
    """(date × symbol) 截面面板

    由 PIT 长表构建；行 = 交易日（升序 DatetimeIndex），列 = symbol。
    缺失组合为 NaN（晚上市/已退市/停牌自然表达，无需填充）。
    """

    def __init__(self, long_table: pd.DataFrame):
        required = {"as_of", "symbol"}
        missing = required - set(long_table.columns)
        if missing:
            raise ValueError(f"长表缺少字段: {sorted(missing)}")
        table = long_table.copy()
        table["as_of"] = pd.to_datetime(table["as_of"], errors="coerce").dt.normalize()
        table = table.dropna(subset=["as_of", "symbol"])
        table["symbol"] = table["symbol"].astype(str)
        table = table.drop_duplicates(subset=["as_of", "symbol"], keep="last")

        self._panels: dict[str, pd.DataFrame] = {}
        for field in PANEL_FIELDS:
            if field in table.columns:
                value = pd.to_numeric(table[field], errors="coerce")
                self._panels[field] = value.to_frame().assign(
                    _d=table["as_of"].values, _s=table["symbol"].values
                ).pivot(index="_d", columns="_s", values=field).sort_index()
        if "price" not in self._panels:
            raise ValueError("长表缺少 price 字段，无法构建面板")
        self.dates: pd.DatetimeIndex = self._panels["price"].index
        self.symbols = self._panels["price"].columns

        # 静态/低频字段（rating/list_date/maturity_date/issue_size 等）：
        # 按品种广播（每品种取最后非空值），snapshot 时合并回截面长表
        self._static: pd.DataFrame = pd.DataFrame(index=self.symbols)
        static_cols = [c for c in table.columns if c not in ({"as_of", "symbol"} | set(PANEL_FIELDS))]
        if static_cols:
            self._static = (
                table[["symbol"] + static_cols]
                .dropna(how="all", subset=static_cols)
                .groupby("symbol")
                .last()
                .reindex(self.symbols)
                .rename_axis("symbol")
            )

    # ── 查询接口 ─────────────────────────────────

    def get_panel(self, field: str, start=None, end=None) -> pd.DataFrame:
        """取字段的 (date × symbol) 矩阵，可按日期区间裁剪"""
        if field not in self._panels:
            raise KeyError(f"面板无字段 {field}（可用: {sorted(self._panels)}）")
        frame = self._panels[field]
        if start is not None or end is not None:
            lo = pd.Timestamp(start) if start is not None else None
            hi = pd.Timestamp(end) if end is not None else None
            frame = frame.loc[[d for d in frame.index
                               if (lo is None or d >= lo) and (hi is None or d <= hi)]]
        return frame

    @property
    def price(self) -> pd.DataFrame:
        return self._panels["price"]

    @property
    def premium_rate(self) -> pd.DataFrame:
        return self._panels.get("premium_rate", pd.DataFrame(index=self.dates))

    @property
    def static(self) -> pd.DataFrame:
        """静态/低频字段表（index=symbol）：rating/list_date/maturity_date 等"""
        return self._static

    def snapshot(self, as_of) -> pd.DataFrame:
        """单日截面长表：{symbol: price/premium_rate/静态字段}"""
        ts = pd.Timestamp(as_of).normalize()
        parts = {}
        for field, frame in self._panels.items():
            if ts in frame.index:
                parts[field] = frame.loc[ts]
        if not parts:
            return pd.DataFrame(columns=["symbol"])
        out = pd.DataFrame(parts)
        out.index.name = "symbol"
        out = out.reset_index()
        if not self._static.empty:
            out = out.merge(self._static.reset_index(), on="symbol", how="left")
        return out

    # ── 研究计算辅助 ─────────────────────────────

    def forward_returns(
        self, periods: int = 20, field: str = "price", universe: Optional[Iterable[str]] = None
    ) -> pd.DataFrame:
        """前向收益矩阵：F[t, s] = P[t+periods, s] / P[t, s] - 1

        用于因子 IC 评估（因子值在 t，收益在 t→t+periods，无前视）。
        尾部 periods 行因无未来价而为 NaN。
        """
        price = self.get_panel(field)
        if universe is not None:
            cols = [c for c in price.columns if c in set(universe)]
            price = price[cols]
        fwd = price.shift(-periods) / price - 1.0
        return fwd
