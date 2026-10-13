import asyncio
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import db
import cepen_messages as letters
import cepen_tts
import test_cepen_messages
CHAT = test_cepen_messages.CHAT


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_cepen_messages.LetterTests(); self.fixture.setUp()
        self.now = self.fixture.now
        with closing(db.get_connection()) as conn, conn:
            conn.execute('UPDATE cepen_letters SET tts_attempt=0')
        self.bot = AsyncMock()
        self.bot.get_chat_member.return_value = SimpleNamespace(status='member')
        self.directory = tempfile.TemporaryDirectory()
        self.mocks = [patch('ai_runtime.mode', return_value='api'),
                      patch('cepen_messages.context', return_value={'mood':'счастлив'}),
                      patch('cepen_messages.generate', return_value='Дарю {gift} сита!'),
                      patch.dict('os.environ', {'CEPEN_TTS_DIR':self.directory.name}),
                      patch('cepen_tts.direction', return_value={'voice':'Puck','style':'warm worm'}),
                      patch('cepen_tts.synthesize', return_value=b'OggSfake')]
        self.started = [p.start() for p in self.mocks]

    def tearDown(self):
        for p in reversed(self.mocks): p.stop()
        self.directory.cleanup(); self.fixture.tearDown()

    def test_voice_delivery_gift_once(self):
        self.assertEqual(1, asyncio.run(letters.dispatch(self.bot,self.now)))
        self.assertEqual(0, asyncio.run(letters.dispatch(self.bot,self.now)))
        self.bot.send_voice.assert_awaited_once(); self.bot.send_message.assert_not_called()
        with closing(db.get_connection()) as conn:
            self.assertEqual(1,conn.execute("SELECT count(*) FROM sit_ledger WHERE action_code='cepen_letter_gift'").fetchone()[0])
        self.assertEqual([],list(Path(self.directory.name).iterdir()))

    def test_three_failures_and_five_minute_delay(self):
        self.started[-1].side_effect = TimeoutError()
        asyncio.run(letters.dispatch(self.bot,self.now))
        asyncio.run(letters.dispatch(self.bot,self.now+timedelta(minutes=4)))
        self.assertEqual(1,self.started[-1].call_count)
        asyncio.run(letters.dispatch(self.bot,self.now+timedelta(minutes=6)))
        asyncio.run(letters.dispatch(self.bot,self.now+timedelta(minutes=12)))
        self.assertEqual(3,self.started[-1].call_count)
        self.started[2].assert_called_once(); self.started[-2].assert_called_once()
        self.bot.send_message.assert_awaited_once(); self.bot.send_voice.assert_not_called()

    def test_unknown_voice_delivery_no_repeat(self):
        self.bot.send_voice.side_effect = TimeoutError()
        asyncio.run(letters.dispatch(self.bot,self.now))
        asyncio.run(letters.dispatch(self.bot,self.now+timedelta(minutes=20)))
        self.bot.send_voice.assert_awaited_once(); self.bot.send_message.assert_not_called()

    def test_voice_json_and_no_audio_in_audit(self):
        self.assertEqual('Puck',cepen_tts.parse_voice('{"voice":"Puck","style":"warm"}')['voice'])
        with self.assertRaises(ValueError):cepen_tts.parse_voice('{"voice":"unknown","style":"warm"}')
        self.assertNotIn('secret_audio',str(cepen_tts.sanitize({'inlineData':{'data':'secret_audio'}})))

    def test_tts_model_quotas(self):
        import ai_runtime
        for model in cepen_tts.MODELS:
            self.assertEqual(10,ai_runtime.model_limits(model)['RPD'])

    def test_exact_size_boundaries(self):
        for tier,boundary in enumerate((30,120,300,500)):
            self.assertEqual(tier,cepen_tts.size_tier(boundary))
            self.assertEqual(tier+1,cepen_tts.size_tier(boundary+0.001))

    def test_cached_basis_keeps_voice_across_moods(self):
        data={'cepen':200,'skin':'chef','profession':'chef','mood':'подавлен'}
        with closing(db.get_connection()) as conn,conn:
            conn.execute('INSERT INTO cepen_voice_profiles VALUES (?,?,?,?)',
                         (CHAT,1,'[2, "chef"]','{"voice":"Kore","style":"warm worm"}'))
        self.mocks[-2].stop()
        try:
            with patch('cepen_tts.providers.call_google') as call:
                first=cepen_tts.direction(data,CHAT,1)
                second=cepen_tts.direction({**data,'mood':'счастлив'},CHAT,1)
                self.assertEqual(first['voice'],second['voice'])
                self.assertNotEqual(first['style'],second['style'])
                call.assert_not_called()
        finally:self.mocks[-2].start()

    def test_chain_skips_exhausted_model(self):
        # Exercise the real adapter without network/audio conversion.
        import base64
        response = SimpleNamespace(ok=True, json=lambda: {'steps':[{'type':'model_output','content':[
            {'type':'audio','mime_type':'audio/wav','data':base64.b64encode(b'wav').decode()}]}]})
        with patch.dict('os.environ', {'GEMINI_API_KEY':'test'}), \
             patch('cepen_tts.runtime.model_ready', return_value=True), \
             patch('cepen_tts.runtime.budget_wait', side_effect=lambda model: 1 if model==cepen_tts.MODELS[0] else 0), \
             patch('cepen_tts.runtime.reserve', return_value=1), \
             patch('cepen_tts.runtime.settle'), patch('cepen_tts.audit'), \
             patch('cepen_tts.ai_http.post', return_value=response) as post, \
             patch('cepen_tts.ogg_audio', return_value=b'OggS'):
            # The fixture mocks synthesize; call its saved original function.
            original = self.mocks[-1].get_original()[0]
            self.mocks[-1].stop()
            try:
                self.assertEqual(b'OggS',cepen_tts.synthesize('Привет',{'voice':'Puck','style':'warm'}))
                self.assertIn(cepen_tts.MODELS[1],post.call_args.args[0])
            finally:
                self.mocks[-1].start()
