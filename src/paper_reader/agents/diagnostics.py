"""Stable, redaction-safe user actions for Agent probe results."""

from __future__ import annotations

from paper_reader.agents.contracts import AgentBackendError, AgentErrorCode, AgentProbeCode


def agent_recommended_action(code: AgentProbeCode) -> str | None:
    if code is AgentProbeCode.READY:
        return None
    if code is AgentProbeCode.NOT_INSTALLED:
        return "请先安装对应 Agent，然后重新检测。"
    if code is AgentProbeCode.NOT_AUTHENTICATED:
        return "请先完成登录或凭据配置，然后重新检测。"
    if code is AgentProbeCode.CONFIGURATION_INVALID:
        return "请修正 Agent 配置文件中的无效设置，然后重新检测。"
    if code is AgentProbeCode.VERSION_UNSUPPORTED:
        return "请升级 Agent 到受支持的版本，然后重新检测。"
    if code is AgentProbeCode.SERVICE_NOT_RUNNING:
        return "请启动本机 Ollama 服务，然后重新检测。"
    if code is AgentProbeCode.PROBE_TIMEOUT:
        return "检测超时，请确认服务可访问后重试。"
    return "Agent 当前不可用，请检查服务或网络后重试。"


def agent_generation_message(error: AgentBackendError) -> str:
    messages = {
        AgentErrorCode.TIMEOUT: "模型生成超时，请尝试较小的模型或降低推理强度后重试。",
        AgentErrorCode.OUTPUT_LIMIT: (
            "模型已达到输出或上下文限制，未能完成最终答案。"
            "请降低推理强度（关键词可选“最小”）或缩小输入范围后重试。"
        ),
        AgentErrorCode.INVALID_OUTPUT: "模型返回的结果不符合所需结构，请重试或选择其他模型。",
        AgentErrorCode.AUTHENTICATION: "Agent 登录或凭据无效，请在设置中检查并重新检测。",
        AgentErrorCode.TRANSPORT: "无法连接模型服务，请确认所选服务已启动且网络可访问。",
        AgentErrorCode.CANCELLED: "模型生成已取消。",
    }
    return messages.get(error.code, str(error))


__all__ = ["agent_generation_message", "agent_recommended_action"]
