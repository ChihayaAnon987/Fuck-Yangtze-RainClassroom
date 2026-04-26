#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于缓存问题的真实AI测试脚本
使用problem_answer_cache.json中的真实问题进行测试
"""

import sys
import os
import json

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from util.ai import init_ai_strategy, request_ai
from config import question_type


def load_cached_problems():
    """加载缓存的问题"""
    cache_file = "problem_answer_cache.json"
    if not os.path.exists(cache_file):
        print(f"缓存文件 {cache_file} 不存在")
        return []
    
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
        
        problems = []
        for key, value in cache_data.items():
            problem_info = value["question"]
            expected_answer = value["answer"]
            problems.append({
                "problem_id": problem_info["problem_id"],
                "problem_type": problem_info["problem_type"],
                "problem_content": problem_info["problem_content"],
                "options": problem_info["options"],
                "img_url": problem_info["img_url"],
                "expected_answer": expected_answer
            })
        return problems
    except Exception as e:
        print(f"加载缓存问题失败: {e}")
        return []


def test_problem(problem_data):
    """测试单个问题"""
    problem_type_str = question_type.get(problem_data["problem_type"], "未知题型")
    problem_content = problem_data["problem_content"]
    options = problem_data["options"]
    expected_answer = problem_data["expected_answer"]
    img_url = problem_data["img_url"]
    
    print("=" * 80)
    print(f"[CACHED TEST] 题型: {problem_type_str}")
    print(f"问题: {problem_content[:100]}{'...' if len(problem_content) > 100 else ''}")
    print(f"选项: {[opt['value'] for opt in options] if options else '无'}")
    print(f"期望答案: {expected_answer}")
    print("-" * 80)
    
    try:
        answer = request_ai(
            type=problem_type_str,
            problem=problem_content,
            options=options,
            img_url=img_url
        )
        print(f"AI答案: {answer}")
        
        # 比较答案是否匹配
        if set(answer) == set(expected_answer):
            print("✅ 答案匹配!")
        else:
            print(f"❌ 答案不匹配! 期望: {expected_answer}, 实际: {answer}")
            
        return answer == expected_answer
        
    except Exception as e:
        print(f"测试失败: {e}")
        return False


def main():
    """主函数"""
    # 初始化AI策略
    init_ai_strategy(timeout=30)
    
    # 加载缓存问题
    problems = load_cached_problems()
    if not problems:
        print("没有找到缓存问题，退出测试")
        return
    
    print(f"找到 {len(problems)} 个缓存问题")
    
    # 选择几个代表性问题进行测试
    selected_problems = []
    
    # 找一个编译原理相关的单选题
    for p in problems:
        if p["problem_type"] == 1 and "预测分析" in p["problem_content"]:
            selected_problems.append(p)
            break
    
    # 找一个文法分析的单选题  
    for p in problems:
        if p["problem_type"] == 1 and "四元式" in p["problem_content"]:
            selected_problems.append(p)
            break
    
    # 找一个填空题
    for p in problems:
        if p["problem_type"] == 3 and "基于直觉" in p["problem_content"]:
            selected_problems.append(p)
            break
    
    if not selected_problems:
        # 如果没找到特定问题，就测试前3个
        selected_problems = problems[:3]
    
    # 运行测试
    correct_count = 0
    total_count = len(selected_problems)
    
    for problem in selected_problems:
        if test_problem(problem):
            correct_count += 1
        print()
    
    print("=" * 80)
    print(f"测试总结: {correct_count}/{total_count} 正确")
    print("=" * 80)


if __name__ == "__main__":
    main()