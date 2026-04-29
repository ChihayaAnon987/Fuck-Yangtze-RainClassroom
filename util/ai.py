#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI解题模块（改用策略层）
重构为使用AIStrategy，支持多模型并行调用
"""

import logging
from typing import List
from util.ai_strategy import AIStrategy

logger = logging.getLogger(__name__)

# 全局策略实例
_strategy = None


def init_ai_strategy(timeout: int = 30):
    """
    初始化AI策略引擎
    
    Args:
        timeout: 请求超时时间（秒）
    """
    global _strategy
    _strategy = AIStrategy(timeout=timeout)
    print(f"[AI] 策略引擎已初始化，支持模型: {_strategy.available_models}")


def request_ai(type, problem, options, img_url) -> List[str]:
    """
    请求AI解题
    
    这是兼容现有代码的接口，内部使用策略层
    
    Args:
        type: 题目类型
        problem: 题干文本
        options: 选项列表
        img_url: 题目图片URL
        
    Returns:
        答案列表，如 ["A"] 或 ["填空答案"]
    """
    if _strategy is None:
        logger.error("[AI] AI策略未初始化")
        return []
        
    try:
        answer = _strategy.solve(type, problem, options, img_url)
        return answer
    except Exception as e:
        logger.error(f"[AI] 解题失败: {e}")
        return []