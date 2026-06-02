import asyncio
import websockets
import datetime
import hashlib
import base64
import hmac
from urllib.parse import urlencode
import ssl
from wsgiref.handlers import format_date_time
from datetime import datetime
from time import mktime
import json
from .const import TIMEOUT_CODE
from websockets.protocol import State
from ten_ai_base.const import (
    LOG_CATEGORY_VENDOR,
)
from ten_ai_base.timeline import AudioTimeline
from ten_runtime import (
    AsyncTenEnv,
)
from .audio_buffer_manager import AudioBufferManager

STATUS_FIRST_FRAME = 0  # First frame identifier
STATUS_CONTINUE_FRAME = 1  # Middle frame identifier
STATUS_LAST_FRAME = 2  # Last frame identifier


class XfyunWSRecognitionCallback:
    """WebSocket Speech Recognition Callback Interface"""

    async def on_open(self):
        """Called when connection is established"""

    async def on_result(self, message_data):
        """
        Recognition result callback
        :param message_data: Complete recognition result data
        """

    async def on_error(self, error_msg, error_code=None):
        """Error callback"""

    async def on_close(self):
        """Called when connection is closed"""


class XfyunWSRecognition:
    """Async WebSocket-based speech recognition class"""

    def __init__(
        self,
        app_id: str,
        api_key: str,
        api_secret: str,
        audio_timeline: AudioTimeline,
        ten_env: AsyncTenEnv,
        config: dict,
        callback: XfyunWSRecognitionCallback,
    ):
        """
        Initialize WebSocket speech recognition
        :param app_id: Application ID
        :param api_key: API key
        :param api_secret: API secret
        :param audio_timeline: Audio timeline manager
        :param ten_env: Ten environment object for logging
        :param config: Configuration parameter dictionary, including the following optional parameters
        :param callback: Callback function instance
        """
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.audio_timeline = audio_timeline
        self.ten_env = ten_env

        # Set default configuration
        default_config = {
            "host": "iat.xf-yun.com",
            "path": "/v1",
            "domain": "slm",
            "language": "zh_cn",
            "accent": "mandarin",
            "dwa": "wpgs",
            "eos": 800,  # 对话场景建议值，官方示例 6000
            "samplerate": 16000,
            "channels": 1,
            "bit_depth": 16,
            "audio_encoding": "raw",
        }

        # Merge user configuration and default configuration
        if config is None:
            config = {}
        self.config = {**default_config, **config}

        self.host = self.config["host"]
        self.callback = callback

        # Common parameters
        self.common_args = {"app_id": self.app_id}

        # Business parameters - extract all business-related parameters from config
        self.business_args = {}

        # Required business parameters
        required_business_params = [
            "domain",
            "language",
            "accent",
            "language_name",
        ]
        for param in required_business_params:
            if param in self.config:
                self.business_args[param] = self.config[param]

        # Optional business parameters
        optional_business_params = [
            "dwa",
            "request_id",
            "eos",
            "pd",
            "res_id",
            "vto",
            "punc",
            "nunum",
            "pptaw",
            "dyhotws",
            "personalization",
            "seg_max",
            "seg_min",
            "seg_weight",
            "speex_size",
            "spkdia",
            "pgsnum",
            "vad_mdn",
            "language_type",
            "dhw",
            "dhw_mod",
            "feature_list",
            "rsgid",
            "rlang",
            "pgs_flash_freq",
        ]
        for param in optional_business_params:
            if param in self.config:
                self.business_args[param] = self.config[param]

        self._log_debug(f"Business arguments: {self.business_args}")

        self.websocket = None
        self.is_started = False
        self.is_first_frame = True
        self._message_task = None
        self._consumer_task = None

        self.audio_buffer = AudioBufferManager(
            ten_env=self.ten_env, threshold=1280
        )

    def _log_debug(self, message):
        """Unified logging method"""
        self.ten_env.log_debug(message)

    def _create_url(self):
        """Generate WebSocket connection URL for IAT 中英识别大模型 API"""
        path = self.config.get("path", "/v1")
        url = f"wss://{self.host}{path}"

        # Generate RFC1123 format timestamp
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        # Concatenate string for HMAC signature
        signature_origin = f"host: {self.host}\n"
        signature_origin += f"date: {date}\n"
        signature_origin += f"GET {path} HTTP/1.1"

        # Encrypt using hmac-sha256
        signature_sha = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        signature_sha = base64.b64encode(signature_sha).decode(encoding="utf-8")

        authorization_origin = f'api_key="{self.api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha}"'
        authorization = base64.b64encode(
            authorization_origin.encode("utf-8")
        ).decode(encoding="utf-8")

        # Combine authentication parameters into dictionary
        v = {"authorization": authorization, "host": self.host, "date": date}
        url = url + "?" + urlencode(v)
        return url

    async def _handle_message(self, message):
        """Handle WebSocket message (IAT 中英识别大模型 response format)

        Official response structure:
        {
          "header": {"code": 0, "message": "success", "sid": "xxx", "status": 0|1|2},
          "payload": {
            "result": {
              "text": "<base64>",  # decoded: {"sn":1, "ls":bool, "ws":[...], "pgs":"apd"|"rpl"}
              "seq": 1,
              "status": 0|1|2
            }
          }
        }

        Key fields for sentence final detection:
        - payload.result.text (base64 JSON) → inner.ls: true = sentence complete
        - header.status == 2: entire session complete
        - payload.result.status == 2: final result
        """
        try:
            message_data = json.loads(message)
            header = message_data.get("header", {})
            code = header.get("code")
            sid = header.get("sid")
            header_status = header.get("status", -1)

            # ALWAYS log response at INFO for debugging sentence boundary
            self.ten_env.log_info(
                f"[IAT RESP] code={code} sid={sid} header_status={header_status} "
                f"raw_first_200={str(message)[:200]}"
            )

            if code != 0:
                error_msg = header.get("message", "Unknown error")
                self.ten_env.log_error(
                    f"[IAT ERR] sid={sid} code={code} message={error_msg}"
                )
                if self.callback:
                    await self.callback.on_error(error_msg, code)
                return

            if not self.callback:
                return

            # Parse nested result
            payload = message_data.get("payload", {})
            result_payload = payload.get("result", {})
            text_b64 = result_payload.get("text", "")
            result_status = result_payload.get("status", -1)

            # Decode base64 inner JSON
            inner_data = {}
            if text_b64:
                try:
                    inner_json_str = base64.b64decode(text_b64).decode("utf-8")
                    inner_data = json.loads(inner_json_str)
                except Exception as e:
                    self.ten_env.log_warn(f"[IAT] Failed to decode result.text: {e}")

            # ---- Official final detection ----
            # 1. inner.ls == True  → 当前句子结束
            # 2. result_status == 2 → 整个识别会话结束
            # 3. header_status == 2 → 整个会话结束
            ls_value = inner_data.get("ls", False)
            is_sentence_end = (ls_value is True)
            is_session_end = (result_status == 2 or header_status == 2)

            # Map to legacy format
            inner_data["sub_end"] = is_sentence_end

            self.ten_env.log_info(
                f"[IAT RESULT] ls={ls_value} is_sentence_end={is_sentence_end} "
                f"result_status={result_status} header_status={header_status} "
                f"is_session_end={is_session_end} "
                f"sn={inner_data.get('sn', -1)} pgs={inner_data.get('pgs', 'none')} "
                f"text_len={len(inner_data.get('ws', []))}"
            )

            legacy_message = {
                "code": code,
                "sid": sid,
                "data": {
                    "status": result_status if result_status >= 0 else header_status,
                    "result": inner_data,
                },
            }
            await self.callback.on_result(legacy_message)

        except Exception as e:
            error_msg = f"Error processing message: {e}"
            self.ten_env.log_error(f"[IAT] {error_msg}")
            if self.callback:
                await self.callback.on_error(error_msg)

    async def _message_handler(self):
        """Handle incoming WebSocket messages"""
        if self.websocket is None:
            self._log_debug(
                "WebSocket connection not established, skipping message handler"
            )
            return

        try:
            async for message in self.websocket:
                await self._handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            self._log_debug("WebSocket connection closed")
        except Exception as e:
            error_msg = f"WebSocket message handler error: {e}"
            self._log_debug(f"### {error_msg} ###")
            if self.callback:
                await self.callback.on_error(error_msg)
        finally:
            self.is_started = False
            if self.callback:
                await self.callback.on_close()

    async def start(self, timeout=10):
        """
        Start speech recognition service
        :param timeout: Connection timeout in seconds, default 10 seconds
        """
        if self.is_started:
            self._log_debug("Recognition already started")
            return True

        try:
            ws_url = self._create_url()
            # Log masked URL (hide signature for security)
            masked_url = ws_url.split("?")[0] + "?authorization=***&host=" + self.host + "&date=***"
            self.ten_env.log_info(f"Xfyun ASR connecting to: {masked_url}")
            self._log_debug(f"Full URL: {ws_url}")

            # Create SSL context that doesn't verify certificates (similar to original)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            # Connect to WebSocket with timeout
            self.websocket = await websockets.connect(
                ws_url, ssl=ssl_context, open_timeout=timeout
            )

            self.ten_env.log_info("Xfyun ASR WebSocket connected successfully")
            self._log_debug("### WebSocket opened ###")
            self.is_first_frame = True
            self.is_started = True

            # Start message handler task
            self._message_task = asyncio.create_task(self._message_handler())

            # Start consumer task for sending audio from buffer
            self._consumer_task = asyncio.create_task(self._consume_and_send())

            if self.callback:
                await self.callback.on_open()

            self._log_debug("Recognition started successfully")
            return True

        except asyncio.TimeoutError:
            path = self.config.get("path", "/v1")
            error_msg = (
                f"Connection timeout after {timeout}s to wss://{self.host}{path}. "
                f"Check: 1) Network reachable? 2) App ID has '中英识别大模型' service enabled? "
                f"3) Credentials correct?"
            )
            self.ten_env.log_error(error_msg)
            self._log_debug(f"Failed to start recognition: {error_msg}")
            if self.callback:
                await self.callback.on_error(error_msg, TIMEOUT_CODE)
            return False
        except Exception as e:
            error_msg = f"Failed to start recognition: {e}"
            self.ten_env.log_error(error_msg)
            self._log_debug(error_msg)
            if self.callback:
                await self.callback.on_error(error_msg)
            return False

    async def send_audio_frame(self, audio_data):
        """
        Producer side: push audio bytes into buffer.
        :param audio_data: Audio data (bytes)
        """
        try:
            await self.audio_buffer.push_audio(audio_data)
        except Exception as e:
            self._log_debug(f"Failed to enqueue audio frame: {e}")
            if self.callback:
                await self.callback.on_error(
                    f"Failed to enqueue audio frame: {e}"
                )

    async def _consume_and_send(self):
        """Consumer loop: pull chunks from buffer and send over websocket (IAT protocol)."""
        sample_rate = self.config.get("samplerate", 16000)
        channels = self.config.get("channels", 1)
        bit_depth = self.config.get("bit_depth", 16)
        audio_encoding = self.config.get("audio_encoding", "raw")
        seq = 0
        try:
            while True:
                chunk = await self.audio_buffer.pull_chunk()
                if chunk == b"":
                    # EOF after close and buffer drained
                    break

                seq += 1
                audio_b64 = str(base64.b64encode(chunk), "utf-8")

                # Build IAT protocol payload
                if self.is_first_frame:
                    # First frame: includes parameter.iat configuration
                    d = {
                        "header": {
                            "app_id": self.app_id,
                            "status": STATUS_FIRST_FRAME,
                        },
                        "parameter": {
                            "iat": {
                                "domain": self.config.get("domain", "slm"),
                                "language": self.config.get("language", "zh_cn"),
                                "accent": self.config.get("accent", "mandarin"),
                                "eos": self.config.get("eos", 6000),
                                "dwa": self.config.get("dwa", "wpgs"),
                                "result": {
                                    "encoding": "utf8",
                                    "compress": "raw",
                                    "format": "json",
                                },
                            }
                        },
                        "payload": {
                            "audio": {
                                "encoding": audio_encoding,
                                "sample_rate": sample_rate,
                                "channels": channels,
                                "bit_depth": bit_depth,
                                "seq": seq,
                                "status": STATUS_FIRST_FRAME,
                                "audio": audio_b64,
                            }
                        },
                    }
                    self.is_first_frame = False
                else:
                    # Subsequent frames: header + payload only
                    d = {
                        "header": {
                            "app_id": self.app_id,
                            "status": STATUS_CONTINUE_FRAME,
                        },
                        "payload": {
                            "audio": {
                                "encoding": audio_encoding,
                                "sample_rate": sample_rate,
                                "channels": channels,
                                "bit_depth": bit_depth,
                                "seq": seq,
                                "status": STATUS_CONTINUE_FRAME,
                                "audio": audio_b64,
                            }
                        },
                    }

                # Update timeline based on actual sent bytes
                duration_ms = int(len(chunk) / (int(sample_rate) / 1000 * 2))
                self.audio_timeline.add_user_audio(duration_ms)

                if self.websocket is None:
                    break
                await self.websocket.send(json.dumps(d))
        except websockets.exceptions.ConnectionClosed:
            self._log_debug(
                "WebSocket connection closed while consuming audio frames"
            )
            self.is_started = False
        except Exception as e:
            self._log_debug(f"Consumer loop error: {e}")
            if self.callback:
                await self.callback.on_error(f"Consumer loop error: {e}")

    async def stop(self):
        """
        Stop speech recognition
        """
        if not self.is_connected():
            self._log_debug("Recognition not started")
            return

        try:
            # Close producer buffer so consumer drains remaining bytes and exits
            self.audio_buffer.close()
            if self._consumer_task:
                try:
                    await self._consumer_task
                except asyncio.CancelledError:
                    pass

            # Send end identifier (IAT protocol)
            d = {
                "header": {
                    "app_id": self.app_id,
                    "status": STATUS_LAST_FRAME,
                },
                "payload": {
                    "audio": {
                        "encoding": self.config.get("audio_encoding", "raw"),
                        "sample_rate": self.config.get("samplerate", 16000),
                        "channels": self.config.get("channels", 1),
                        "bit_depth": self.config.get("bit_depth", 16),
                        "seq": 999999,
                        "status": STATUS_LAST_FRAME,
                        "audio": "",
                    }
                },
            }
            ws = self.websocket
            if ws is not None:
                await ws.send(json.dumps(d))
            self.is_started = False
            self.ten_env.log_info(
                f"vendor_cmd: ${json.dumps(d)}",
                category=LOG_CATEGORY_VENDOR,
            )

        except websockets.exceptions.ConnectionClosed:
            self._log_debug("WebSocket connection already closed")
        except Exception as e:
            self._log_debug(f"Failed to stop recognition: {e}")
            if self.callback:
                await self.callback.on_error(f"Failed to stop recognition: {e}")

    async def stop_consumer(self):
        """Stop consumer task"""
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

    async def close(self):
        """Close WebSocket connection"""
        if self.websocket:
            try:
                if self.websocket.state == State.OPEN:
                    await self.websocket.close()
            except Exception as e:
                self._log_debug(f"Error closing websocket: {e}")

        await self.stop_consumer()

        if self._message_task and not self._message_task.done():
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass

        self.is_started = False
        self.is_first_frame = True
        self._log_debug("WebSocket connection closed")

    def is_connected(self) -> bool:
        """Check if WebSocket connection is established"""
        if self.websocket is None:
            return False

        # Check if websocket is still open by checking the state
        try:
            # For websockets library, we can check the state attribute
            if hasattr(self.websocket, "state"):
                return self.is_started and self.websocket.state == State.OPEN
            # Fallback: just check if websocket exists and is_started is True
            else:
                return self.is_started
        except Exception:
            # If any error occurs, assume disconnected
            return False
