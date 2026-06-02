import asyncio
import json
import os
import time
from typing import Literal

from .agent.decorators import agent_event_handler
from ten_runtime import (
    AsyncExtension,
    AsyncTenEnv,
    Cmd,
    Data,
)

from .agent.agent import Agent
from .agent.events import (
    ASRResultEvent,
    LLMResponseEvent,
    ToolRegisterEvent,
    UserJoinedEvent,
    UserLeftEvent,
)
from .helper import _send_cmd, _send_data, parse_sentences
from .config import MainControlConfig  # assume extracted from your base model

# TTS pre-generation — optional, gracefully degrades if module not available
_tts_pregen_available = False
try:
    import sys as _sys
    _current_dir = os.path.dirname(os.path.abspath(__file__))
    _extension_dir = os.path.dirname(_current_dir)
    if _extension_dir not in _sys.path:
        _sys.path.insert(0, _extension_dir)
    from xfyun_tts_python.xfyun_tts import XfYunTTSClient  # noqa: E402
    from xfyun_tts_python.config import XfYunTTSConfig  # noqa: E402
    _tts_pregen_available = True
except ImportError:
    pass

import uuid


class MainControlExtension(AsyncExtension):
    """
    The entry point of the agent module.
    Consumes semantic AgentEvents from the Agent class and drives the runtime behavior.
    """

    def __init__(self, name: str):
        super().__init__(name)
        self.ten_env: AsyncTenEnv = None
        self.agent: Agent = None
        self.config: MainControlConfig = None

        self.stopped: bool = False
        self._rtc_user_count: int = 0
        self.sentence_fragment: str = ""
        self.turn_id: int = 0
        self.session_id: str = "0"

    def _current_metadata(self) -> dict:
        return {"session_id": self.session_id, "turn_id": self.turn_id}

    async def on_init(self, ten_env: AsyncTenEnv):
        self.ten_env = ten_env

        # Load config from runtime properties
        config_json, _ = await ten_env.get_property_to_json(None)
        self.config = MainControlConfig.model_validate_json(config_json)

        self.agent = Agent(ten_env)

        # Now auto-register decorated methods
        for attr_name in dir(self):
            fn = getattr(self, attr_name)
            event_type = getattr(fn, "_agent_event_type", None)
            if event_type:
                self.agent.on(event_type, fn)

    # === Register handlers with decorators ===
    @agent_event_handler(UserJoinedEvent)
    async def _on_user_joined(self, event: UserJoinedEvent):
        self._rtc_user_count += 1
        if self._rtc_user_count == 1 and self.config and self.config.greeting:
            await self._send_to_tts(self.config.greeting, True)
            await self._send_transcript(
                "assistant", self.config.greeting, True, 100
            )

    @agent_event_handler(UserLeftEvent)
    async def _on_user_left(self, event: UserLeftEvent):
        self._rtc_user_count -= 1

    @agent_event_handler(ToolRegisterEvent)
    async def _on_tool_register(self, event: ToolRegisterEvent):
        await self.agent.register_llm_tool(event.tool, event.source)

    @agent_event_handler(ASRResultEvent)
    async def _on_asr_result(self, event: ASRResultEvent):
        self.session_id = event.metadata.get("session_id", "100")
        stream_id = int(self.session_id)
        if not event.text:
            return
        if event.final or len(event.text) > 2:
            await self._interrupt()
        if event.final:
            self.turn_id += 1
            await self.agent.queue_llm_input(event.text)
        await self._send_transcript("user", event.text, event.final, stream_id)

    @agent_event_handler(LLMResponseEvent)
    async def _on_llm_response(self, event: LLMResponseEvent):
        if not event.is_final and event.type == "message":
            sentences, self.sentence_fragment = parse_sentences(
                self.sentence_fragment, event.delta
            )
            for s in sentences:
                await self._send_to_tts(s, False)

        if event.is_final and event.type == "message":
            remaining_text = self.sentence_fragment or ""
            self.sentence_fragment = ""
            await self._send_to_tts(remaining_text, True)

        await self._send_transcript(
            "assistant",
            event.text,
            event.is_final,
            100,
            data_type=("reasoning" if event.type == "reasoning" else "text"),
        )

    async def on_start(self, ten_env: AsyncTenEnv):
        ten_env.log_info("[MainControlExtension] on_start")
        # Pre-generate greeting audio in background (gracefully degrades)
        if _tts_pregen_available:
            asyncio.create_task(self._pre_generate_greeting())
        else:
            ten_env.log_info(
                "[MainControlExtension] TTS pre-generation skipped "
                "(xfyun_tts_python module not importable)"
            )

    async def _pre_generate_greeting(self):
        """Pre-generate greeting TTS audio and save as local PCM file."""
        if not _tts_pregen_available:
            return
        try:
            greeting_text = self.config.greeting if self.config else ""
            if not greeting_text:
                self.ten_env.log_warn("No greeting text, skipping pre-generation")
                return

            self.ten_env.log_info(
                f"[MainControlExtension] Pre-generating greeting TTS to file: \"{greeting_text}\""
            )

            tts_config = XfYunTTSConfig(
                app_id=os.getenv("XF_XFYUN_TTS_APP_ID", ""),
                api_key=os.getenv("XF_XFYUN_TTS_API_KEY", ""),
                api_secret=os.getenv("XF_XFYUN_TTS_API_SECRET", ""),
                voice_name="aisbabyxu",
                sample_rate=16000,
                speed=100,
                volume=50,
            )

            client = XfYunTTSClient(tts_config, self.ten_env)
            pcm_chunks: list[bytes] = []
            async for chunk, event_type in client.get(greeting_text):
                if event_type == 1 and chunk:
                    pcm_chunks.append(chunk)
                elif event_type == 2:
                    break
                elif event_type in (3, 4):
                    self.ten_env.log_error(f"Greeting TTS pre-generation failed: {chunk}")
                    return

            if pcm_chunks:
                pcm_data = b"".join(pcm_chunks)
                duration_s = len(pcm_data) / (tts_config.sample_rate * 2)
                greeting_file = "/tmp/greeting_audio.pcm"
                with open(greeting_file, "wb") as f:
                    f.write(pcm_data)
                self.ten_env.log_info(
                    f"[MainControlExtension] Greeting TTS saved to file: "
                    f"{greeting_file} ({len(pcm_data)} bytes, {duration_s:.1f}s, "
                    f"speed={tts_config.speed})"
                )
            else:
                self.ten_env.log_warn("Greeting TTS returned no audio")

        except Exception as e:
            self.ten_env.log_error(f"Failed to pre-generate greeting: {e}")

    async def on_stop(self, ten_env: AsyncTenEnv):
        ten_env.log_info("[MainControlExtension] on_stop")
        self.stopped = True
        await self.agent.stop()

    async def on_cmd(self, ten_env: AsyncTenEnv, cmd: Cmd):
        await self.agent.on_cmd(cmd)

    async def on_data(self, ten_env: AsyncTenEnv, data: Data):
        await self.agent.on_data(data)

    # === helpers ===
    async def _send_transcript(
        self,
        role: str,
        text: str,
        final: bool,
        stream_id: int,
        data_type: Literal["text", "reasoning"] = "text",
    ):
        """
        Sends the transcript (ASR or LLM output) to the message collector.
        """
        if data_type == "text":
            await _send_data(
                self.ten_env,
                "message",
                "message_collector",
                {
                    "data_type": "transcribe",
                    "role": role,
                    "text": text,
                    "text_ts": int(time.time() * 1000),
                    "is_final": final,
                    "stream_id": stream_id,
                },
            )
        elif data_type == "reasoning":
            await _send_data(
                self.ten_env,
                "message",
                "message_collector",
                {
                    "data_type": "raw",
                    "role": role,
                    "text": json.dumps(
                        {
                            "type": "reasoning",
                            "data": {
                                "text": text,
                            },
                        }
                    ),
                    "text_ts": int(time.time() * 1000),
                    "is_final": final,
                    "stream_id": stream_id,
                },
            )
        self.ten_env.log_info(
            f"[MainControlExtension] Sent transcript: {role}, final={final}, text={text}"
        )

    async def _send_to_tts(self, text: str, is_final: bool):
        """
        Sends a sentence to the TTS system.
        """
        request_id = f"tts-request-{self.turn_id}"
        await _send_data(
            self.ten_env,
            "tts_text_input",
            "tts",
            {
                "request_id": request_id,
                "text": text,
                "text_input_end": is_final,
                "metadata": self._current_metadata(),
            },
        )
        self.ten_env.log_info(
            f"[MainControlExtension] Sent to TTS: is_final={is_final}, text={text}"
        )

    async def _interrupt(self):
        """
        Interrupts ongoing LLM and TTS generation. Typically called when user speech is detected.
        """
        self.sentence_fragment = ""
        await self.agent.flush_llm()
        await _send_data(
            self.ten_env, "tts_flush", "tts", {"flush_id": str(uuid.uuid4())}
        )
        await _send_cmd(self.ten_env, "flush", "agora_rtc")
        self.ten_env.log_info("[MainControlExtension] Interrupt signal sent")
