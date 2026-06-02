from datetime import datetime
import os
from typing import Optional, Dict, Any

from typing_extensions import override
from .const import (
    DUMP_FILE_NAME,
    MODULE_NAME_ASR,
)
from ten_ai_base.asr import (
    ASRBufferConfig,
    ASRBufferConfigModeDiscard,
    ASRResult,
    AsyncASRBaseExtension,
)
from ten_ai_base.message import (
    ModuleError,
    ModuleErrorVendorInfo,
    ModuleErrorCode,
)
from ten_runtime import (
    AsyncTenEnv,
    AudioFrame,
)
from ten_ai_base.const import (
    LOG_CATEGORY_KEY_POINT,
    LOG_CATEGORY_VENDOR,
)
from ten_ai_base.dumper import Dumper
from .reconnect_manager import ReconnectManager
from .recognition import XfyunWSRecognition, XfyunWSRecognitionCallback
from .config import XfyunASRConfig


class XfyunBigmodelASRExtension(
    AsyncASRBaseExtension, XfyunWSRecognitionCallback
):
    """Xfyun ASR Extension"""

    def __init__(self, name: str):
        super().__init__(name)
        self.recognition: Optional[XfyunWSRecognition] = None
        self.config: Optional[XfyunASRConfig] = None
        self.audio_dumper: Optional[Dumper] = None
        self.sent_user_audio_duration_ms_before_last_reset: int = 0
        self.last_finalize_timestamp: int = 0

        # WPGS mode status variables
        self.wpgs_buffer: Dict[int, Dict[str, Any]] = (
            {}
        )  # Mapping from sequence number to data including text, bg, ed

        # Cache last non-empty text for finalization
        self._last_non_empty_text: str = ""

        # Track last auto-finalized text to avoid duplicate finalization
        self._last_finalized_text: str = ""

        # Reconnection manager
        self.reconnect_manager: Optional[ReconnectManager] = None

        # Audio frame metrics
        self.audio_frame_received: int = 0
        self.audio_frame_sent_to_asr: int = 0

        # Flag: True when stop_connection was explicitly called (not unexpected disconnect)
        self._stopped_explicitly: bool = False

    @override
    async def on_deinit(self, ten_env: AsyncTenEnv) -> None:
        await super().on_deinit(ten_env)
        if self.audio_dumper:
            await self.audio_dumper.stop()
            self.audio_dumper = None

    @override
    def vendor(self) -> str:
        """Get ASR vendor name"""
        return "xfyun_bigmodel"

    @override
    async def on_init(self, ten_env: AsyncTenEnv) -> None:
        await super().on_init(ten_env)

        # Initialize reconnection manager
        self.reconnect_manager = ReconnectManager(logger=ten_env)

        config_json, _ = await ten_env.get_property_to_json("")

        try:
            self.config = XfyunASRConfig.model_validate_json(config_json)
            self.config.update(self.config.params)
            # 强制覆盖：防止 params 中残留的 IST 配置反向污染
            self.config.enforce_iat_bigmodel()
            ten_env.log_info(
                f"Xfyun ASR config [provider={self.config.provider}]: "
                f"host={self.config.host}, path={self.config.path}, "
                f"domain={self.config.domain}, language={self.config.language}, "
                f"accent={self.config.accent}, sample_rate={self.config.sample_rate}, "
                f"channels={self.config.channels}, bit_depth={self.config.bit_depth}, "
                f"audio_encoding={self.config.audio_encoding}, "
                f"eos={self.config.eos}ms, dwa={self.config.dwa} "
                f"(credentials: {self.config.to_json(sensitive_handling=True)})",
                category=LOG_CATEGORY_KEY_POINT,
            )
            if self.config.dump:
                dump_file_path = os.path.join(
                    self.config.dump_path, DUMP_FILE_NAME
                )
                self.audio_dumper = Dumper(dump_file_path)

        except Exception as e:
            ten_env.log_error(f"Invalid Xfyun ASR config: {e}")
            self.config = XfyunASRConfig.model_validate_json("{}")
            await self.send_asr_error(
                ModuleError(
                    module=MODULE_NAME_ASR,
                    code=ModuleErrorCode.FATAL_ERROR.value,
                    message=str(e),
                ),
            )

    @override
    async def start_connection(self) -> None:
        """Start ASR connection"""
        assert self.config is not None
        self.ten_env.log_info(
            f"Starting Xfyun ASR connection [provider={self.config.provider}]"
        )

        try:
            # Check required credentials
            if not self.config.app_id or self.config.app_id.strip() == "":
                error_msg = (
                    "Xfyun App ID is required but not provided or is empty"
                )
                self.ten_env.log_error(error_msg)
                await self.send_asr_error(
                    ModuleError(
                        module=MODULE_NAME_ASR,
                        code=ModuleErrorCode.FATAL_ERROR.value,
                        message=error_msg,
                    ),
                )
                return

            if not self.config.api_key or self.config.api_key.strip() == "":
                error_msg = (
                    "Xfyun API key is required but not provided or is empty"
                )
                self.ten_env.log_error(error_msg)
                await self.send_asr_error(
                    ModuleError(
                        module=MODULE_NAME_ASR,
                        code=ModuleErrorCode.FATAL_ERROR.value,
                        message=error_msg,
                    ),
                )
                return

            if (
                not self.config.api_secret
                or self.config.api_secret.strip() == ""
            ):
                error_msg = (
                    "Xfyun API secret is required but not provided or is empty"
                )
                self.ten_env.log_error(error_msg)
                await self.send_asr_error(
                    ModuleError(
                        module=MODULE_NAME_ASR,
                        code=ModuleErrorCode.FATAL_ERROR.value,
                        message=error_msg,
                    ),
                )
                return

            # Stop existing connection
            if self.is_connected():
                await self.stop_connection()
            # Start audio dumper
            if self.audio_dumper:
                await self.audio_dumper.start()

            # Prepare Xfyun IAT bigmodel config (中英识别大模型 API)
            xfyun_config = {
                "host": self.config.host,
                "path": self.config.path,
                "domain": self.config.domain,
                "language": self.config.language,
                "accent": self.config.accent,
                "dwa": self.config.dwa,
                "eos": self.config.eos,
                "samplerate": self.config.sample_rate,
                "channels": self.config.channels,
                "bit_depth": self.config.bit_depth,
                "audio_encoding": self.config.audio_encoding,
            }

            self.ten_env.log_info(
                f"Xfyun ASR [provider={self.config.provider}] connecting to "
                f"wss://{self.config.host}{self.config.path} "
                f"domain={self.config.domain} language={self.config.language} "
                f"accent={self.config.accent} sample_rate={self.config.sample_rate} "
                f"channels={self.config.channels} bit_depth={self.config.bit_depth} "
                f"audio_encoding={self.config.audio_encoding} eos={self.config.eos}ms"
            )

            # Create recognition instance
            self.recognition = XfyunWSRecognition(
                app_id=self.config.app_id,
                api_key=self.config.api_key,
                api_secret=self.config.api_secret,
                audio_timeline=self.audio_timeline,
                ten_env=self.ten_env,
                config=xfyun_config,
                callback=self,
            )

            # Start recognition (now async)
            success = await self.recognition.start()
            if success:
                self.ten_env.log_info(
                    "Xfyun ASR connection started successfully"
                )
            else:
                error_msg = "Failed to start Xfyun ASR connection"
                self.ten_env.log_error(error_msg)
                await self.send_asr_error(
                    ModuleError(
                        module=MODULE_NAME_ASR,
                        code=ModuleErrorCode.NON_FATAL_ERROR.value,
                        message=error_msg,
                    ),
                )

        except Exception as e:
            self.ten_env.log_error(f"Failed to start Xfyun ASR connection: {e}")
            await self.send_asr_error(
                ModuleError(
                    module=MODULE_NAME_ASR,
                    code=ModuleErrorCode.NON_FATAL_ERROR.value,
                    message=str(e),
                ),
            )

    @override
    async def on_open(self) -> None:
        """Handle callback when connection is established"""
        self.ten_env.log_info(
            "vendor_status_changed: on_open",
            category=LOG_CATEGORY_VENDOR,
        )
        # Notify reconnect manager of successful connection
        if self.reconnect_manager:
            self.reconnect_manager.mark_connection_successful()

        # Reset timeline and audio duration (recognition.py handles internal timeline)
        self.sent_user_audio_duration_ms_before_last_reset += (
            self.audio_timeline.get_total_user_audio_duration()
        )
        self.audio_timeline.reset()

        # Reset frame counters for new session
        self.audio_frame_received = 0
        self.audio_frame_sent_to_asr = 0

        # Reset explicit stop flag for new connection
        self._stopped_explicitly = False

        # Reset WPGS status variables and text cache
        self.wpgs_buffer.clear()
        self._last_non_empty_text = ""
        self._last_finalized_text = ""
        self.ten_env.log_debug("Xfyun ASR WPGS state reset")

        # Verify IAT bigmodel config at connection time
        assert self.config is not None
        self.ten_env.log_info(
            f"Xfyun ASR connection verified: "
            f"host={self.config.host}, "
            f"path={self.config.path}, "
            f"domain={self.config.domain}, "
            f"language={self.config.language}, "
            f"accent={self.config.accent}",
            category=LOG_CATEGORY_KEY_POINT,
        )

    @override
    async def on_result(self, message_data: dict) -> None:
        """Handle recognition result callback — 官方 IAT ls/status 判定

        Official final detection:
        - sub_end == True  → 来自 inner.ls == True（句子结束）
        - status == 2      → 服务端识别会话结束（header.status 或 result.status）
        """
        try:
            code = message_data.get("code")
            if code != 0:
                return

            data = message_data.get("data", {})
            status = data.get("status", -1)
            result_data = data.get("result", {})

            # ---- Extract fields ----
            sn = result_data.get("sn", -1)
            start_ms = result_data.get("bg", 0)
            end_ms = result_data.get("ed", 0)
            duration_ms = end_ms - start_ms if end_ms > start_ms else 0

            # Extract text from word list (ws[].cw[].w)
            data_ws = result_data.get("ws", [])
            result = ""
            for i in data_ws:
                for w in i.get("cw", []):
                    result += w.get("w", "")

            # ---- Official final detection ----
            sub_end = result_data.get("sub_end", False)
            is_session_end = (status == 2)
            is_final = (sub_end is True) or is_session_end

            # Handle WPGS streaming mode
            pgs = result_data.get("pgs")
            result_to_send = result

            if pgs:
                if pgs == "apd":  # Append
                    self.wpgs_buffer[sn] = {"text": result, "bg": start_ms, "ed": end_ms}
                    result_to_send = "".join(
                        self.wpgs_buffer[i]["text"]
                        for i in sorted(self.wpgs_buffer.keys())
                    )
                elif pgs == "rpl":  # Replace
                    rg = result_data.get("rg", [])
                    if len(rg) >= 2:
                        for key in list(self.wpgs_buffer.keys()):
                            if rg[0] <= key <= rg[1]:
                                self.wpgs_buffer.pop(key, None)
                    self.wpgs_buffer[sn] = {"text": result, "bg": start_ms, "ed": end_ms}
                    result_to_send = "".join(
                        self.wpgs_buffer[i]["text"]
                        for i in sorted(self.wpgs_buffer.keys())
                    )
            else:
                result_to_send = result

            # ---- Log EVERY result at INFO ----
            self.ten_env.log_info(
                f"[IAT→EXT] text=\"{result_to_send}\" is_final={is_final} "
                f"(sub_end={sub_end} status={status} session_end={is_session_end}) "
                f"sn={sn} pgs={pgs} ls_raw={result_data.get('ls', 'N/A')}"
            )

            # Clear WPGS on sentence end
            if is_final:
                self.wpgs_buffer.clear()
                self.ten_env.log_info(
                    f"[IAT→EXT] SENTENCE FINAL: \"{result_to_send}\" "
                    f"(trigger: sub_end={sub_end}, status={status})"
                )

            # Calculate timeline-based start
            actual_start_ms = int(
                self.audio_timeline.get_audio_duration_before_time(start_ms)
                + self.sent_user_audio_duration_ms_before_last_reset
            )

            # Cache last non-empty text for empty finals
            if result_to_send != "":
                self._last_non_empty_text = result_to_send

            if is_final and result_to_send == "" and self._last_non_empty_text:
                result_to_send = self._last_non_empty_text
                self.ten_env.log_info(
                    f"[IAT→EXT] Using cached text for final: \"{result_to_send}\""
                )

            if is_final:
                self._last_non_empty_text = ""

            # ---- Send to downstream (main_control → LLM) ----
            if self.config is not None and result_to_send != "":
                await self._handle_asr_result(
                    text=result_to_send,
                    final=is_final,
                    start_ms=actual_start_ms,
                    duration_ms=duration_ms,
                    language=self.config.normalized_language,
                )
            elif result_to_send == "":
                self.ten_env.log_debug(
                    f"[IAT→EXT] Skipping empty text (is_final={is_final})"
                )

            # Close recognition on session end
            if is_session_end and self.recognition:
                self.ten_env.log_info("[IAT→EXT] Session ended by server (status=2), closing")
                await self.recognition.close()

        except Exception as e:
            self.ten_env.log_error(f"Error processing Xfyun ASR result: {e}")

    @override
    async def on_error(
        self, error_msg: str, error_code: Optional[int] = None
    ) -> None:
        """Handle error callback"""
        self.ten_env.log_error(
            f"vendor_error: code: {error_code}, reason: {error_msg}",
            category=LOG_CATEGORY_VENDOR,
        )

        # Send error information
        await self.send_asr_error(
            ModuleError(
                module=MODULE_NAME_ASR,
                code=ModuleErrorCode.NON_FATAL_ERROR.value,
                message=error_msg,
            ),
            ModuleErrorVendorInfo(
                vendor=self.vendor(),
                code=str(error_code) if error_code else "unknown",
                message=error_msg,
            ),
        )

    @override
    async def on_close(self) -> None:
        """Handle callback when connection is closed"""
        self.ten_env.log_info(
            "vendor_status_changed: on_close",
            category=LOG_CATEGORY_VENDOR,
        )

        # Log final metrics summary
        self.ten_env.log_info(
            f"Xfyun ASR session metrics: audio_frame_received={self.audio_frame_received}, "
            f"audio_frame_sent_to_asr={self.audio_frame_sent_to_asr}, "
            f"actual_send={self.audio_frame_sent_to_asr} (>0={self.audio_frame_sent_to_asr > 0})"
        )

        # Clear WPGS status variables
        self.wpgs_buffer.clear()

        if not self.stopped and not self._stopped_explicitly:
            self.ten_env.log_warn(
                "Xfyun ASR connection closed unexpectedly. Reconnecting..."
            )
            await self._handle_reconnect()
        else:
            self.ten_env.log_info(
                "Xfyun ASR connection closed (stopped or explicit stop, no reconnect)"
            )

    @override
    async def finalize(self, session_id: str | None) -> None:
        """Finalize recognition"""
        assert self.config is not None

        self.last_finalize_timestamp = int(datetime.now().timestamp() * 1000)
        self.ten_env.log_debug(
            f"Xfyun ASR finalize start at {self.last_finalize_timestamp}"
        )

        await self._handle_finalize_disconnect()

    async def _handle_asr_result(
        self,
        text: str,
        final: bool,
        start_ms: int = 0,
        duration_ms: int = 0,
        language: str = "",
    ):
        """Process ASR recognition result"""
        assert self.config is not None

        if final:
            await self._finalize_end()

        asr_result = ASRResult(
            text=text,
            final=final,
            start_ms=start_ms,
            duration_ms=duration_ms,
            language=language,
            words=[],
        )

        await self.send_asr_result(asr_result)

    async def _handle_finalize_disconnect(self):
        """Handle disconnect mode finalization"""
        if self.recognition:
            await self.recognition.stop()
            self.ten_env.log_debug("Xfyun ASR finalize disconnect completed")

    async def _handle_reconnect(self):
        """Handle reconnection"""
        if not self.reconnect_manager:
            self.ten_env.log_error("ReconnectManager not initialized")
            return

        # Check if retry is still possible
        if not self.reconnect_manager.can_retry():
            self.ten_env.log_warn("No more reconnection attempts allowed")
            await self.send_asr_error(
                ModuleError(
                    module=MODULE_NAME_ASR,
                    code=ModuleErrorCode.NON_FATAL_ERROR.value,
                    message="No more reconnection attempts allowed",
                )
            )
            return

        # Attempt reconnection
        success = await self.reconnect_manager.handle_reconnect(
            connection_func=self.start_connection,
            error_handler=self.send_asr_error,
        )

        if success:
            self.ten_env.log_debug(
                "Reconnection attempt initiated successfully"
            )
        else:
            info = self.reconnect_manager.get_attempts_info()
            self.ten_env.log_debug(
                f"Reconnection attempt failed. Status: {info}"
            )

    async def _finalize_end(self) -> None:
        """Handle finalization end logic"""
        if self.last_finalize_timestamp != 0:
            timestamp = int(datetime.now().timestamp() * 1000)
            latency = timestamp - self.last_finalize_timestamp
            self.ten_env.log_debug(
                f"Xfyun ASR finalize end at {timestamp}, latency: {latency}ms"
            )
            self.last_finalize_timestamp = 0
            await self.send_asr_finalize_end()

    async def stop_connection(self) -> None:
        """Stop ASR connection — explicitly, not due to unexpected disconnect"""
        try:
            self._stopped_explicitly = True
            if self.recognition:
                await self.recognition.close()
                self.recognition = None
            self.ten_env.log_info("Xfyun ASR connection stopped (explicit)")

        except Exception as e:
            self.ten_env.log_error(f"Error stopping Xfyun ASR connection: {e}")

    @override
    def is_connected(self) -> bool:
        """Check connection status"""
        is_connected: bool = (
            self.recognition is not None and self.recognition.is_connected()
        )
        return is_connected

    @override
    def buffer_strategy(self) -> ASRBufferConfig:
        """Buffer strategy configuration"""
        return ASRBufferConfigModeDiscard()

    @override
    def input_audio_sample_rate(self) -> int:
        """Input audio sample rate"""
        assert self.config is not None
        return self.config.sample_rate

    @override
    async def send_audio(
        self, frame: AudioFrame, session_id: str | None
    ) -> bool:
        """Send audio data"""
        assert self.config is not None

        if not self.recognition:
            return False

        try:
            self.audio_frame_received += 1

            buf = frame.lock_buf()
            audio_data = bytes(buf)

            # Dump audio data
            if self.audio_dumper:
                await self.audio_dumper.push_bytes(audio_data)

            # Send audio data to recognition service (which handles buffering and timeline internally)
            await self.recognition.send_audio_frame(audio_data)

            self.audio_frame_sent_to_asr += 1

            # Log first frame to confirm pipeline started
            if self.audio_frame_sent_to_asr == 1:
                self.ten_env.log_info(
                    f"Xfyun ASR: first audio frame sent! "
                    f"audio_frame_received={self.audio_frame_received}, "
                    f"audio_frame_sent_to_asr={self.audio_frame_sent_to_asr}"
                )

            # Periodic metrics log (every 100 frames ~= 2s at 20ms/frame)
            if self.audio_frame_sent_to_asr % 100 == 0:
                self.ten_env.log_info(
                    f"Xfyun ASR metrics: audio_frame_received={self.audio_frame_received}, "
                    f"audio_frame_sent_to_asr={self.audio_frame_sent_to_asr} "
                    f"(actual_send={self.audio_frame_sent_to_asr}, >0={self.audio_frame_sent_to_asr > 0})"
                )

            frame.unlock_buf(buf)
            return True

        except Exception as e:
            self.ten_env.log_error(f"Error sending audio to Xfyun ASR: {e}")
            frame.unlock_buf(buf)
            return False
