import os, json, sqlite3, urllib.request, urllib.parse, datetime, pathlib, re, sys, time
ROOT=pathlib.Path('/workspace'); DB=ROOT/'data'/'jobs.sqlite3'; CFG=ROOT/'config'/'settings.json'
def config(): return json.loads(CFG.read_text())
def db():
 DB.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(DB); c.execute('''CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, company TEXT, title TEXT, location TEXT, url TEXT, description TEXT, salary TEXT, score INTEGER DEFAULT 0, status TEXT DEFAULT 'discovered', reason TEXT DEFAULT '', discovered_at TEXT)'''); c.commit();return c
def get_json(url):
 req=urllib.request.Request(url,headers={'User-Agent':'ClaudeJobAgent/1.0 (personal research; contact operator)'})
 with urllib.request.urlopen(req,timeout=18) as r:return json.load(r)
def fetch(cfg):
 out=[]
 for board in cfg.get('greenhouse_boards',[]):
  try:
   data=get_json('https://boards-api.greenhouse.io/v1/boards/'+urllib.parse.quote(board)+'/jobs?content=true')
   for j in data.get('jobs',[]):out.append(dict(id='gh:'+board+':'+str(j['id']),company=board,title=j.get('title',''),location=j.get('location',{}).get('name',''),url=j.get('absolute_url',''),description=re.sub('<[^>]*>',' ',j.get('content','')),salary=''))
  except Exception as e:print('Greenhouse board failed:',board,str(e),file=sys.stderr)
 for board in cfg.get('lever_boards',[]):
  try:
   data=get_json('https://api.lever.co/v0/postings/'+urllib.parse.quote(board)+'?mode=json')
   for j in data:out.append(dict(id='lever:'+board+':'+j['id'],company=board,title=j.get('text',''),location=j.get('categories',{}).get('location',''),url=j.get('hostedUrl',''),description=re.sub('<[^>]*>',' ',j.get('descriptionPlain','') or j.get('description','')),salary=str(j.get('salaryRange','') or '')))
  except Exception as e:print('Lever board failed:',board,str(e),file=sys.stderr)
 return out
def rank(j,cfg):
 title=j['title'].lower(); location=j['location'].lower(); description=j['description'].lower(); whole=' '.join((title,location,description))
 if any(x in title for x in cfg['excluded_title_terms']):return 0,'Excluded seniority/type'
 if not any(x in title for x in cfg['target_title_terms']):return 0,'Not a target role'
 if cfg.get('remote_only') and not ('remote' in location or 'remote' in title):return 0,'Not explicitly remote (manual verification needed)'
 score=50+min(30,sum(5 for x in cfg['skills'] if x.lower() in whole))
 if 'united states' in whole or 'usa' in whole or 'us remote' in whole:score+=10
 return min(score,100),'Keyword match; verify eligibility, compensation and remote geography manually'
def run():
 from app.job_classifier import classify_job
 cfg=config();conn=db();items=fetch(cfg);new=0
 for j in items:
  score,reason=rank(j,cfg)
  classification,reasons=classify_job(j,cfg)
  initial_status = (
   'filtered' if classification == 'excluded'
   else 'review'
  )
  cur=conn.execute('''INSERT OR IGNORE INTO jobs(id,company,title,location,url,description,salary,score,status,reason,discovered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(j['id'],j['company'],j['title'],j['location'],j['url'],j['description'][:30000],j['salary'],score,initial_status,reason,datetime.datetime.now(datetime.timezone.utc).isoformat()))
  if cur.rowcount:
   conn.execute(
    '''UPDATE jobs
       SET classification=?,
           classification_reason=?,
           classified_at=?
       WHERE id=?''',
    (
     classification,
     '; '.join(reasons),
     datetime.datetime.now(datetime.timezone.utc).isoformat(),
     j['id']
    )
   )
  new+=cur.rowcount
 conn.commit();print(json.dumps({'fetched':len(items),'new':new,'awaiting_review':conn.execute("SELECT count(*) FROM jobs WHERE status='review'").fetchone()[0],'database':str(DB)},indent=2));conn.close()
def list_jobs():
 c=db();rows=c.execute("SELECT id,company,title,location,score,url FROM jobs WHERE status='review' ORDER BY score DESC LIMIT 30").fetchall()
 for r in rows:print(' | '.join(map(str,r)))
 if not rows:print('No matching jobs yet. Add employer board slugs to config/settings.json.')
def tailor(job_id):
 if not config().get("resume_generation_enabled", False):
  raise SystemExit("Resume generation PAUSED. No API request made.")

 c=db();r=c.execute('SELECT company,title,description,url FROM jobs WHERE id=?',(job_id,)).fetchone()
 if not r:raise SystemExit('Job not found')
 profile=(ROOT/'config'/'verified_profile.txt').read_text()
 key=os.getenv('ANTHROPIC_API_KEY')
 if not key:raise SystemExit('Set ANTHROPIC_API_KEY privately in your shell; never commit it or paste it into chat.')
 payload=json.dumps({'model':os.getenv('CLAUDE_MODEL','claude-sonnet-4-5'),'max_tokens':1600,'system':'You are a truthful resume editor. Use only facts explicitly contained in the verified profile. Never fabricate experience, metrics, tools, dates, education, authorization, or credentials. Return a plain-text resume draft and clearly flag missing evidence. No application submission.', 'messages':[{'role':'user','content':'Verified profile:\n'+profile+'\n\nJob: '+r[1]+' at '+r[0]+'\n'+r[2][:16000]}]}).encode()
 req=urllib.request.Request('https://api.anthropic.com/v1/messages',data=payload,headers={'x-api-key':key,'anthropic-version':'2023-06-01','content-type':'application/json'},method='POST')
 try:
  with urllib.request.urlopen(req,timeout=90) as response:result=json.load(response)
 except urllib.error.HTTPError as e:raise SystemExit('Claude API error '+str(e.code)+': '+e.read().decode()[:400])
 draft='\n'.join(x.get('text','') for x in result.get('content',[]) if x.get('type')=='text')
 path=ROOT/'resumes'/('draft_'+re.sub('[^A-Za-z0-9_-]','_',job_id)+'.txt');path.parent.mkdir(exist_ok=True);path.write_text(draft)
 c.execute("UPDATE jobs SET status='draft_ready' WHERE id=?",(job_id,));c.commit();print('Draft saved for human review:',path)
if __name__=='__main__':
 command=sys.argv[1] if len(sys.argv)>1 else 'scan'
 if command=='scan':run()
 elif command=='list':list_jobs()
 elif command=='tailor' and len(sys.argv)>2:tailor(sys.argv[2])
 elif command=='health':db().close();print('OK: database initialized')
 else:raise SystemExit('Usage: python -m app.main [health|scan|list|tailor JOB_ID]')
