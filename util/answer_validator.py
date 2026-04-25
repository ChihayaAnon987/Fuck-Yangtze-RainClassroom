"""
答题校验模块 - 保证答案与题型的一致性

实现三层答题质量保障：
1. 校验答案与题型是否匹配
2. 判断是否需要刷新缓存
3. 智能选择多个答案源时的最优答案
"""

import logging
from typing import List, Tuple, Optional, Dict

logger = logging.getLogger(__name__)


def validate_answer_for_problem_type(
    answer,
    problem_type: int,
    options: Optional[list] = None
) -> Tuple[bool, List[str], str]:
    """
    答题前的校验逻辑 - 确保答案与题型匹配
    
    Args:
        answer: AI或搜题返回的答案 (list 或 str)
        problem_type: 题目类型 (1=单选, 2=多选, 3=填空)
        options: 选项列表 (对选择题有效)
    
    Returns:
        (is_valid, normalized_answer, validation_reason)
        - is_valid: 是否通过校验
        - normalized_answer: 规范化后的答案列表
        - validation_reason: 校验原因说明
    """
    # 规范化答案为列表
    answer_list = answer if isinstance(answer, list) else [answer]
    # 过滤空值
    answer_list = [a for a in answer_list if a and str(a).strip()]
    
    logger.info(f"[VALIDATOR] 校验答案: type={problem_type}, answer={answer_list}")
    
    # 单选题校验
    if problem_type == 1:
        if len(answer_list) == 0:
            return (False, [], "SINGLE_CHOICE_EMPTY: 单选题缺少答案")
        
        if len(answer_list) > 1:
            # 单选题不应该有多个答案，取第一个（最可信的来源）
            normalized = [answer_list[0]]
            reason = f"SINGLE_CHOICE_REDUCED: 从多答中选择第一个 [{','.join(answer_list)}] → [{answer_list[0]}]"
            logger.warning(f"[VALIDATOR] {reason}")
            return (True, normalized, reason)
        
        # 单个答案的单选题
        return (True, answer_list, "SINGLE_CHOICE_OK")
    
    # 多选题校验
    elif problem_type == 2:
        if len(answer_list) == 0:
            return (False, [], "MULTIPLE_CHOICE_EMPTY: 多选题缺少答案")
        
        if len(answer_list) == 1:
            # 多选题应该有多个答案，但只有一个也可接受
            return (True, answer_list, "MULTIPLE_CHOICE_SINGLE_ANSWER")
        
        if len(answer_list) <= 10:  # 通常选项不超过10个，多选不超过6个
            return (True, answer_list, "MULTIPLE_CHOICE_OK")
        
        # 多答超过10个，可能是搜题结果混乱
        return (False, answer_list, "MULTIPLE_CHOICE_SUSPICIOUS: 答案过多(>10)")
    
    # 填空题校验
    elif problem_type == 3:
        if len(answer_list) > 0:
            return (True, answer_list, "FILL_BLANK_OK")
        return (False, [], "FILL_BLANK_EMPTY: 填空题缺少答案")
    
    # 未知类型
    return (False, answer_list, f"UNKNOWN_TYPE: {problem_type}")


def should_refresh_cache(
    validation_result: Tuple[bool, List[str], str],
    cache_age_seconds: Optional[int] = None
) -> bool:
    """
    判断是否应该跳过缓存，重新调用AI/搜题
    
    Args:
        validation_result: validate_answer_for_problem_type 返回的结果
        cache_age_seconds: 缓存年龄（如果超过1小时则倾向刷新）
    
    Returns:
        bool - True表示应该重新调用AI/搜题，False表示使用缓存
    """
    is_valid, _, reason = validation_result
    
    # 失败的校验一定要刷新
    if not is_valid:
        logger.info(f"[VALIDATOR] 校验失败，需要刷新缓存: {reason}")
        return True
    
    # 可疑状态（如单选题多答被强制规范化）需要考虑刷新
    if "REDUCED" in reason:
        # 标记为需要刷新（但可通过缓存年龄判断）
        if cache_age_seconds and cache_age_seconds > 3600:  # 超过1小时
            logger.warning(f"[VALIDATOR] 可疑答案且缓存已过期(>1h)，建议刷新: {reason}")
            return True
        logger.info(f"[VALIDATOR] 可疑答案但缓存仍新鲜，继续使用: {reason}")
        return False
    
    if "SUSPICIOUS" in reason:
        logger.warning(f"[VALIDATOR] 可疑答案，建议刷新: {reason}")
        return True
    
    # 正常情况不需要刷新
    logger.debug(f"[VALIDATOR] 校验通过，使用缓存: {reason}")
    return False


def select_best_answer_from_multiple_sources(
    cached_answer: List[str],
    new_answer: List[str],
    problem_type: int,
    source_priority: Dict[str, float] = None
) -> Tuple[List[str], str]:
    """
    当缓存答案与新搜题答案不一致时，智能选择最匹配的
    
    竞争策略：
    - 相同 → 保持缓存
    - 单选题 → 都是单答则都可接受，都是多答则拒绝新答案
    - 多选题 → 计算重合度，超过70%则取交集，否则取信息更多的
    
    Args:
        cached_answer: 缓存的答案列表
        new_answer: 新搜到的答案列表
        problem_type: 题目类型
        source_priority: 源优先级（如 {"ai": 0.9, "search": 0.7}）
    
    Returns:
        (selected_answer, selection_reason)
    """
    cached_set = set(str(x).upper() for x in cached_answer)
    new_set = set(str(x).upper() for x in new_answer)
    
    logger.info(f"[SELECTOR] 比较答案: 缓存={cached_answer} vs 新搜={new_answer}")
    
    # 情况1: 答案完全相同
    if cached_set == new_set:
        logger.info(f"[SELECTOR] 答案一致，保持缓存")
        return (cached_answer, "ANSWER_CONSISTENT")
    
    # 情况2: 单选题处理
    if problem_type == 1:
        if len(cached_answer) == 1 and len(new_answer) == 1:
            # 都是单答但不同 - 保持缓存（优先稳定性）
            logger.info(f"[SELECTOR] 单选题两个不同答案，保持缓存保证稳定")
            return (cached_answer, "SINGLE_CHOICE_DIFFER_USE_CACHE")
        elif len(new_answer) == 1 and len(cached_answer) > 1:
            # 缓存是多答，新搜是单答 - 用新答案（更规范）
            logger.info(f"[SELECTOR] 缓存多答但新搜单答，采用新答案")
            return (new_answer, "SINGLE_CHOICE_NEW_MORE_VALID")
        elif len(cached_answer) == 1 and len(new_answer) > 1:
            # 缓存单答，新搜多答 - 保持缓存
            logger.info(f"[SELECTOR] 缓存单答，新搜多答，保持缓存")
            return (cached_answer, "SINGLE_CHOICE_CACHE_VALID")
        # 都是多答（都不正常）- 保持缓存
        return (cached_answer, "SINGLE_CHOICE_BOTH_ABNORMAL")
    
    # 情况3: 多选题处理
    elif problem_type == 2:
        if len(cached_set) == 0 or len(new_set) == 0:
            return (cached_answer, "MULTIPLE_CHOICE_FALLBACK_CACHE")
        
        # 计算重合度
        intersection = cached_set & new_set
        union = cached_set | new_set
        overlap_ratio = len(intersection) / len(union) if union else 0
        
        logger.info(f"[SELECTOR] 多选题重合度: {overlap_ratio:.1%} ({len(intersection)}/{len(union)})")
        
        if overlap_ratio >= 0.7:  # 超过70%重合
            # 取交集（双方都同意的）
            result = sorted(list(intersection))
            logger.info(f"[SELECTOR] 高重合度，取交集: {result}")
            return (result, f"MULTIPLE_CHOICE_CONSENSUS_{overlap_ratio:.0%}")
        
        elif overlap_ratio >= 0.5:  # 50-70%重合
            # 中等重合，保持缓存
            logger.info(f"[SELECTOR] 中等重合度，保持缓存")
            return (cached_answer, "MULTIPLE_CHOICE_MEDIUM_OVERLAP")
        
        else:  # 低于50%重合
            # 低重合度，选信息更多的
            if len(new_answer) > len(cached_answer):
                logger.info(f"[SELECTOR] 低重合度，新答案更多，采用新答案")
                return (new_answer, f"MULTIPLE_CHOICE_NEW_MORE_INFO")
            else:
                logger.info(f"[SELECTOR] 低重合度，保持缓存")
                return (cached_answer, "MULTIPLE_CHOICE_LOW_OVERLAP_KEEP_CACHE")
    
    # 其他情况：保持缓存（求稳定）
    logger.warning(f"[SELECTOR] 默认情况，保持缓存")
    return (cached_answer, "DEFAULT_KEEP_CACHE")
