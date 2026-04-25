#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek 兼容客户端封装

把原来一次性执行的示例脚本重构为可复用模块，供
util.ai_provider / 测试脚本直接调用。
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import OpenAI

from config import (
    deepseek_api_key as config_deepseek_api_key,
    deepseek_base_url as config_deepseek_base_url,
    deepseek_enable_thinking as config_deepseek_enable_thinking,
    deepseek_model as config_deepseek_model,
    deepseek_reasoning_effort as config_deepseek_reasoning_effort,
    deepseek_timeout as config_deepseek_timeout,
)


@dataclass(frozen=True)
class DeepSeekRuntimeConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    timeout: int = 30
    reasoning_effort: str = "high"
    enable_thinking: bool = True


def _coalesce_text(*values: Any, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _coalesce_int(*values: Any, default: int = 30) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _coalesce_bool(*values: Any, default: bool = True) -> bool:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _resolve_runtime_config(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    enable_thinking: Optional[bool] = None,
) -> DeepSeekRuntimeConfig:
    return DeepSeekRuntimeConfig(
        api_key=_coalesce_text(
            api_key,
            os.getenv("DEEPSEEK_API_KEY"),
            config_deepseek_api_key,
        ),
        base_url=_coalesce_text(
            base_url,
            os.getenv("DEEPSEEK_BASE_URL"),
            config_deepseek_base_url,
            default="https://api.deepseek.com",
        ),
        model=_coalesce_text(
            model,
            os.getenv("DEEPSEEK_MODEL"),
            config_deepseek_model,
            default="deepseek-v4-flash",
        ),
        timeout=_coalesce_int(
            timeout,
            os.getenv("DEEPSEEK_TIMEOUT"),
            config_deepseek_timeout,
            default=30,
        ),
        reasoning_effort=_coalesce_text(
            reasoning_effort,
            os.getenv("DEEPSEEK_REASONING_EFFORT"),
            config_deepseek_reasoning_effort,
            default="high",
        ),
        enable_thinking=_coalesce_bool(
            enable_thinking,
            os.getenv("DEEPSEEK_ENABLE_THINKING"),
            config_deepseek_enable_thinking,
            default=True,
        ),
    )


def build_client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> OpenAI:
    """
    构建 DeepSeek 兼容的 OpenAI SDK Client。

    Args:
        api_key: 显式传入的 API Key，未传时依次回退到环境变量、config.py
        base_url: 显式传入的 Base URL，未传时依次回退到环境变量、config.py

    Returns:
        OpenAI client
    """
    runtime = _resolve_runtime_config(api_key=api_key, base_url=base_url)
    if not runtime.api_key:
        raise ValueError(
            "缺少 DeepSeek API Key，请配置 DEEPSEEK_API_KEY 或在 config.ini 中提供 DEEPSEEK_API_KEY"
        )

    return OpenAI(
        api_key=runtime.api_key,
        base_url=runtime.base_url.rstrip("/"),
    )


def chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    timeout: Optional[int] = None,
    client: Optional[OpenAI] = None,
    reasoning_effort: Optional[str] = None,
    enable_thinking: Optional[bool] = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    top_p: float = 0.9,
    **kwargs: Any,
) -> str:
    """
    调用 DeepSeek 兼容接口完成一次聊天请求。

    Args:
        messages: OpenAI Chat Completions 消息列表
        model: 模型名称
        timeout: 请求超时时间（秒）
        client: 预构建的 OpenAI client；未传则内部构建
        reasoning_effort: DeepSeek 推理强度
        enable_thinking: 是否启用 thinking
        max_tokens: 最大输出长度
        temperature: 温度
        top_p: top_p
        **kwargs: 额外参数（会尽量透传）

    Returns:
        AI 返回的 content 文本
    """
    runtime = _resolve_runtime_config(
        model=model,
        timeout=timeout,
        reasoning_effort=reasoning_effort,
        enable_thinking=enable_thinking,
    )

    sdk_client = client or build_client()

    request_kwargs: Dict[str, Any] = {
        "model": runtime.model,
        "messages": messages,
        "stream": False,
        "timeout": runtime.timeout,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }

    # DeepSeek 的思考模式参数
    if runtime.reasoning_effort:
        request_kwargs["reasoning_effort"] = runtime.reasoning_effort
    if runtime.enable_thinking:
        request_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

    # 兼容调用方传入的可选参数
    for key in (
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "stop",
        "tools",
        "tool_choice",
        "metadata",
        "response_format",
    ):
        if key in kwargs and kwargs[key] is not None:
            # DeepSeek 不稳定支持 response_format，这里保留给未来兼容，但默认不强依赖
            if key == "response_format":
                continue
            request_kwargs[key] = kwargs[key]

    extra_body = kwargs.get("extra_body")
    if isinstance(extra_body, dict):
        request_kwargs["extra_body"] = {
            **request_kwargs.get("extra_body", {}),
            **extra_body,
        }

    response = sdk_client.chat.completions.create(**request_kwargs)
    content = response.choices[0].message.content
    return str(content or "").strip()


def _demo() -> None:
    """命令行直接运行时的最小连通性示例。"""
    if not _coalesce_text(os.getenv("DEEPSEEK_API_KEY"), config_deepseek_api_key):
        print("[DEEPSEEK] 未配置 DEEPSEEK_API_KEY，已跳过示例请求")
        return

    demo_messages = [
        {"role": "system", "content": "你是一个只回复简短确认信息的助手。"},
        {"role": "user", "content": "请回复：收到"},
    ]

    try:
        reply = chat_completion(
            messages=demo_messages,
            model=os.getenv("DEEPSEEK_MODEL") or config_deepseek_model,
            timeout=_coalesce_int(os.getenv("DEEPSEEK_TIMEOUT"), config_deepseek_timeout, default=30),
        )
        print(reply)
    except Exception as exc:
        print(f"[DEEPSEEK] 示例请求失败: {exc}")


if __name__ == "__main__":
    _demo()
