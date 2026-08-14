"""Parquet 缓存

设计要点：
1. 按 exchange/adjust/symbol 分区存储，避免单目录文件过多
2. 增量更新：已有则合并去重，避免重复下载
3. zstd 压缩：体积小、读取快
4. 缓存查询：按 date 范围切片返回
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd


class ParquetCache:
    """按 symbol + adjust 分区存储，支持增量更新"""

    def __init__(
        self,
        root: str | Path,
        compression: str = "zstd",
        logger: Optional[logging.Logger] = None,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.compression = compression
        self.logger = logger or logging.getLogger("parquet_cache")

    def _path(self, symbol: str, adjust: str, kind: str = "bars") -> Path:
        """缓存文件路径：root/<kind>/<exchange>/<adjust>/<symbol>.parquet"""
        # exchange 从 symbol 后缀提取
        if "." in symbol:
            exchange = symbol.split(".", 1)[-1].lower()
        else:
            # 纯数字代码兜底
            exchange = "unknown"
        return self.root / kind / exchange / adjust / f"{symbol}.parquet"

    def get_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "hfq",
    ) -> Optional[pd.DataFrame]:
        """读取缓存的日线数据，按 [start, end] 切片返回

        Returns:
            DataFrame 或 None（缓存不存在）
        """
        p = self._path(symbol, adjust, "bars")
        if not p.exists():
            return None
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            self.logger.warning(f"读取缓存失败 {p}: {e}")
            return None

        if len(df) == 0:
            return df

        df["date"] = pd.to_datetime(df["date"])
        start_dt = pd.Timestamp(start)
        end_dt = pd.Timestamp(end)
        sliced = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        return sliced.reset_index(drop=True) if len(sliced) > 0 else sliced

    def put_bars(
        self,
        symbol: str,
        df: pd.DataFrame,
        adjust: str = "hfq",
    ) -> None:
        """写入日线数据，与已有缓存增量合并去重"""
        if df is None or len(df) == 0:
            return

        p = self._path(symbol, adjust, "bars")
        p.parent.mkdir(parents=True, exist_ok=True)

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])

        # 与已有合并去重
        if p.exists():
            try:
                old = pd.read_parquet(p)
                old["date"] = pd.to_datetime(old["date"])
                df = pd.concat([old, df], ignore_index=True)
                df = df.drop_duplicates(subset=["date"], keep="last")
                df = df.sort_values("date").reset_index(drop=True)
            except Exception as e:
                self.logger.warning(f"合并缓存失败 {p}，覆盖写入: {e}")

        df.to_parquet(p, compression=self.compression, index=False)
        self.logger.debug(f"缓存写入 {symbol}: {len(df)} 行 -> {p}")

    def get_stock_basic(self) -> Optional[pd.DataFrame]:
        """读取股票基础信息缓存"""
        p = self.root / "stock_basic.parquet"
        if not p.exists():
            return None
        try:
            return pd.read_parquet(p)
        except Exception as e:
            self.logger.warning(f"读取 stock_basic 缓存失败: {e}")
            return None

    def put_stock_basic(self, df: pd.DataFrame) -> None:
        """写入股票基础信息（全量覆盖）"""
        if df is None or len(df) == 0:
            return
        p = self.root / "stock_basic.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p, compression=self.compression, index=False)
        self.logger.debug(f"stock_basic 缓存写入 {len(df)} 行")

    def get_trade_calendar(self, year: int) -> Optional[list[str]]:
        """读取交易日历缓存（按年存储）"""
        p = self.root / "trade_calendar" / f"{year}.parquet"
        if not p.exists():
            return None
        try:
            df = pd.read_parquet(p)
            return sorted(df["date"].astype(str).tolist())
        except Exception as e:
            self.logger.warning(f"读取交易日历缓存失败: {e}")
            return None

    def put_trade_calendar(self, dates: list[str], year: int) -> None:
        """写入交易日历（按年）"""
        if not dates:
            return
        p = self.root / "trade_calendar" / f"{year}.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({"date": dates})
        df.to_parquet(p, compression=self.compression, index=False)

    def list_cached_symbols(self, exchange: Optional[str] = None) -> list[str]:
        """列出已缓存的标的代码"""
        base = self.root / "bars"
        if not base.exists():
            return []

        symbols = []
        for ex_dir in base.iterdir():
            if exchange and ex_dir.name != exchange.lower():
                continue
            for adj_dir in ex_dir.iterdir():
                for f in adj_dir.glob("*.parquet"):
                    symbols.append(f.stem)
        return sorted(set(symbols))

    def clear(self, symbol: Optional[str] = None) -> None:
        """清空缓存（symbol 为 None 时清空全部）"""
        if symbol is None:
            import shutil
            if self.root.exists():
                shutil.rmtree(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            self.logger.info("缓存全部清空")
        else:
            # 清空指定 symbol 的所有 adjust 变体
            for ex_dir in (self.root / "bars").iterdir() if (self.root / "bars").exists() else []:
                for adj_dir in ex_dir.iterdir():
                    p = adj_dir / f"{symbol}.parquet"
                    if p.exists():
                        p.unlink()
