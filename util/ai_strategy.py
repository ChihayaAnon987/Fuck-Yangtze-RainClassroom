#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 解题策略层

核心流程：OCR 识别 → 题库搜索 → AI 作答
实现“搜到即返，搜不到才 AI”的优化逻辑。
"""

import ast
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from config import enable_question_bank
from util.ai_provider import MultiModelAIProvider
from util.enncy import search
from util.ocr import ocr_form_url_image

logger = logging.getLogger(__name__)


class AIStrategy:
    """
    AI 解题策略
    管理 OCR、搜题、AI 三个阶段
    """

    def __init__(self, timeout: int = 30):
        """
        初始化策略

        Args:
            timeout: 超时时间（秒）
        """
        self.timeout = timeout
        self.provider = MultiModelAIProvider(timeout=timeout)
        self.available_models = self.provider.get_available_models()

    def _extract_problem_text(self, problem: str, img_url: str) -> tuple:
        """
        第一阶段：文本提取

        如果题干文本为空，使用 OCR 识别图片。

        Args:
            problem: 题干文本（可能为空）
            img_url: 题目图片 URL

        Returns:
            (problem_text, is_ocr_used)
        """
        if problem.strip():
            return problem.strip(), False

        if not img_url:
            logger.warning("[STRATEGY] 题干和图片都为空")
            return "", False

        try:
            ocr_result = ocr_form_url_image(img_url)
            print(f"[STRATEGY] OCR 结果: {ocr_result}")
            return ocr_result, True
        except Exception as e:
            logger.error(f"[STRATEGY] OCR 失败: {e}")
            return "", False

    def _search_from_question_bank(self, problem_text: str) -> Optional[List[str]]:
        """
        第二阶段：题库搜索

        如果启用题库且题干非空，则尝试从题库获取答案。

        Args:
            problem_text: 题干文本

        Returns:
            答案列表，如果未找到则返回 None
        """
        if not enable_question_bank:
            return None

        if not problem_text.strip():
            logger.warning("[STRATEGY] 题干为空，跳过题库搜索")
            return None

        try:
            print("[STRATEGY] 开始题库搜索...")
            result = search(problem_text)
            print(f"[STRATEGY] 题库返回结果: {result}")

            if result and "data" in result and "answer" in result["data"]:
                answer_text = result["data"]["answer"]
                if "没搜到该题的答案" not in answer_text:
                    # 尝试解析答案
                    extracted = self._extract_answer_from_search(answer_text)
                    if extracted:
                        print(f"[STRATEGY] 题库搜到答案: {extracted}")
                        return extracted

            print("[STRATEGY] 题库未搜到该题")
            return None
        except Exception as e:
            logger.error(f"[STRATEGY] 题库搜索异常: {e}")
            return None

    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return (
            "你是一个专业的课堂答题助手。请严格按照要求格式回答问题。\n"
            "1. 单选题：只返回一个选项字母，如 ['B']\n"
            "2. 多选题：返回所有正确选项的字母列表，如 ['A', 'B', 'D']\n"
            "3. 投票题：返回你认为最合适的一个或多个选项字母，如 ['A'] 或 ['A', 'C']\n"
            "4. 填空题：返回填空的答案列表，如 ['10'] 或 ['北京', '上海']\n"
            "5. 主观题：返回你的观点或答案，如 ['我认为应该选择A方案']\n"
            "6. 所有答案必须是JSON数组格式，只包含字母、文字或数字，不要包含任何解释。\n"
            "7. 尽可能给出最合理的答案，即使不确定也要基于题目内容推测。\n"
            "8. 不要返回空数组 []，除非题目确实没有任何可回答的内容。\n"
            "9. 对于主观选择题（如'基于直觉选择'），请选择最符合常理的选项。\n"
            "10. 不要添加任何额外的文字、标点符号或解释。"
        )

    def _get_user_prompt(self, problem_type: str, problem_text: str, options: List[str]) -> str:
        """根据题目类型生成用户提示词"""
        base_info = f"题目：{problem_text}"
        options_info = f"选项：{options}" if options else ""

        if problem_type == "单选题":
            return f"题目类型：单选题\n{base_info}\n{options_info}\n请只返回最合适的答案。"
        
        if problem_type == "多选题":
            return f"题目类型：多选题\n{base_info}\n{options_info}\n请返回所有正确选项。"
        
        if problem_type == "投票题":
            return f"题目类型：投票题\n{base_info}\n{options_info}\n请选择你认为最合适的选项。"
        
        if problem_type == "主观题":
            if options:
                return f"题目类型：主观题（有选项）\n{base_info}\n可选答案：{options}\n请选择或表达你的观点。"
            return f"题目类型：主观题\n{base_info}\n请表达你的观点或答案。"
        
        # 填空题
        if options:
            return f"题目类型：填空题（主观选择）\n{base_info}\n可选答案：{options}\n请选择最合适的选项字母。"
        return f"题目类型：填空题\n{base_info}\n只填写最终答案。"

    def _build_messages(self, problem_type: str, problem_text: str, options: List[str]) -> List[Dict[str, str]]:
        """构建AI消息"""
        return [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": self._get_user_prompt(problem_type, problem_text, options)}
        ]

    def _parse_ai_response(self, response: str) -> List[str]:
        """解析AI响应"""
        if not response:
            return []

        # 首先尝试直接评估为Python字面量（处理 ['A', 'B'] 这样的格式）
        try:
            import ast
            parsed = ast.literal_eval(response)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if item and str(item).strip()]
        except (ValueError, SyntaxError):
            pass

        # 尝试直接解析JSON
        try:
            parsed = json.loads(response)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if item]
        except json.JSONDecodeError:
            pass

        # 尝试提取JSON块
        json_block = self._extract_json_block(response)
        if json_block:
            try:
                parsed = json.loads(json_block)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if item]
            except json.JSONDecodeError:
                pass

        # 尝试直接提取
        logger.debug(f"[STRATEGY] 无法解析响应，尝试直接提取: {response[:100]}...")
        return self._extract_answer_directly(response)

    def _extract_json_block(self, text: str) -> str:
        """从文本中提取JSON块"""
        # 查找 ```json ... ``` 格式
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            return json_match.group(1).strip()

        # 查找 ``` ... ``` 格式
        code_match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()

        # 查找花括号包围的JSON
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            return brace_match.group(0)

        # 查找方括号包围的数组
        array_match = re.search(r"\[.*\]", text, re.DOTALL)
        if array_match:
            return array_match.group(0)

        return ""

    def _extract_answer_directly(self, text: str) -> List[str]:
        """直接从文本中提取答案"""
        text = text.strip()
        if not text:
            return []
        
        # 1. 单个字母
        if len(text) == 1 and text.isalpha():
            return [text.upper()]
            
        # 2. 方括号内容 [A, B]
        if "[" in text and "]" in text:
            content = text[text.find("[")+1:text.rfind("]")]
            items = [item.strip().strip("'\"") for item in content.split(",")]
            cleaned_items = [item for item in items if item]
            if cleaned_items:
                return cleaned_items
            
        # 3. 逗号分隔的字母 A, B, C
        if "," in text:
            parts = [p.strip() for p in text.split(",")]
            if all(p.isalpha() for p in parts if p):
                return [p.upper() for p in parts if p]
            
        return []

    def _extract_answer_from_search(self, search_result: str) -> Optional[List[str]]:
        """从题库搜索结果中提取答案"""
        # 这里可以根据题库返回的具体格式进行解析
        # 目前简单处理，返回None让AI处理
        return None

    def solve(self, problem_type: str, problem: str, options: List[str], img_url: str = "") -> List[str]:
        """
        主解题流程

        Args:
            problem_type: 题目类型（单选题/多选题/填空题）
            problem: 题干文本
            options: 选项列表（填空题为空列表）
            img_url: 题目图片URL（可选）

        Returns:
            答案列表
        """
        print(f"[STRATEGY] 开始解题流程: type={problem_type}")

        # 第一阶段：文本提取
        problem_text, is_ocr_used = self._extract_problem_text(problem, img_url)
        if not problem_text:
            logger.error("[STRATEGY] 无法获取题干文本")
            return []

        # 第二阶段：题库搜索
        bank_answer = self._search_from_question_bank(problem_text)
        if bank_answer is not None:
            return bank_answer

        # 第三阶段：AI作答
        messages = self._build_messages(problem_type, problem_text, options)
        print(f"[STRATEGY] 调用 AI 解题 (models={self.available_models})...")

        try:
            # 并行调用所有模型，选择最佳答案
            best_answer_str, best_confidence, best_model = self.provider.chat_completion_parallel(
                messages=messages,
                response_format={"type": "json_object"},
                timeout=self.timeout  # 传递超时参数
            )
            
            print(f"[STRATEGY] 最佳模型 {best_model} (置信度: {best_confidence:.2f}): {best_answer_str[:100]}...")
            return self._parse_ai_response(best_answer_str)
            
        except Exception as e:
            logger.error(f"[STRATEGY] AI 解题失败: {e}")
            return []
