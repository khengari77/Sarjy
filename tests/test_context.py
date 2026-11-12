from app.domain.context import ContextRecord, compile_context
from app.domain.pipeline import PipelineConfig, validate_pipeline


def test_exclusions_preserve_system_and_determinism():
    records = (ContextRecord("s", "system", "Rules"), ContextRecord("m", "user", "Riyadh"))
    result = compile_context(records, 3, frozenset({"s", "m"}))
    assert result["included_record_ids"] == ["s"]
    assert result == compile_context(records, 3, frozenset({"s", "m"}))

def test_arabic_deepgram_tts_rejected():
    config = PipelineConfig(stt={"provider":"deepgram_direct","model":"nova-3"}, llm={"provider":"deepseek_direct","model":"deepseek-flash"}, tts={"provider":"deepgram_direct","model":"aura-2","voice_id":"x"})
    assert {"role":"tts","code":"UNSUPPORTED_LANGUAGE"} in validate_pipeline(config, set())

def test_temporary_openai_default_and_independent_deepseek_key_validation():
    from app.domain.pipeline import PRESET
    assert PRESET['llm'] == ('openai_direct','gpt-6-luna')
    base={'stt':{'provider':'deepgram_direct','model':'nova-3'},'tts':{'provider':'elevenlabs_direct','model':'eleven_flash_v2_5','voice_id':'voice'}}
    config=PipelineConfig(**base,llm={'provider':'openai_direct','model':'gpt-6-luna'})
    keys={'DEEPGRAM_API_KEY','ELEVEN_API_KEY','OPENAI_API_KEY'}
    assert validate_pipeline(config,keys) == []
    # Frozen configs: a provider change produces a new value, never a mutation.
    swapped=PipelineConfig(**base,llm={'provider':'deepseek_direct','model':'deepseek-flash'})
    assert config.llm.provider == 'openai_direct'
    assert {'role':'llm','code':'MISSING_CREDENTIAL'} in validate_pipeline(swapped,keys)
    assert validate_pipeline(swapped,keys|{'DEEPSEEK_API_KEY'}) == []
