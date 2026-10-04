import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import ai_tasks as tasks
import ai_runtime as rt
import ai_grounding as g
from ai_providers import ProviderUnavailable,call_google,call_external
from web.grounding import complete,register


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=patch.object(tasks,'DB_FILE',Path(self.tmp.name)/'test.db');self.db.start()
        self.env=patch.dict(os.environ,{'GEMINI_API_KEY':'synthetic','AI_MAPS_RPD':'500','AI_SEARCH_RPD':'1500','AI_SEARCH_25_RPD':'500','AI_SEARCH_DEFAULT_RPD':'1500'});self.env.start()
        rt.initialize()
        rt.heartbeat('pc','local',list(rt.TABLES));rt.heartbeat('api','groq',list(rt.TABLES))
        self.source={'id':12,'chat_id':-42,'user_id':123,'request_message_id':5,'message_text':'Музей в Париже','trigger_reason':'maps'}
        self.plan={'queries':['Find museums in Paris'],'needed_facts':['address'],'answer_strategy':'answer','location':'Париже','needs_clarification':False,'clarification':''}
        self.result={'status':'grounded','text':'The museum opens at nine [1].','tool':'maps','provider':'google','model':'synthetic','sources':[{'title':'Museum','url':'https://maps.google.com/maps?cid=123','kind':'maps'}],'supports':[]}

    def tearDown(self):self.env.stop();self.db.stop();self.tmp.cleanup()

    def claim(self,kind='maps_grounding'):
        rt.set_mode(-42,'api')
        ident=g.create_from_plan(self.source,self.plan) if kind=='maps_grounding' else g.create_task(self.source,kind,{},'Question')
        return rt.claim('tasks','api')

    def test_schema_is_repeatable_on_existing_database(self):
        rt.initialize();rt.initialize()
        with closing(rt.connect()) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_tool_usage').fetchone()[0],0)

    def test_model_and_tool_reservations_are_atomic_and_shared(self):
        with patch.dict(os.environ,{'AI_MAPS_RPD':'1'}):
            first=rt.reserve(g.MAPS_MODELS[0],10,20,tool='maps');self.assertIsNotNone(first)
            self.assertIsNone(rt.reserve(g.MAPS_MODELS[1],10,20,tool='maps'))
            self.assertIsNotNone(rt.reserve(g.MAPS_MODELS[1],10,20))
            g.settle_tool(first,False);self.assertIsNotNone(rt.reserve(g.MAPS_MODELS[1],10,20,tool='maps'))

    def test_search_family_limit_does_not_reset_with_model(self):
        with patch.dict(os.environ,{'AI_SEARCH_25_RPD':'1'}):
            self.assertIsNotNone(rt.reserve('gemini-2.5-flash',10,20,tool='search'))
            self.assertIsNone(rt.reserve('gemini-2.5-flash-lite',10,20,tool='search'))
            self.assertGreater(g.tool_wait('search',g.MAPS_MODELS[0]),0)

    def test_default_search_shares_family_cap_and_global_cap(self):
        with patch.dict(os.environ,{'AI_SEARCH_DEFAULT_RPD':'1'}):
            self.assertIsNotNone(rt.reserve(g.SEARCH_MODELS[0],10,20,tool='search'))
            self.assertIsNone(rt.reserve(g.SEARCH_MODELS[1],10,20,tool='search'))
            self.assertIsNotNone(rt.reserve('gemini-2.5-flash',10,20,tool='search'))
            self.assertIsNotNone(rt.reserve(g.SEARCH_MODELS[1],10,20))
        with patch.dict(os.environ,{'AI_SEARCH_RPD':'2'}):
            self.assertIsNone(rt.reserve('gemini-2.5-flash-lite',10,20,tool='search'))

    def test_default_tool_cooldown_does_not_block_generation_or_other_family(self):
        g.cool_tool('search',g.SEARCH_MODELS[0],600,shared=True)
        self.assertGreater(g.tool_wait('search',g.SEARCH_MODELS[1]),0)
        self.assertEqual(g.tool_wait('search','gemini-2.5-flash'),0)
        self.assertTrue(rt.model_ready(g.SEARCH_MODELS[0]))
        self.assertEqual(g.tool_wait('maps',g.SEARCH_MODELS[0]),0)

    def test_search_chain_uses_confirmed_default_models_before_legacy(self):
        task=self.claim('web_grounding');result={**self.result,'tool':'search'}
        with patch('ai_providers.call_google',side_effect=[ProviderUnavailable('quota',600,False),(json.dumps(result),{'model':g.SEARCH_MODELS[1]})]) as call:
            output,meta=call_external(task,30)
        self.assertEqual(call.call_args_list[0].args[2],'gemini-robotics-er-2-preview')
        self.assertEqual(meta['model'],'gemma-4-31b-it')
        self.assertEqual(json.loads(output)['status'],'grounded')
        self.assertEqual(rt.model_limits('gemini-robotics-er-2-preview')['RPD'],20)

    def test_tool_failure_does_not_disable_ordinary_model(self):
        g.cool_tool('maps',g.MAPS_MODELS[0],86400)
        self.assertTrue(rt.model_ready(g.MAPS_MODELS[0]));self.assertGreater(g.tool_wait('maps',g.MAPS_MODELS[0]),0)
        self.assertEqual(g.tool_wait('maps',g.MAPS_MODELS[1]),0)

    def test_primary_failure_uses_secondary(self):
        task=self.claim()
        with patch('ai_providers.call_google',side_effect=[ProviderUnavailable('failed',300,False),(json.dumps(self.result),{'model':g.MAPS_MODELS[1]})]) as call:
            output,meta=call_external(task,30)
            self.assertEqual(meta['model'],g.MAPS_MODELS[1]);self.assertEqual(call.call_args_list[0].kwargs['tool'],'maps')

    def test_daily_exhaustion_falls_back_and_minute_exhaustion_waits(self):
        task=self.claim()
        with patch('ai_providers.call_google',side_effect=ProviderUnavailable('daily',1000,False)):
            self.assertEqual(json.loads(g.call(task,30)[0])['status'],'fallback')
        with patch('ai_providers.call_google',side_effect=ProviderUnavailable('minute',45,False)):
            with self.assertRaises(ProviderUnavailable) as error:g.call(task,30)
            self.assertEqual(error.exception.retry_after,45)

    def test_google_payload_and_unicode_citation(self):
        task=self.claim();text='Музей';candidate={'finishReason':'STOP','content':{'parts':[{'text':text}]},'groundingMetadata':{'groundingChunks':[{'maps':{'uri':'https://maps.google.com/maps?cid=123','title':'Museum'}}],'groundingSupports':[{'segment':{'endIndex':len(text.encode()),'text':text},'groundingChunkIndices':[0]}]}}
        response=Mock(ok=True,status_code=200);response.json.side_effect=[{'totalTokens':20},{'candidates':[candidate],'usageMetadata':{'promptTokenCount':20}},{'candidates':[candidate],'usageMetadata':{'promptTokenCount':20}}]
        with patch('ai_http.post',return_value=response) as post:
            output,meta=call_google(task,30,g.MAPS_MODELS[0],tool='maps')
        self.assertEqual(json.loads(output)['text'],'Музей [1]')
        self.assertEqual(post.call_args.kwargs['json']['tools'],[{'googleMaps':{}}])
        self.assertNotIn('thinkingBudget',json.dumps(post.call_args.kwargs['json']))

    def test_missing_sources_never_becomes_grounded_answer(self):
        task=self.claim();response=Mock(ok=True,status_code=200)
        response.json.side_effect=[{'totalTokens':20},{'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'Unverified address'}]}}]},{'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'Unverified address'}]}}]}]
        with patch('ai_http.post',return_value=response):
            with self.assertRaises(ProviderUnavailable):call_google(task,30,g.MAPS_MODELS[0],tool='maps')

    def test_gemma_search_uses_supported_thinking_level_and_requires_sources(self):
        task=self.claim('web_grounding')
        candidate={'finishReason':'STOP','content':{'parts':[{'text':'Новость'}]},'groundingMetadata':{'groundingChunks':[{'web':{'uri':'https://example.com/news','title':'News'}}]}}
        response=Mock(ok=True,status_code=200)
        response.json.side_effect=[{'totalTokens':20},{'candidates':[candidate],'usageMetadata':{'promptTokenCount':20}},{'candidates':[candidate],'usageMetadata':{'promptTokenCount':20}}]
        with patch('ai_http.post',return_value=response) as post:
            output,meta=call_google(task,30,'gemma-4-31b-it',tool='search')
        self.assertEqual(json.loads(output)['status'],'grounded')
        self.assertEqual(post.call_args.kwargs['json']['generationConfig']['thinkingConfig'],{'thinkingLevel':'minimal'})
        self.assertEqual(post.call_args.kwargs['json']['tools'],[{'googleSearch':{}}])

    def test_maps_planning_and_nearby_clarification(self):
        with patch.dict(os.environ,{'AI_CREATOR_USER_ID':'123'}):
            task=self.claim()
            self.assertNotIn(tasks.CREATOR_POLICY_MARKER,task['prompt'])
            self.assertIn('creator',task['system_instruction'])
        self.assertEqual(tasks.validate_type_check_output('maps'),'maps')
        self.assertIn('needs_clarification',tasks.build_search_plan_prompt(message_text='Кафе рядом',trigger_reason='maps'))
        source=dict(self.source,message_text='Кафе рядом');plan=dict(self.plan,location='Екатеринбург')
        self.assertTrue(g.checked_plan(source,plan)['needs_clarification'])
        explicit=dict(self.source,message_text='Кафе рядом в Париже');self.assertFalse(g.checked_plan(explicit,dict(self.plan))['needs_clarification'])
        coordinates=dict(self.source,message_text='Кафе рядом с координатами 56.8389, 60.6057')
        checked=g.checked_plan(coordinates,dict(self.plan,queries=['Find cafes nearby'],location='',needs_clarification=True))
        self.assertFalse(checked['needs_clarification']);self.assertEqual(checked['coordinates'],{'latitude':56.8389,'longitude':60.6057})
        invalid=dict(self.source,message_text='Кафе рядом с координатами 90.5, 200.0')
        self.assertTrue(g.checked_plan(invalid,dict(self.plan,queries=['Find cafes']))['needs_clarification'])

    def test_api_grounding_preferred_and_local_mode_never_calls_google(self):
        rt.set_mode(-42,'local_api');g.create_from_plan(self.source,self.plan)
        self.assertIsNone(rt.claim('tasks','pc'));self.assertEqual(rt.claim('tasks','api')['task_type'],'maps_grounding')
        rt.set_mode(-42,'local')
        from ai_providers import call_local
        with patch('ai_http.post') as post:
            output,_=call_local({'task_type':'maps_grounding'},30)
            self.assertEqual(json.loads(output)['status'],'fallback');post.assert_not_called()

    def test_translation_is_separate_and_result_survives_retry(self):
        task=self.claim();send=Mock(return_value=999)
        result=complete(task,SimpleNamespace(output=json.dumps(self.result)),send,Mock())
        self.assertEqual(result['status'],'done');send.assert_not_called()
        rt.initialize();translation=rt.claim('tasks','api');self.assertEqual(translation['task_type'],'maps_translation')
        result=complete(translation,SimpleNamespace(output='Музей открывается в девять [1].'),send,Mock())
        self.assertEqual(result['status'],'done');self.assertIn('Google Maps',send.call_args.args[1]);self.assertNotIn('Museum',send.call_args.args[1]);self.assertIn('Источники',send.call_args.args[1])
        self.assertEqual(json.loads(g.get(task['id'])['result_json'])['text'],self.result['text'])

    def test_translation_losing_markers_retries_without_map_call(self):
        task=self.claim();complete(task,SimpleNamespace(output=json.dumps(self.result)),Mock(),Mock());translation=rt.claim('tasks','api')
        send=Mock();result=complete(translation,SimpleNamespace(output='Музей открывается в девять.'),send,Mock())
        self.assertEqual(result['status'],'retry');send.assert_not_called();self.assertIsNotNone(g.get(task['id']))

    def test_fallback_uses_existing_search_context_and_keeps_disclaimer(self):
        task=self.claim();create=Mock(return_value=1234)
        with patch('web_search.build_web_context',return_value='Synthetic search context'):
            result=complete(task,SimpleNamespace(output=json.dumps({'status':'fallback','tool':'maps'})),Mock(),create)
        self.assertEqual(result['final_task_id'],1234);self.assertIn('Google Maps недоступен',create.call_args.kwargs['web_context'])

    def test_search_response_queues_beside_other_response_without_duplicates(self):
        rt.set_mode(-42,'api')
        base=dict(chat_id=-42,requester_user_id=123,request_message_id=1,message_text='Question',requester_name='Synthetic',requester_nick=None,trigger_reason='mention')
        with patch.object(tasks,'get_response_short_memory',return_value=[]),patch.object(tasks,'get_response_long_memory',return_value=[]),patch.object(tasks,'get_latest_profile_json',return_value=None):
            self.assertIsNotNone(tasks.create_response_task(**base))
            search=dict(base,request_message_id=2,trigger_reason='grounding_fallback')
            self.assertIsNotNone(tasks.create_response_task(**search))
            self.assertIsNone(tasks.create_response_task(**search))
            self.assertIsNotNone(tasks.create_response_task(**dict(base,request_message_id=3)))
            self.assertIsNone(tasks.create_response_task(**dict(base,request_message_id=4)))

    def test_unsafe_urls_rejected_and_markup_escaped(self):
        for url in ('javascript:alert(1)','https://user:pass@example.test','http://example.test','https://example.test/\n'):
            self.assertIsNone(g.safe_url(url))
        result=dict(self.result,text='<img src=x onerror=alert(1)> [1]')
        html=g.telegram_html(result);self.assertNotIn('<img',html);self.assertIn('&lt;img',html)
        result['sources']=[{'title':'bad','url':'javascript:alert(1)'}]
        with self.assertRaises(ValueError):g.validate_result(json.dumps(result))

    def test_telegram_full_reply_has_no_citation_clutter_or_source_list(self):
        result=dict(self.result,text='### Новости\n\n**'+('Подробности новости. '*500)+'** [1.5, 1.9] [1].')
        html=g.telegram_html(result,'https://example.test/view')
        self.assertIn('Подробности новости.',html);self.assertGreater(len(html),3900)
        self.assertNotIn('[1]',html);self.assertNotIn('[1.5',html)
        self.assertNotIn('maps?cid=',html);self.assertIn('>Источники</a>',html)

    def test_result_page_is_expiring_and_isolates_suggestions(self):
        task=self.claim();result=dict(self.result,tool='search',suggestions_html='<script>top.hacked=1</script><a href="https://example.test">Search suggestion</a>')
        g.save(task['id'],result);row=g.get(task['id']);app=FastAPI();register(app);client=TestClient(app)
        self.assertEqual(client.get('/ai_sources/guess').status_code,404)
        response=client.get('/ai_sources/'+row['token']);self.assertEqual(response.status_code,200)
        self.assertIn('sandbox=',response.text);self.assertNotIn('allow-scripts',response.text);self.assertEqual(response.headers['cache-control'],'private, no-store')
        with closing(rt.connect()) as conn:conn.execute("UPDATE ai_grounding_results SET expires='2000-01-01'");conn.commit()
        self.assertEqual(client.get('/ai_sources/'+row['token']).status_code,404)
