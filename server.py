#!/usr/bin/env python3
"""Standard-library-only backend for Tala Mobile. Never fabricates market data."""
import json, math, os, threading, time, html
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parent; STATIC=ROOT/'static'
NAVASAN_DEFAULT='http://api.navasan.tech'; CHART_TTL=int(os.getenv('NAVASAN_CANDLE_CACHE_SECONDS','86400')); NEWS_TTL=1200
_cache={}; _lock=threading.RLock(); _rates={}
class AppError(Exception):
 def __init__(self,msg,status=502): self.status=status; super().__init__(msg)
def cache_get(k,ttl):
 with _lock:
  v=_cache.get(k)
  if v and time.time()-v[0]<ttl:return v[1]
  _cache.pop(k,None); return None
def cache_put(k,v):
 with _lock:_cache[k]=(time.time(),v)
def clear_cache():
 with _lock:_cache.clear()
def quote_ttl():
 try:v=int(os.getenv('NAVASAN_QUOTE_CACHE_SECONDS','14400'))
 except ValueError:raise AppError('تنظیم زمان کش قیمت نامعتبر است.',500)
 if v<1:raise AppError('زمان کش قیمت باید مثبت باشد.',500)
 return v
def fetch_json(url,timeout=12,body=None,headers=None):
 h={'User-Agent':'TalaMobile/2.0','Accept':'application/json'}; h.update(headers or {})
 req=Request(url,data=body,headers=h,method='POST' if body is not None else 'GET')
 try:
  with urlopen(req,timeout=timeout) as r:
   raw=r.read(2_000_001)
   if len(raw)>2_000_000:raise AppError('پاسخ سرویس بیش از اندازه است.',502)
   return json.loads(raw.decode(r.headers.get_content_charset() or 'utf-8'))
 except AppError:raise
 except HTTPError as e:raise AppError('سرویس بالادستی پاسخ ناموفق داد.',502) from e
 except (URLError,TimeoutError,OSError,json.JSONDecodeError,UnicodeDecodeError) as e:raise AppError('دریافت داده از سرویس بالادستی ناموفق بود.',502) from e
def num(v):
 try:
  if v is None or isinstance(v,bool):return None
  x=float(str(v).replace(',','').strip()); return x if math.isfinite(x) and x>=0 else None
 except (TypeError,ValueError):return None
def ts(v):
 try:
  if v is None or v=='':return None
  if isinstance(v,(int,float)) or str(v).strip().replace('.','',1).isdigit():
   n=float(v); n=n/1000 if n>1e12 else n; return int(n) if n>0 else None
  d=datetime.fromisoformat(str(v).strip().replace('Z','+00:00')); d=d.replace(tzinfo=d.tzinfo or timezone.utc);return int(d.timestamp())
 except (ValueError,TypeError,OverflowError):return None
def normalize_quote_payload(payload,retrieved_at=None):
 if not isinstance(payload,dict):raise AppError('قالب دادهٔ قیمت قابل‌استفاده نیست.',502)
 out={}
 for key_name,name in [('gold_18k','18ayar'),('usd_irr','usd_sell'),('xau_usd','usd_xau')]:
  x=payload.get(name); x=x if isinstance(x,dict) else {'value':x}
  value=num(x.get('value'))
  if value is None:raise AppError('قیمت '+name+' در پاسخ منبع موجود یا معتبر نیست.',502)
  out[key_name]={'value_rial':value,'value_toman':value/10,'timestamp':ts(x.get('timestamp') or x.get('date'))}
 out.update(retrieved_at=int(retrieved_at if retrieved_at is not None else time.time()),source='Navasan API')
 return out
def navasan_url(path,params):
 key_name=os.getenv('NAVASAN_API_KEY','').strip()
 if not key_name:raise AppError('کلید Navasan روی میزبان تنظیم نشده است.',503)
 base=os.getenv('NAVASAN_BASE_URL',NAVASAN_DEFAULT).strip().rstrip('/')
 if not base.startswith(('http://','https://')):raise AppError('نشانی Navasan نامعتبر است.',500)
 return base+path+'?'+urlencode({'api_key_name':key_name,**params})
def get_quote():
 ttl=quote_ttl(); x=cache_get('quote',ttl)
 if x is not None:return {**x,'cache':{'ttl_seconds':ttl,'hit':True}}
 x=normalize_quote_payload(fetch_json(navasan_url('/latest/',{})));cache_put('quote',x)
 return {**x,'cache':{'ttl_seconds':ttl,'hit':False}}
def normalize_candles(payload):
 rows=payload.get('data') or payload.get('candles') if isinstance(payload,dict) else payload
 if not isinstance(rows,list):raise AppError('تاریخچهٔ کندل قالب نامعتبر دارد.',502)
 result=[]
 for r in rows:
  if not isinstance(r,dict):continue
  vals={k:num(r.get(k)) for k in ('open','high','low','close')}; t=ts(r.get('timestamp') or r.get('date'))
  if t is None or any(v is None for v in vals.values()):continue
  if vals['high']<max(vals['open'],vals['close'],vals['low']) or vals['low']>min(vals['open'],vals['close'],vals['high']):continue
  result.append({'timestamp':t,'date':str(r.get('date') or ''),**vals})
 result=sorted({r['timestamp']:r for r in result}.values(),key=lambda z:z['timestamp'])[-120:]
 if not result:raise AppError('دادهٔ واقعی کندل روزانه در دسترس نیست؛ نمودار ساخته نشد.',404)
 return result
def get_candles():
 x=cache_get('candles',CHART_TTL)
 if x is None:
  end=int(time.time()); start=end-365*86400
  x=normalize_candles(fetch_json(navasan_url('/ohlcSearch/',{'item':'18ayar','start':start,'end':end})));cache_put('candles',x);hit=False
 else:hit=True
 return {'candles':x,'source':'Navasan API — OHLC روزانه','cache':{'ttl_seconds':CHART_TTL,'hit':hit}}
NEWS_Q={'iran':'(gold OR inflation OR currency) sourcelang:persian','world':'(gold OR XAUUSD OR "Federal Reserve" OR inflation) sourcelang:english'}
def get_news(region):
 if region not in NEWS_Q:raise AppError('ناحیهٔ خبر نامعتبر است.',400)
 x=cache_get('news:'+region,NEWS_TTL)
 if x is None:
  params={'query':NEWS_Q[region],'mode':'ArtList','format':'json','maxrecords':12,'timespan':'24h','sort':'DateDesc'}
  data=fetch_json('https://api.gdeltproject.org/api/v2/doc/doc?'+urlencode(params),15); articles=data.get('articles',[]) if isinstance(data,dict) else []
  if not isinstance(articles,list):raise AppError('قالب اخبار معتبر نیست.',502)
  x=[]
  for a in articles[:12]:
   if not isinstance(a,dict):continue
   u=a.get('url'); p=urlparse(u or '')
   if p.scheme not in ('http','https') or not p.netloc:continue
   x.append({'title':str(a.get('title') or 'بدون عنوان')[:400],'source':str(a.get('domain') or a.get('sourceCountry') or 'منبع نامشخص')[:120],'url':u,'seen_at':str(a.get('seendate') or '')[:40]})
  cache_put('news:'+region,x); hit=False
 else:hit=True
 return {'items':x,'source':'GDELT DOC API','cache':{'ttl_seconds':NEWS_TTL,'hit':hit}}
def config_public():
 return {'quote_cache_seconds':quote_ttl(),'chart_cache_seconds':CHART_TTL,'news_cache_seconds':NEWS_TTL,'quote_refresh_seconds':max(60,quote_ttl()),'chart_refresh_seconds':CHART_TTL,'source':'Navasan API','news_source':'GDELT DOC API','free_tier_note':'پلن رایگان: سهمیهٔ محدود؛ کش پیش‌فرض ۴ ساعت و دریافت دستی نیز از کش عبور نمی‌کند.','transport_warning':'مستند Navasan این endpoint را با HTTP معرفی می‌کند؛ HTTP می‌تواند کلید را در مسیر شبکه آشکار کند. HTTPS را بدون پشتیبانی ارائه‌دهنده فرض نکنید.'}
def access_token():return os.getenv('APP_ACCESS_TOKEN','').strip()
def authorized(h):
 expected=access_token()
 if not expected:return True
 return h.get('Authorization','')=='Bearer '+expected
def rate_ok(identity):
 now=time.time(); minute=now-60; day=now-86400
 with _lock:
  vals=[v for v in _rates.get(identity,[]) if v>day]
  if sum(v>minute for v in vals)>=2 or len(vals)>=20:_rates[identity]=vals;return False
  vals.append(now);_rates[identity]=vals;return True
def analyze(question,ip):
 if not access_token():raise AppError('تحلیل AI غیرفعال است: APP_ACCESS_TOKEN را روی میزبان تنظیم کنید.',503)
 if not os.getenv('OPENAI_API_KEY','').strip():raise AppError('تحلیل AI هنوز فعال نشده است؛ کلید OpenAI روی میزبان تنظیم نشده.',503)
 if not rate_ok(ip):raise AppError('سقف درخواست تحلیل رسیده است؛ بعداً دوباره تلاش کنید.',429)
 if len(question)>300:raise AppError('پرسش حداکثر ۳۰۰ نویسه است.',400)
 q=get_quote(); candles=get_candles()['candles']; news=get_news('iran')['items'][:8]
 closes=[c['close']/10 for c in candles]; indicators={}
 if len(closes)>=20:
  indicators['ema20_toman']=sum(closes[-20:])/20
  indicators['range20_toman']={'low':min(closes[-20:]),'high':max(closes[-20:])}
 else:indicators['data_note']='برای EMA20 و دامنهٔ ۲۰روزه دادهٔ کافی نیست.'
 if len(closes)>=50:indicators['ema50_toman']=sum(closes[-50:])/50
 if len(closes)>=15:
  diffs=[closes[i]-closes[i-1] for i in range(1,len(closes))]; gains=[max(v,0) for v in diffs[-14:]]; losses=[max(-v,0) for v in diffs[-14:]]
  avg_g=sum(gains)/14; avg_l=sum(losses)/14; indicators['rsi14']=100 if avg_l==0 else 100-100/(1+avg_g/avg_l)
 else:indicators['rsi14_note']='برای RSI14 دادهٔ کافی نیست.'
 snapshot={'quote':q,'candles':candles[-60:],'indicators':indicators,'news_headlines':news,'question':question,'instruction':'پاسخ فارسی، خنثی و آموزشی؛ فقط بر شواهد همین داده‌ها تکیه کن. سناریوها و عدم‌قطعیت را جدا کن. خبرها تأیید مستقل نشده‌اند. توصیه معامله نده؛ دادهٔ غایب را جعل نکن.'}
 model=os.getenv('OPENAI_MODEL','gpt-5.4-mini').strip() or 'gpt-5.4-mini'
 payload=json.dumps({'model':model,'input':[{'role':'user','content':[{'type':'input_text','text':json.dumps(snapshot,ensure_ascii=False)}]}],'instructions':'شما دستیار آموزشی بازار هستید، نه مشاور مالی. تحلیل محتاطانه و کوتاه فارسی، شواهد/سناریو/عدم‌قطعیت و هشدار عدم توصیه معامله را بگو. فقط داده ورودی را استفاده کن.','store':False,'max_output_tokens':900},ensure_ascii=False).encode()
 data=fetch_json('https://api.openai.com/v1/responses',timeout=35,body=payload,headers={'Authorization':'Bearer '+os.getenv('OPENAI_API_KEY'),'Content-Type':'application/json'})
 texts=[]
 for item in data.get('output',[]) if isinstance(data,dict) else []:
  for c in item.get('content',[]) if isinstance(item,dict) else []:
   if c.get('type')=='output_text' and isinstance(c.get('text'),str):texts.append(c['text'])
 if not texts:raise AppError('پاسخ تحلیل‌گر قابل‌خواندن نبود.',502)
 return {'text':'\n'.join(texts),'model':model,'source_time':q['retrieved_at'],'disclaimer':'تحلیل آموزشی است، خبرها راستی‌آزمایی مستقل نشده‌اند و توصیهٔ خریدوفروش نیست.'}
class Handler(BaseHTTPRequestHandler):
 server_version='TalaMobile/2.0'
 def log_message(self,fmt,*args):print('[%s] %s'%(self.log_date_time_string(),fmt%args))
 def send_data(self,status,data,typ='application/json; charset=utf-8'):
  body=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()
  self.send_response(status);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(body)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(body)
 def api(self,fn):
  try:self.send_data(200,fn())
  except AppError as e:self.send_data(e.status,{'error':str(e)})
  except Exception as e:print('Internal API failure:',type(e).__name__);self.send_data(500,{'error':'خطای داخلی سرور.'})
 def do_GET(self):
  u=urlparse(self.path); p=u.path
  if p.startswith('/api/') and not authorized(self.headers):return self.send_data(401,{'error':'برای دسترسی، کلید خصوصی را در تنظیمات وارد کنید.'})
  if p=='/api/config':return self.api(config_public)
  if p=='/api/quote':return self.api(get_quote)
  if p=='/api/candles':return self.api(get_candles)
  if p=='/api/news':return self.api(lambda:get_news(parse_qs(u.query).get('region',['iran'])[0]))
  if p.startswith('/api/'):return self.send_data(404,{'error':'مسیر پیدا نشد.'})
  rel='index.html' if p=='/' else p.lstrip('/'); file=(STATIC/rel).resolve()
  try:
   if not file.is_relative_to(STATIC.resolve()) or not file.is_file():return self.send_data(404,{'error':'فایل پیدا نشد.'})
   types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.webmanifest':'application/manifest+json; charset=utf-8'}
   self.send_data(200,file.read_bytes(),types.get(file.suffix,'application/octet-stream'))
  except OSError:return self.send_data(404,{'error':'فایل پیدا نشد.'})
 def do_POST(self):
  p=urlparse(self.path).path
  if p!='/api/ai':return self.send_data(404,{'error':'مسیر پیدا نشد.'})
  if not access_token():return self.send_data(503,{'error':'تحلیل AI بدون APP_ACCESS_TOKEN عمداً غیرفعال است.'})
  if not authorized(self.headers):return self.send_data(401,{'error':'برای دسترسی، کلید خصوصی را در تنظیمات وارد کنید.'})
  try:n=int(self.headers.get('Content-Length','0'))
  except ValueError:n=999999
  if n>4096:return self.send_data(413,{'error':'درخواست بیش از حد بزرگ است.'})
  if n<1:return self.send_data(400,{'error':'بدنهٔ درخواست خالی است.'})
  try:data=json.loads(self.rfile.read(n)); question=data.get('question','') if isinstance(data,dict) else None
  except Exception:return self.send_data(400,{'error':'JSON درخواست معتبر نیست.'})
  if not isinstance(question,str) or len(question)>300:return self.send_data(400,{'error':'پرسش باید متن و حداکثر ۳۰۰ نویسه باشد.'})
  self.api(lambda:analyze(question,self.client_address[0]+'|'+self.headers.get('Authorization','')))
def main():
 host='0.0.0.0'; port=int(os.getenv('PORT','8000')); print('Tala Mobile listening on %s:%d'%(host,port)); ThreadingHTTPServer((host,port),Handler).serve_forever()
if __name__=='__main__':main()
