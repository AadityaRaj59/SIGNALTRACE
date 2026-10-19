import asyncio
import httpx
from backend.app.core.envfile import anchor_sqlite_url, clean_secret, config_source
from backend.app.core.config import Settings, get_settings
from backend.app.services import llm_provider, search_provider

def test_empty_and_placeholder_secrets_are_unset():
    assert clean_secret('') is None
    assert clean_secret('your_api_key_here') is None
    assert clean_secret('real-secret-value')=='real-secret-value'

def test_relative_sqlite_url_is_anchored():
    url=anchor_sqlite_url('sqlite:///./signaltrace.db')
    from backend.app.core.envfile import PROJECT_ROOT
    # SQLite absolute URL slash counts differ between POSIX and Windows.
    assert url.startswith('sqlite:///') and url.endswith('/signaltrace.db')
    assert PROJECT_ROOT.as_posix() in url

def test_settings_ignore_empty_env_and_support_base_url(monkeypatch,tmp_path):
    env=tmp_path/'.env'; env.write_text('TAVILY_API_KEY=from-file\nOPENAI_BASE_URL=https://api.apinex.bond/v1\nOPENAI_MODEL=free/gemini-3.8-flash\n',encoding='utf-8')
    monkeypatch.setenv('TAVILY_API_KEY','')
    s=Settings(_env_file=env)
    assert s.tavily_api_key=='from-file'
    assert s.openai_base_url=='https://api.apinex.bond/v1'
    assert s.openai_model=='free/gemini-3.8-flash'

def test_apinex_base_url_is_used(monkeypatch):
    settings=get_settings()
    monkeypatch.setattr(settings,'openai_api_key','test-key')
    monkeypatch.setattr(settings,'openai_base_url','https://api.apinex.bond/v1')
    monkeypatch.setattr(settings,'openai_model','free/gemini-3.8-flash')
    calls=[]
    async def fake_post(url,headers,payload,timeout):
        calls.append((url,payload))
        return httpx.Response(200,json={'choices':[{'message':{'content':'```json\n{"ok":true}\n```'}}]})
    monkeypatch.setattr(llm_provider,'_post',fake_post)
    result=asyncio.run(llm_provider.json_completion('Return JSON',{'x':1}))
    assert result=={'ok':True}
    assert calls[0][0]=='https://api.apinex.bond/v1/chat/completions'
    assert calls[0][1]['model']=='free/gemini-3.8-flash'

def test_llm_http_errors_are_classified_without_secret_leak():
    response=httpx.Response(402,json={'error':{'message':'Insufficient balance'}})
    err=llm_provider._error_for_response(response,'top-secret')
    assert isinstance(err,llm_provider.LLMBillingError)
    response=httpx.Response(401,json={'error':{'message':'bad key top-secret'}})
    err=llm_provider._error_for_response(response,'top-secret')
    assert isinstance(err,llm_provider.LLMAuthError)
    assert 'top-secret' not in str(err)

def test_search_missing_key_is_not_silent(monkeypatch):
    settings=get_settings(); monkeypatch.setattr(settings,'tavily_api_key',None)
    try: asyncio.run(search_provider.search_public_sources('bank results'))
    except search_provider.SearchAuthError: pass
    else: raise AssertionError('Missing Tavily key should be a classified error, not empty results')

def test_diagnostics_endpoint_is_disabled_by_default():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    response=TestClient(app).get('/api/diagnostics')
    assert response.status_code==404

def test_env_path_is_independent_of_current_directory_and_can_be_overridden(monkeypatch,tmp_path):
    from backend.app.core.envfile import resolve_env_file, PROJECT_ROOT
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('SIGNALTRACE_ENV_FILE',raising=False)
    assert resolve_env_file()==(PROJECT_ROOT/'.env').resolve()
    override=tmp_path/'alternate.env'; override.write_text('TAVILY_API_KEY=private',encoding='utf-8')
    monkeypatch.setenv('SIGNALTRACE_ENV_FILE',str(override))
    assert resolve_env_file()==override.resolve()

def test_json_mode_falls_back_once_when_gateway_rejects_response_format(monkeypatch):
    settings=get_settings()
    monkeypatch.setattr(settings,'openai_api_key','test-key')
    monkeypatch.setattr(settings,'openai_base_url','https://api.apinex.bond/v1')
    monkeypatch.setattr(settings,'openai_model','free/gemini-3.8-flash')
    monkeypatch.setattr(settings,'llm_json_mode','auto')
    key=(settings.openai_base_url,settings.openai_model)
    llm_provider._json_mode_cache.pop(key,None)
    calls=[]
    async def fake_post(url,headers,payload,timeout):
        calls.append(dict(payload))
        if len(calls)==1:
            return httpx.Response(400,json={'error':{'message':'response_format json_object is not supported'}})
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
    monkeypatch.setattr(llm_provider,'_post',fake_post)
    assert asyncio.run(llm_provider.json_completion('Return JSON',{}))=={'ok':True}
    assert 'response_format' in calls[0]
    assert 'response_format' not in calls[1]
    assert llm_provider._json_mode_cache[key] is False
