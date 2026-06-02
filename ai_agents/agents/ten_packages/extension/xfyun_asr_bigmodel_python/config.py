from typing import Dict, Any
from pydantic import BaseModel, Field
from ten_ai_base.utils import encrypt


class XfyunASRConfig(BaseModel):
    """Xfyun ASR Bigmodel Configuration (IAT 中英识别大模型)

    Official API: wss://iat.xf-yun.com/v1
    Documentation: https://www.xfyun.cn/doc/spark/spark_zh_iat.html
    """

    # Provider identification
    provider: str = "xfyun_iat_bigmodel"

    # Credentials (from env vars)
    app_id: str = ""
    api_key: str = ""
    api_secret: str = ""

    # IAT API: host / path / url
    host: str = "iat.xf-yun.com"
    path: str = "/v1"
    url: str = "wss://iat.xf-yun.com/v1"

    # IAT API: domain / language / accent
    domain: str = "slm"
    language: str = "zh_cn"
    accent: str = "mandarin"

    # Audio parameters
    sample_rate: int = 16000
    channels: int = 1
    bit_depth: int = 16
    audio_encoding: str = "raw"  # "raw" for PCM, "lame" for MP3

    # IAT control parameters
    dwa: str = "wpgs"
    eos: int = 800  # 静音多少 ms 后服务端自动断句（官方示例 6000，对话建议 800-1500）

    # Result parameters
    result_encoding: str = "utf8"
    result_compress: str = "raw"
    result_format: str = "json"

    # Limits
    max_audio_seconds: int = 60

    # Dump / debug
    dump: bool = False
    dump_path: str = "/tmp"

    # Legacy fields (kept for backward compatibility, NOT used by IAT)
    language_name: str = "zh"
    aue: str = "raw"
    finalize_mode: str = "disconnect"
    mute_pkg_duration_ms: int = 1000
    dhw: str = ""
    punc: int = 1
    nunum: int = 1
    vto: int = 3000

    params: Dict[str, Any] = Field(default_factory=dict)

    def update(self, params: Dict[str, Any]) -> None:
        """Update configuration with additional parameters."""
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def enforce_iat_bigmodel(self) -> None:
        """强制覆盖 IAT 中英识别大模型的关键字段，防止 params 中残留的 IST
        配置（如 language=mix, domain=ist_cbm_mix）反向污染正确配置。
        """
        if self.provider != "xfyun_iat_bigmodel":
            return

        # 硬锁定 IAT API 核心参数，不允许任何来源覆盖
        self.host = "iat.xf-yun.com"
        self.path = "/v1"
        self.url = "wss://iat.xf-yun.com/v1"
        self.domain = "slm"
        self.language = "zh_cn"
        self.accent = "mandarin"

    def to_json(self, sensitive_handling: bool = False) -> str:
        """Convert config to JSON string with optional sensitive data handling."""
        config_dict = self.model_dump()
        if sensitive_handling:
            if self.api_key:
                config_dict["api_key"] = encrypt(config_dict["api_key"])
            if self.api_secret:
                config_dict["api_secret"] = encrypt(config_dict["api_secret"])
            if self.app_id:
                config_dict["app_id"] = encrypt(config_dict["app_id"])
        if config_dict["params"]:
            for key, value in config_dict["params"].items():
                if key == "api_key":
                    config_dict["params"][key] = encrypt(value)
                if key == "api_secret":
                    config_dict["params"][key] = encrypt(value)
                if key == "app_id":
                    config_dict["params"][key] = encrypt(value)
        return str(config_dict)

    @property
    def normalized_language(self):
        if self.language_name == "zh":
            return "zh-CN"
        elif self.language_name == "en":
            return "en-US"
        elif self.language_name == "ja":
            return "ja-JP"
        elif self.language_name == "ko":
            return "ko-KR"
        elif self.language_name == "ru":
            return "ru-RU"
        elif self.language_name == "fr":
            return "fr-FR"
        elif self.language_name == "es":
            return "es-ES"
        elif self.language_name == "ar":
            return "ar-AE"
        else:
            return self.language_name
