"""Native provider construction; only called after backend capability validation."""
import openai as openai_sdk
from livekit.plugins import deepgram, elevenlabs, openai


def build_llm(config: dict, credentials: dict[str, str], *, client=None):
    choice = config['llm']
    if choice['provider'] == 'openai_direct' and choice['model'] == 'gpt-6-luna':
        llm = openai.responses.LLM(
            model='gpt-6-luna',
            client=client or openai_sdk.AsyncOpenAI(api_key=credentials['OPENAI_API_KEY'], max_retries=0),
            use_websocket=False, reasoning={'effort':'none'}, service_tier='default',
            store=False,
            prompt_cache_breakpoints=False,
        )
    elif choice['provider'] == 'deepseek_direct' and choice['model'] == 'deepseek-flash':
        llm = openai.LLM(model=choice['model'], api_key=credentials['DEEPSEEK_API_KEY'], base_url='https://api.deepseek.com/v1', client=client, max_retries=0, extra_body={'thinking': {'type': 'disabled'}})
    else:
        raise ValueError('Unsupported LLM configuration')
    return llm


def build_providers(config: dict, credentials: dict[str, str]):
    return {
        'stt': deepgram.STT(model=config['stt']['model'], language=config['stt']['language'], api_key=credentials['DEEPGRAM_API_KEY']),
        'llm': build_llm(config, credentials),
        'tts': elevenlabs.TTS(model=config['tts']['model'], voice_id=config['tts']['voice_id'], language=config['tts']['language'], api_key=credentials['ELEVEN_API_KEY'], enable_logging=False),
    }
