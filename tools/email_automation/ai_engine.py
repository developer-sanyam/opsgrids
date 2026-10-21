"""Multi-AI contextual reply generator supporting OpenAI (ChatGPT) and Google Gemini.

Generates intelligent, context-aware automated replies for Email and WhatsApp
based on conversation history, user tone, and company context.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

import httpx

logger = logging.getLogger("email_automation.ai_engine")

ChannelType = Literal["email", "whatsapp"]


DEFAULT_SYSTEM_PROMPT = """You are an AI assistant for OpsGrids (a unified operations
platform providing CRM, Attendance, Invoicing, Inventory, Payroll, and Support).
Your task is to write a polite, helpful, context-aware reply to an incoming message.

Guidelines:
1. Always maintain a professional, courteous, and helpful tone.
2. If the user asks a question, answer it directly and clearly.
3. If this is a cold follow-up or lead inquiry, emphasize how bringing core operations onto
   one connected platform saves time and costs.
4. Channel Adaptation:
   - For EMAIL: Include a polite greeting (e.g. "Hi [Name],"), well-structured short paragraphs,
     and a professional sign-off (e.g. "Best regards,\\nOpsGrids Team").
   - For WHATSAPP: Keep it conversational, friendly, and concise (1 to 3 short sentences max).
     Avoid corporate boilerplate or long paragraphs.
5. Never invent false commitments or specific pricing unless asked. Suggest a quick 10-minute
   demo/walkthrough if appropriate.
6. Do not include markdown headers (# or ##) or HTML tags.
"""


class AIReplyEngine:
    """Manages AI-driven contextual response generation using OpenAI or Google Gemini."""

    def __init__(
        self,
        openai_key: str | None = None,
        gemini_key: str | None = None,
        openai_model: str | None = None,
        gemini_model: str | None = None,
        provider: str | None = None,
    ) -> None:
        self.openai_key = openai_key or os.getenv("OPENAI_API_KEY", "")
        self.gemini_key = gemini_key or os.getenv("GEMINI_API_KEY", "")
        self.openai_model = openai_model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.gemini_model = gemini_model or os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        self.provider = (provider or os.getenv("AI_PROVIDER", "auto")).lower()

    def has_ai_configured(self) -> bool:
        """Returns True if at least one valid AI provider key is present."""
        return bool(self.openai_key or self.gemini_key)

    def generate_reply(
        self,
        incoming_message: str,
        sender_name: str = "",
        subject: str = "",
        channel: ChannelType = "email",
        conversation_history: list[dict[str, str]] | None = None,
        custom_instructions: str = "",
    ) -> str:
        """Generates an AI reply based on conversation history and channel.

        Falls back to a clean default template if no AI provider is configured.
        """
        # Determine active provider
        active_provider = self._select_provider()

        if active_provider == "gemini":
            try:
                return self._call_gemini(
                    incoming_message=incoming_message,
                    sender_name=sender_name,
                    subject=subject,
                    channel=channel,
                    conversation_history=conversation_history,
                    custom_instructions=custom_instructions,
                )
            except Exception as exc:
                logger.warning("Gemini generation failed: %s; trying OpenAI fallback", exc)
                if self.openai_key:
                    try:
                        return self._call_openai(
                            incoming_message=incoming_message,
                            sender_name=sender_name,
                            subject=subject,
                            channel=channel,
                            conversation_history=conversation_history,
                            custom_instructions=custom_instructions,
                        )
                    except Exception as e:
                        logger.error("OpenAI fallback also failed: %s", e)

        elif active_provider == "openai":
            try:
                return self._call_openai(
                    incoming_message=incoming_message,
                    sender_name=sender_name,
                    subject=subject,
                    channel=channel,
                    conversation_history=conversation_history,
                    custom_instructions=custom_instructions,
                )
            except Exception as exc:
                logger.warning("OpenAI generation failed: %s; trying Gemini fallback", exc)
                if self.gemini_key:
                    try:
                        return self._call_gemini(
                            incoming_message=incoming_message,
                            sender_name=sender_name,
                            subject=subject,
                            channel=channel,
                            conversation_history=conversation_history,
                            custom_instructions=custom_instructions,
                        )
                    except Exception as e:
                        logger.error("Gemini fallback also failed: %s", e)

        # Fallback template if no AI key configured or calls failed
        return self._fallback_template(channel, sender_name, subject)

    def _select_provider(self) -> str:
        if self.provider in ("gemini", "google") and self.gemini_key:
            return "gemini"
        if self.provider in ("openai", "chatgpt") and self.openai_key:
            return "openai"

        # Auto preference: Gemini if key available, else OpenAI
        if self.gemini_key:
            return "gemini"
        if self.openai_key:
            return "openai"
        return "none"

    def _build_system_prompt(self, channel: ChannelType, custom_instructions: str = "") -> str:
        prompt = DEFAULT_SYSTEM_PROMPT
        if custom_instructions:
            prompt += f"\nSpecific Instructions for this workspace:\n{custom_instructions}\n"
        prompt += f"\nCurrent Delivery Channel: {channel.upper()}"
        return prompt

    def _call_gemini(
        self,
        incoming_message: str,
        sender_name: str,
        subject: str,
        channel: ChannelType,
        conversation_history: list[dict[str, str]] | None,
        custom_instructions: str,
    ) -> str:
        """Invokes Google Gemini API via REST."""
        system_prompt = self._build_system_prompt(channel, custom_instructions)

        # Build contents array
        contents = []
        if conversation_history:
            for item in conversation_history:
                role = "model" if item.get("role") in ("assistant", "model") else "user"
                contents.append({"role": role, "parts": [{"text": item.get("content", "")}]})

        user_content = f"Incoming Message from: {sender_name or 'Customer'}\n"
        if subject:
            user_content += f"Subject: {subject}\n"
        user_content += f"Message Body:\n{incoming_message}\n\nPlease generate the reply:"

        contents.append({"role": "user", "parts": [{"text": user_content}]})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 800,
            },
        }

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            return text.strip()

    def _call_openai(
        self,
        incoming_message: str,
        sender_name: str,
        subject: str,
        channel: ChannelType,
        conversation_history: list[dict[str, str]] | None,
        custom_instructions: str,
    ) -> str:
        """Invokes OpenAI (ChatGPT) API via REST."""
        system_prompt = self._build_system_prompt(channel, custom_instructions)

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for item in conversation_history:
                messages.append(
                    {
                        "role": "assistant"
                        if item.get("role") in ("assistant", "model")
                        else "user",
                        "content": item.get("content", ""),
                    }
                )

        user_content = f"Incoming Message from: {sender_name or 'Customer'}\n"
        if subject:
            user_content += f"Subject: {subject}\n"
        user_content += f"Message Body:\n{incoming_message}\n\nPlease generate the reply:"

        messages.append({"role": "user", "content": user_content})

        url = "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": self.openai_model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 800,
        }
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return text.strip()

    def _fallback_template(self, channel: ChannelType, sender_name: str, subject: str) -> str:
        """Safe default reply when no AI key is provided."""
        greeting = f"Hi {sender_name}," if sender_name else "Hello,"
        if channel == "whatsapp":
            return (
                f"{greeting} Thanks for reaching out to OpsGrids! We received your message "
                "and our team will get back to you shortly."
            )

        clean_subj = subject
        if clean_subj.lower().startswith("re:"):
            clean_subj = clean_subj[3:].strip()

        return (
            f"{greeting}\n\n"
            "Thank you for contacting OpsGrids. We have received your inquiry regarding "
            f"'{clean_subj}' and our team is reviewing it. We will get back to you shortly.\n\n"
            "Best regards,\n"
            "OpsGrids Team"
        )
