#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Provider 统一接口层

支持多个AI模型并行调用，并根据置信度选择最终答案。
"""

import os
import time
import logging
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, ALL_COMPLETED

from config import (
    AI_MODELS_CONFIG,
    ai_request_timeout,
)
from openai import OpenAI


# 关闭OpenAI SDK的详细HTTP日志
logging.getLogger("openai._base_client").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


class MultiModelAIProvider:
    """
    多模型AI提供商统一接口
    支持并行调用多个模型，并根据优先级（置信度）选择最佳答案
    """

    def __init__(self, timeout: int = None):
        """
        初始化多模型AI Provider

        Args:
            timeout: 请求超时时间（秒），默认使用配置中的值
        """
        self.timeout = timeout or ai_request_timeout
        self.models = AI_MODELS_CONFIG
        self.clients = {}
        self._init_clients()

    def _init_clients(self):
        """初始化所有模型的客户端"""
        for model_config in self.models:
            try:
                client = OpenAI(
                    api_key=model_config["api_key"],
                    base_url=model_config["base_url"] or "https://api.chatanywhere.tech/v1",
                )
                self.clients[model_config["name"]] = {
                    "client": client,
                    "config": model_config
                }
            except Exception as e:
                print(f"[AI PROVIDER] 模型 {model_config['name']} 初始化失败: {e}")

    def _call_single_model(self, model_name: str, messages: List[Dict[str, str]], **kwargs) -> Tuple[str, float, str]:
        """
        调用单个模型
        
        Returns:
            (answer, confidence, model_name)
        """
        if model_name not in self.clients:
            raise ValueError(f"模型 {model_name} 未初始化")
            
        client_info = self.clients[model_name]
        client = client_info["client"]
        config = client_info["config"]
        
        effective_timeout = kwargs.get("timeout", self.timeout)
        
        try:
            request_kwargs = {
                "model": kwargs.get("model", config["model"]),
                "messages": messages,
                "max_tokens": kwargs.get("max_tokens", 1024),
                "temperature": kwargs.get("temperature", 0.7),
                "top_p": kwargs.get("top_p", 0.9),
                "timeout": effective_timeout,  # OpenAI SDK的超时参数
            }

            response_format = kwargs.get("response_format", {"type": "json_object"})
            if response_format is not None:
                request_kwargs["response_format"] = response_format

            # OpenAI SDK本身会处理超时，我们依赖它的超时机制
            response = client.chat.completions.create(**request_kwargs)
            answer = response.choices[0].message.content
            
            # 使用模型配置中的priority作为置信度
            confidence = float(config.get("priority", 1.0))
            
            return answer, confidence, model_name
            
        except Exception as e:
            print(f"[AI PROVIDER] 调用模型 {model_name} 失败: {e}")
            # 返回空答案，置信度为0
            return "", 0.0, model_name

    def chat_completion_parallel(
        self,
        messages: List[Dict[str, str]],
        **kwargs,
    ) -> Tuple[str, float, str]:
        """
        并行调用所有可用模型，返回置信度最高的答案

        Args:
            messages: 消息列表
            **kwargs: 其他参数

        Returns:
            (best_answer, best_confidence, best_model_name)
        """
        if not self.clients:
            raise RuntimeError("没有可用的AI模型")
            
        results = []
        effective_timeout = kwargs.get("timeout", self.timeout)
        
        # 并行调用所有模型
        with ThreadPoolExecutor(max_workers=len(self.clients)) as executor:
            future_to_model = {
                executor.submit(self._call_single_model, model_name, messages, **kwargs): model_name
                for model_name in self.clients.keys()
            }
            
            # 等待所有任务完成或超时
            try:
                # 使用总的超时时间，确保等待足够长的时间
                done_futures, not_done_futures = wait(
                    future_to_model.keys(), 
                    timeout=effective_timeout + 2,  # 额外缓冲时间
                    return_when=ALL_COMPLETED
                )
                
                # 处理已完成的任务
                for future in done_futures:
                    try:
                        result = future.result(timeout=1)  # 快速获取结果
                        results.append(result)
                    except Exception as e:
                        model_name = future_to_model[future]
                        print(f"[AI PROVIDER] 模型 {model_name} 执行异常: {e}")
                        results.append(("", 0.0, model_name))
                
                # 处理超时未完成的任务
                for future in not_done_futures:
                    model_name = future_to_model[future]
                    print(f"[AI PROVIDER] 模型 {model_name} 超时未响应")
                    results.append(("", 0.0, model_name))
                    
            except Exception as e:
                print(f"[AI PROVIDER] 并行调用异常: {e}")
                # 如果整体超时，至少收集已有的结果
                for future in as_completed(future_to_model, timeout=1):
                    try:
                        result = future.result(timeout=1)
                        results.append(result)
                    except Exception as inner_e:
                        model_name = future_to_model[future]
                        print(f"[AI PROVIDER] 模型 {model_name} 执行异常: {inner_e}")
                        results.append(("", 0.0, model_name))
        
        print(f"[AI PROVIDER] 模型调用结果: {results}")

        # 选择置信度最高的有效答案
        # 优先选择非空答案，然后按置信度排序
        non_empty_results = [r for r in results if r[0] and r[0].strip() != "[]" and r[1] > 0]
        if non_empty_results:
            best_result = max(non_empty_results, key=lambda x: x[1])
            return best_result
            
        # 如果没有非空答案，尝试找看起来有效的空答案（可能是正确的空答案）
        valid_results = [r for r in results if r[1] > 0]
        if valid_results:
            best_result = max(valid_results, key=lambda x: x[1])
            return best_result
            
        # 如果所有结果都无效，返回置信度最高的（可能是空答案）
        if results:
            best_result = max(results, key=lambda x: x[1])
            return best_result
        else:
            raise RuntimeError("所有模型调用都失败了")

    def get_available_models(self) -> List[str]:
        """获取可用的模型列表"""
        return list(self.clients.keys())
