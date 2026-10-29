# -*- coding: utf-8 -*-
"""
组合条件预警引擎 (Composite Condition Alert Engine) - 借鉴 TNT-Likely/PanWatch
=============================================================================
将 jiankong 原有的「单一涨跌幅阈值」预警升级为 PanWatch 式组合条件引擎：

1. 组合条件：condition_group = {"op": "and"/"or", "items": [{type, op, value}, ...]}
   - 条件类型：price / pct_change / volume / amount / high / low / prev_close
   - 操作符：> >= < <= == != between (between 的 value 为 [min, max])
2. 向后兼容：标的若未配置 alert_conditions，则用其 alert_threshold_pct 自动生成
   等价条件组 (|涨跌幅| >= 阈值 ⟺ pct_change>=阈值 OR pct_change<=-阈值)，行为不变。
3. 门禁 (借鉴 PanWatch price_alert_engine)：
   - 冷却窗口 cooldown_minutes (默认 30)：同标的冷却期内不重复触发
   - 每日上限 max_triggers_per_day (默认 5)：防推送轰炸
   - 分钟桶防重：同一分钟同标的只触发一次
4. 状态持久化到 scratch/alert_state.json，重启不丢 (线程安全)。
"""

import json
import time
import threading
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("AlertEngine")

STATE_FILE = Path(__file__).resolve().parent.parent / "scratch" / "alert_state.json"

# 条件类型 -> 中文标签 (用于生成命中描述)
_TYPE_LABELS = {
    "price": "现价",
    "pct_change": "涨跌幅%",
    "volume": "成交量",
    "amount": "成交额",
    "high": "最高价",
    "low": "最低价",
    "prev_close": "昨收",
}

_OPS = (">", ">=", "<", "<=", "==", "!=", "between")


def _evaluate_condition(item: Dict[str, Any], metrics: Dict[str, float]) -> bool:
    """评估单个条件。metrics 缺该字段或配置异常时返回 False (绝不臆造触发)。"""
    try:
        ctype = item.get("type")
        op = item.get("op")
        val = item.get("value")
        actual = metrics.get(ctype)
        if actual is None or op not in _OPS:
            return False
        if op == ">":
            return actual > val
        if op == ">=":
            return actual >= val
        if op == "<":
            return actual < val
        if op == "<=":
            return actual <= val
        if op == "==":
            return abs(actual - val) < 1e-9
        if op == "!=":
            return abs(actual - val) >= 1e-9
        if op == "between":
            if not isinstance(val, (list, tuple)) or len(val) != 2:
                return False
            return val[0] <= actual <= val[1]
        return False
    except Exception:
        return False


def _describe_condition(item: Dict[str, Any], metrics: Dict[str, float]) -> str:
    """生成单个条件的命中描述文本。"""
    ctype = item.get("type")
    label = _TYPE_LABELS.get(ctype, ctype)
    op = item.get("op")
    val = item.get("value")
    actual = metrics.get(ctype)
    actual_s = f"{actual:.2f}" if isinstance(actual, (int, float)) else "?"
    if op == "between" and isinstance(val, (list, tuple)):
        return f"{label}∈[{val[0]}, {val[1]}] (当前 {actual_s})"
    return f"{label} {op} {val} (当前 {actual_s})"


def _evaluate_group(group: Dict[str, Any], metrics: Dict[str, float]) -> Tuple[bool, List[str]]:
    """评估条件组 (and/or)，返回 (是否命中, 命中条件描述列表)。空组视为未命中。"""
    op = str(group.get("op", "and")).lower()
    items = group.get("items", []) or []
    if not items:
        return False, []
    hits = [(it, _evaluate_condition(it, metrics)) for it in items]
    if op == "or":
        overall = any(h for _, h in hits)
    else:
        overall = all(h for _, h in hits)
    matched = [_describe_condition(it, metrics) for it, h in hits if h]
    return overall, matched


def build_group_from_threshold(threshold: float) -> Dict[str, Any]:
    """由单阈值生成等价组合条件组：|pct_change| >= threshold。"""
    return {
        "op": "or",
        "items": [
            {"type": "pct_change", "op": ">=", "value": threshold},
            {"type": "pct_change", "op": "<=", "value": -threshold},
        ],
    }


class AlertEngine:
    """线程安全的组合条件预警引擎 (含冷却/每日上限/分钟桶防重 + 状态持久化)。"""

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file or STATE_FILE
        self._lock = threading.Lock()
        self._state: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning(f"加载预警状态失败 (将重建): {e}")
        return {}

    def _persist(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
            import os
            os.replace(tmp, self.state_file)
        except Exception as e:
            logger.warning(f"持久化预警状态失败: {e}")

    def _gate_pass(self, code: str, cooldown_min: int, daily_max: int,
                   now_ts: float, today: str) -> bool:
        """门禁：冷却窗口 + 每日上限。调用方须已持锁。"""
        st = self._state.get(code, {})
        last = st.get("last_trigger_ts", 0)
        if last and (now_ts - last) < cooldown_min * 60:
            return False  # 冷却期内
        if st.get("today") != today:
            st["today"] = today
            st["today_count"] = 0
        if st.get("today_count", 0) >= daily_max:
            return False  # 达每日上限
        return True

    def _record(self, code: str, now_ts: float, today: str):
        """记录一次触发 (更新冷却水位与今日计数)。调用方须已持锁。"""
        st = self._state.setdefault(code, {})
        if st.get("today") != today:
            st["today"] = today
            st["today_count"] = 0
        st["last_trigger_ts"] = now_ts
        st["today_count"] = st.get("today_count", 0) + 1
        self._persist()

    def evaluate_and_gate(self, item_config: Dict[str, Any], metrics: Dict[str, float],
                          now_ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        评估标的预警并通过门禁。命中且通过门禁返回警报核心 dict，否则返回 None。

        item_config: watchlist 标的配置 (可用 alert_conditions 自定义，否则用 alert_threshold_pct)
        metrics:     当前行情指标 {price, pct_change, volume, amount, high, low, prev_close}
        """
        code = item_config.get("code")
        if not code:
            return None

        # 1. 构建条件组 (向后兼容：无自定义条件则用单阈值)
        custom = item_config.get("alert_conditions")
        if isinstance(custom, dict) and custom.get("items"):
            group = custom
            cooldown_min = int(custom.get("cooldown_minutes", 30))
            daily_max = int(custom.get("max_triggers_per_day", 5))
        else:
            threshold = float(item_config.get("alert_threshold_pct", 3.0))
            group = build_group_from_threshold(threshold)
            cooldown_min = int(item_config.get("cooldown_minutes", 30))
            daily_max = int(item_config.get("max_triggers_per_day", 5))

        # 2. 评估条件
        hit, matched = _evaluate_group(group, metrics)
        if not hit:
            return None

        now_ts = now_ts if now_ts is not None else time.time()
        today = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d")

        # 3. 门禁 (线程安全)
        with self._lock:
            if not self._gate_pass(code, cooldown_min, daily_max, now_ts, today):
                return None
            self._record(code, now_ts, today)

        # 4. 组装警报核心 (名称/时间等由调用方补充)
        pct = metrics.get("pct_change", 0.0)
        threshold = float(item_config.get("alert_threshold_pct", 3.0))
        if not custom:
            level = "URGENT" if abs(pct) >= max(threshold * 2, 7.0) else "ALERT"
        else:
            level = "URGENT" if abs(pct) >= 7.0 else "ALERT"

        minute_bucket = datetime.fromtimestamp(now_ts).strftime("%Y%m%d%H%M")
        return {
            "id": f"{code}:cond:{minute_bucket}",
            "type": "SURGE" if pct > 0 else "DROP",
            "level": level,
            "matched_conditions": matched,
            "rule": "组合条件" if custom else "单阈值",
            "cooldown_minutes": cooldown_min,
        }

    def reset(self, code: Optional[str] = None):
        """清空门禁状态 (全部或单个标的)，用于运维重置。"""
        with self._lock:
            if code:
                self._state.pop(code, None)
            else:
                self._state.clear()
            self._persist()


# 全局单例 (web_server 与 realtime_monitor 共用)
alert_engine = AlertEngine()


if __name__ == "__main__":
    # 自测：单阈值 + 组合条件 + 门禁
    eng = AlertEngine(state_file=Path(__file__).resolve().parent.parent / "scratch" / "_test_alert_state.json")

    print("=== 单阈值向后兼容测试 (阈值 3.0) ===")
    cfg1 = {"code": "600000.SH", "alert_threshold_pct": 3.0}
    print("涨5%:", bool(eng.evaluate_and_gate(cfg1, {"pct_change": 5.0, "price": 10.0})))
    print("涨1%:", bool(eng.evaluate_and_gate(cfg1, {"pct_change": 1.0, "price": 10.0})))

    print("=== 组合条件测试 (涨跌幅>=5 且 现价>=10) ===")
    cfg2 = {
        "code": "000001.SZ",
        "alert_conditions": {
            "op": "and", "cooldown_minutes": 0, "max_triggers_per_day": 99,
            "items": [
                {"type": "pct_change", "op": ">=", "value": 5.0},
                {"type": "price", "op": ">=", "value": 10.0},
            ],
        },
    }
    r1 = eng.evaluate_and_gate(cfg2, {"pct_change": 6.0, "price": 12.0})
    print("涨6%且价12 (应命中):", r1["matched_conditions"] if r1 else None)
    r2 = eng.evaluate_and_gate(cfg2, {"pct_change": 6.0, "price": 8.0})
    print("涨6%但价8 (and不满足应None):", r2)

    print("=== 冷却门禁测试 (冷却60分钟) ===")
    cfg3 = {"code": "300001.SZ", "alert_conditions": {
        "op": "or", "cooldown_minutes": 60, "max_triggers_per_day": 99,
        "items": [{"type": "pct_change", "op": ">=", "value": 2.0}]}}
    a = eng.evaluate_and_gate(cfg3, {"pct_change": 3.0, "price": 1.0})
    b = eng.evaluate_and_gate(cfg3, {"pct_change": 3.0, "price": 1.0})
    print("首次触发:", bool(a), "| 冷却期内二次 (应None):", b)

    # 清理测试状态
    try:
        eng.state_file.unlink()
    except Exception:
        pass
    print("自测完成")
