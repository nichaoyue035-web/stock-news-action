"""
核心数学与运筹学引擎 (Math Engine)
包含：随机过程噪音过滤、胜率推演、凯利仓位最优化
"""
import numpy as np

class QuantMathEngine:
    def __init__(self, win_loss_ratio: float = 1.5, max_position_limit: float = 0.10):
        """
        初始化运筹学约束参数
        win_loss_ratio: 历史盈亏比（预期赔率），保守预设为 1.5
        max_position_limit: 单次开仓绝对上限。全局最多持仓 10 支，因此单只上限严格锁定为 10% (0.10)
        """
        self.b = win_loss_ratio
        self.max_limit = max_position_limit

    def calculate_expected_win_rate(self, features: dict, volatility: float) -> float:
        """
        [模块 1] 基于随机过程 (Stochastik) 的胜率推算
        将非结构化的 LLM 情绪特征，转化为严谨的上涨概率 P。
        """
        # 1. 提取标准化因子
        # sentiment_score: 0~1 (0.5为中性)
        # duration_impact: 1~5 天
        # sector_relevance: 0~1 波及度
        S = features.get("sentiment_score", 0.5)
        D = features.get("duration_impact", 1.0)
        R = features.get("sector_relevance", 0.5)

        # 2. 动能构建 (Momentum) 
        # 公式: M = (S - 0.5) * ln(1 + D) * R
        # 逐行解释: 
        # (S - 0.5) 将 0~1 映射到 -0.5~0.5，区分利空与利好方向。
        # ln(1 + D) 使用自然对数对发酵天数进行平滑，防止线性放大导致的过度拟合。
        # 乘以 R (波及度) 作为权重系数。
        momentum = (S - 0.5) * np.log1p(D) * R

        # 3. 风险惩罚项 (Risk Penalty)
        # 逐行解释: 真实市场价格是带有噪声的观测值。个股近期波动率(volatility)越大，
        # 说明该信号的信噪比越低。设置 1.2 倍的波动率作为置信度惩罚。
        risk_penalty = 1.2 * volatility

        # 4. 胜率映射 (Sigmoid 变形)
        # 逐行解释: 将无限区间的动能减去惩罚后，强行映射到 0~1 的概率区间。
        # 基础胜率设为 0.5 (随机游走)，再加上有效动能带来的概率偏移。
        raw_p = 0.5 + momentum - risk_penalty
        
        # 严格裁剪到 0~1 之间
        p = max(0.0, min(1.0, raw_p))
        return p

    def optimize_position(self, p: float) -> float:
        """
        [模块 2] 运筹学仓位最优化 (Operations Research)
        使用凯利公式 (Kelly Criterion) 求解约束条件下的最优解
        """
        if p <= 0.0:
            return 0.0
            
        q = 1.0 - p  # 失败概率
        
        # 1. 原始凯利公式 f* = (bp - q) / b
        # 逐行解释: b 是赔率，p 是胜率。当期望收益 (bp - q) 为正时，才会输出正仓位。
        f_star = (self.b * p - q) / self.b
        
        # 如果期望收益为负，绝对不开仓
        if f_star <= 0:
            return 0.0
            
        # 2. 引入半凯利 (Half-Kelly) 降波
        # 逐行解释: 真实交易中极少使用满凯利，因为市场并非完美的抛硬币游戏。
        # 乘以 0.5 能够在牺牲约 25% 复合增速的情况下，将最大回撤(Drawdown)降低 50%。
        adjusted_f = f_star * 0.5
        
        # 3. 硬性边界约束
        # 逐行解释: 取计算仓位与系统风控上限的极小值，强制切断尾部风险。
        final_position = min(adjusted_f, self.max_limit)
        
        return final_position
