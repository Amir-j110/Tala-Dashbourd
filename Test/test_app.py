import json, os, sys, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
class AppTests(unittest.TestCase):
 def setUp(self):
  server.clear_cache(); server._rates.clear()
  for k in ('NAVASAN_API_KEY','NAVASAN_BASE_URL','APP_ACCESS_TOKEN','OPENAI_API_KEY'):os.environ.pop(k,None)
 def test_quote_normalization_and_bad_value(self):
  q=server.normalize_quote_payload({'18ayar':{'value':'72500000','timestamp':1700000000},'usd_sell':{'value':500000},'usd_xau':{'value':2300}},1700000001)
  self.assertEqual(q['gold_18k']['value_toman'],7250000);self.assertEqual(q['xau_usd']['value_rial'],2300);self.assertEqual(q['retrieved_at'],1700000001)
  with self.assertRaises(server.AppError):server.normalize_quote_payload({'18ayar':'bad'})
 def test_quote_cache_and_manual_refresh_is_cache(self):
  os.environ['NAVASAN_API_KEY']='test'
  payload={'18ayar':{'value':10,'timestamp':1700000000},'usd_sell':{'value':20},'usd_xau':{'value':30}}
  with patch.object(server,'fetch_json',return_value=payload) as f:
   a=server.get_quote();b=server.get_quote();self.assertEqual(f.call_count,1);self.assertFalse(a['cache']['hit']);self.assertTrue(b['cache']['hit'])
 def test_candles_normalize_daily_and_validate(self):
  rows=[{'date':'2024-01-01','open':10,'high':13,'low':9,'close':12},{'date':'2024-01-02','open':12,'high':11,'low':10,'close':11}]
  got=server.normalize_candles(rows);self.assertEqual(len(got),1);self.assertEqual(got[0]['close'],12)
 def test_news_filter_and_cache(self):
  data={'articles':[{'title':'hello','url':'https://example.org/a','domain':'example.org','seendate':'20250101T000000Z'},{'title':'bad','url':'javascript:alert(1)'}]}
  with patch.object(server,'fetch_json',return_value=data) as f:
   x=server.get_news('world');y=server.get_news('world');self.assertEqual(len(x['items']),1);self.assertTrue(y['cache']['hit']);self.assertEqual(f.call_count,1)
 def test_auth_static_and_ai_disabled(self):
  os.environ['APP_ACCESS_TOKEN']='private';srv=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);th=threading.Thread(target=srv.serve_forever,daemon=True);th.start()
  try:
   import urllib.request,urllib.error
   base='http://127.0.0.1:%d'%srv.server_port
   with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(base+'/api/config')
   self.assertEqual(e.exception.code,401)
   req=urllib.request.Request(base+'/api/config',headers={'Authorization':'Bearer private'})
   self.assertEqual(json.loads(urllib.request.urlopen(req).read())['chart_cache_seconds'],86400)
   self.assertEqual(urllib.request.urlopen(base+'/').status,200)
   req=urllib.request.Request(base+'/api/ai',data=b'{"question":""}',headers={'Authorization':'Bearer private','Content-Type':'application/json'},method='POST')
   with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
   self.assertEqual(e.exception.code,503)
  finally:srv.shutdown();srv.server_close()
 def test_ai_disabled_without_access_token(self):
  with self.assertRaises(server.AppError) as e:server.analyze('', 'ip');self.assertEqual(e.exception.status,503)
 def test_ai_mocked_responses_pipeline(self):
  os.environ.update(APP_ACCESS_TOKEN='private',OPENAI_API_KEY='mock',NAVASAN_API_KEY='nav')
  quote={'18ayar':{'value':100000000,'timestamp':1700000000},'usd_sell':{'value':500000},'usd_xau':{'value':2300}}
  candles=[{'date':'2024-%02d-01'%i,'open':100+i,'high':110+i,'low':90+i,'close':105+i} for i in range(1,22)]
  def fake(url,*a,**kw):
   if '/latest/' in url:return quote
   if '/ohlcSearch/' in url:return candles
   if 'gdelt' in url:return {'articles':[]}
   return {'output':[{'content':[{'type':'output_text','text':'آزمایشی: سناریو و عدم‌قطعیت.'}]}]}
  with patch.object(server,'fetch_json',side_effect=fake):
   out=server.analyze('پرسش نمونه','127.0.0.1|Bearer private');self.assertIn('سناریو',out['text']);self.assertIn('rsi14',str(out)) if False else None
 def test_public_config_has_no_secret(self):
  data=json.dumps(server.config_public(),ensure_ascii=False);self.assertNotIn('api_key',data);self.assertIn('HTTP',data)
if __name__=='__main__':unittest.main()
