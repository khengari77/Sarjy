"""Native provider construction; only called after backend capability validation."""
import openai as openai_sdk
import httpx
from stream_completion import ObservedTransport
from livekit.plugins import deepgram, elevenlabs, openai


class OwnedClientMixin:
    """Close the SDK client when the application factory created it."""
    def __init__(self, *, client, owns_client=False, **kwargs):
        super().__init__(client=client, **kwargs)
        self.application_client = client if owns_client else None

    async def aclose(self):
        try:
            await super().aclose()
        finally:
            if self.application_client is not None:
                await self.application_client.close()


class OwnedDeepSeekLLM(OwnedClientMixin, openai.LLM):
    pass


class OwnedOpenAIResponsesLLM(OwnedClientMixin, openai.responses.LLM):
    pass


def build_llm(config: dict, credentials: dict[str, str], *, client=None):
    choice = config['llm']
    if choice['provider'] == 'openai_direct' and choice['model'] == 'gpt-6-luna':
        llm = OwnedOpenAIResponsesLLM(
            owns_client=client is None,
            model='gpt-6-luna',
            client=client or openai_sdk.AsyncOpenAI(api_key=credentials['OPENAI_API_KEY'], max_retries=0),
            use_websocket=False, reasoning={'effort':'none'}, service_tier='default',
            store=False,
            prompt_cache_breakpoints=False,
        )
    elif choice['provider'] == 'deepseek_direct' and choice['model'] == 'deepseek-flash':
        owns_client = client is None
        sdk_client = client or openai_sdk.AsyncOpenAI(
            api_key=credentials['DEEPSEEK_API_KEY'], base_url='https://api.deepseek.com/v1',
            max_retries=0, http_client=httpx.AsyncClient(transport=ObservedTransport()))
        llm = OwnedDeepSeekLLM(
            owns_client=owns_client, model=choice['model'], api_key=credentials['DEEPSEEK_API_KEY'],
            base_url='https://api.deepseek.com/v1', client=sdk_client, max_retries=0,
            extra_body={'thinking': {'type': 'disabled'}})
    else:
        raise ValueError('Unsupported LLM configuration')
    return llm


def build_fallback_llm(credentials: dict[str, str], *, client=None):
    """Optional OpenAI fallback; absent without an approved OpenAI key."""
    if not credentials.get('OPENAI_API_KEY'):
        return None
    return build_llm(
        {'llm': {'provider': 'openai_direct', 'model': 'gpt-6-luna'}},
        credentials,
        client=client,
    )


def build_providers(config: dict, credentials: dict[str, str]):
    primary = config['llm']['provider']
    return {
        'stt': deepgram.STT(model=config['stt']['model'], language=config['stt']['language'], api_key=credentials['DEEPGRAM_API_KEY']),
        'llm': build_llm(config, credentials),
        'tts': elevenlabs.TTS(model=config['tts']['model'], voice_id=config['tts']['voice_id'], language=config['tts']['language'], api_key=credentials['ELEVEN_API_KEY'], enable_logging=False),
        'fallback_llm': build_fallback_llm(credentials) if primary == 'deepseek_direct' else None,
    }
