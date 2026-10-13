import json
import time
import unittest
from contextlib import closing
from unittest.mock import patch
import test_rag as fixtures
import mechanics
import mechanics_search
import rag_repository as repo
import ai_tasks
import rag_search


class QuestionTests(unittest.TestCase):
    setUp=fixtures.RagTests.setUp
    tearDown=fixtures.RagTests.tearDown

    def test_target_sections_without_embeddings(self):
        mechanics.sync()
        cases=[('Как получить цепня','infection'),('Как заразиться цепнем','infection'),
            ('Как происходит групповая мастурбация в чате','group'),('Как устроен групповой дроч','group'),
            ('От чего зависит процент по вкладу в сит-банке','deposit-rate'),('Почему меняется ставка вклада','deposit-rate')]
        for question,section in cases:
            with self.subTest(question=question):
                rows=mechanics_search.lexical(question)
                self.assertTrue(rows)
                self.assertIn(section,[r['section_id'] for _,r in rows[:3]])

    def test_fallback_and_context_budget(self):
        mechanics.sync()
        task=dict(id=999,task_type='mechanics',chat_id=-42,request_message_id=100,payload_json=json.dumps({'user_query':'Как получить цепня'}))
        with patch('mechanics_search.refine',return_value=(None,'ProviderUnavailable')):
            result=mechanics.retrieve(task,time.monotonic()+15)
        self.assertEqual(result['fallback_reason'],'ProviderUnavailable')
        self.assertEqual(result['fragments'][0]['section_id'],'infection')
        self.assertLessEqual(len(mechanics.context(result['fragments'])),8000)
        self.assertLessEqual(repo.tokens(mechanics.context(result['fragments'])),2000)

    def test_invalid_refinement_and_single_call(self):
        task={'id':999,'chat_id':-42}
        for output in ('not json','{}','{"topic":"x","queries":[3],"needed_facts":["x"]}'):
            with patch('ai_providers.call_external_once',return_value=(output,{})) as call:
                value,error=mechanics_search.refine(task,'q',[],time.monotonic()+15)
                self.assertIsNone(value);self.assertIsNotNone(error);call.assert_called_once()

    def test_valid_refinement_and_api_failure(self):
        task={'id':999,'chat_id':-42}
        output=json.dumps(dict(topic='cepen',queries=['заражение цепнем'],needed_facts=['способ получения']))
        with patch('ai_providers.call_external_once',return_value=(output,{})) as call:
            value,error=mechanics_search.refine(task,'q',[],time.monotonic()+15)
            self.assertIsNone(error);self.assertEqual(value['topic'],'cepen')
            self.assertLessEqual(call.call_args.args[1],8)
        with patch('ai_providers.call_external_once',return_value=('```json\n'+output+'\n```',{})):
            self.assertEqual(mechanics_search.refine(task,'q',[],time.monotonic()+15)[0]['topic'],'cepen')
        with patch('ai_providers.call_external_once',side_effect=TimeoutError):
            value,error=mechanics_search.refine(task,'q',[],time.monotonic()+15)
            self.assertIsNone(value);self.assertEqual(error,'TimeoutError')

    def test_no_candidates_for_unrelated_question(self):
        mechanics.sync()
        self.assertEqual(mechanics_search.lexical('космодром марсианский'),[])

    def test_refinement_does_not_traverse_provider_chain(self):
        from ai_providers import call_external_once,ProviderUnavailable
        with patch('ai_providers.external_models',return_value=['one','two']),patch('ai_runtime.model_ready',return_value=True),patch('ai_runtime.budget_wait',return_value=0),patch('ai_providers.call_google',side_effect=ProviderUnavailable('failure')) as call:
            with self.assertRaises(ProviderUnavailable):call_external_once({'prompt':'q'},8)
            call.assert_called_once()

    def test_deadline_and_stale_sections(self):
        mechanics.sync()
        with closing(repo.connect()) as conn,conn:
            conn.execute("UPDATE ai_mechanics_sections SET current=0 WHERE section_id='infection'")
        self.assertNotIn('infection',[r['section_id'] for _,r in mechanics_search.lexical('получить цепня')])
        with patch('ai_providers.call_external_once') as call:
            value,error=mechanics_search.refine({'id':999,'chat_id':-42},'q',[],time.monotonic()-1)
            self.assertEqual(error,'deadline');call.assert_not_called()

    def test_separate_deadlines_and_fast_lane(self):
        mechanics.sync()
        ident=ai_tasks.create_mechanics_task(chat_id=-42,user_id=1,request_message_id=100,user_query='Как получить цепня')
        sql=ai_tasks.create_text_to_sql_task(chat_id=-42,user_id=1,request_message_id=101,user_query='Баланс')
        m=ai_tasks.get_task(ident);s=ai_tasks.get_task(sql)
        delta=(repo.aware(m['rag_deadline_at']+'Z')-repo.aware(s['rag_deadline_at']+'Z')).total_seconds()
        self.assertGreater(delta,9)
        self.assertEqual(rag_search.claim(allow_mechanics=False)['id'],sql)
        self.assertEqual(rag_search.claim()['id'],ident)
