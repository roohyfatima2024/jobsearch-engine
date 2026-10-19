import os, sqlite3, html, urllib.parse, json
from contextlib import closing
from app.migrations import require_current
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
DB=Path(os.environ.get('JOBS_DB','/workspace/data/jobs.sqlite3'))
RESUMES=Path(os.environ.get('RESUMES_DIR','/workspace/resumes'))
HOST='127.0.0.1'; PORT=8765
CSS='''body{font-family:system-ui,Arial;background:#f6f7fb;color:#172033;margin:0}main{max-width:1100px;margin:32px auto;padding:0 18px}header{display:flex;justify-content:space-between;align-items:center}article{background:white;border:1px solid #e0e4eb;border-radius:12px;padding:18px;margin:14px 0}small{color:#64748b}.pill{display:inline-block;background:#edf2ff;padding:5px 10px;border-radius:30px}button,.btn{border:0;border-radius:8px;padding:10px 14px;cursor:pointer;background:#1e40af;color:white;text-decoration:none;display:inline-block}button.reject{background:#a21caf}button.reset{background:#475569}a{color:#1d4ed8}form{display:inline-block;margin-right:6px}nav a{margin-right:12px}pre{white-space:pre-wrap;word-break:break-word;background:#f1f5f9;padding:16px;border-radius:8px}input{padding:10px;border:1px solid #ccc;border-radius:8px}'''
def conn():
 c=sqlite3.connect(DB.resolve().as_uri()+'?mode=rw',uri=True)
 try:
  require_current(c)
  c.row_factory=sqlite3.Row
 except BaseException:
  c.close()
  raise
 return c
def esc(s):return html.escape(str(s if s is not None else ''))
def layout(body):return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Private Job Review</title><style>{CSS}</style></head><body><main><header><h1>Job Review</h1><small>Private · SSH tunnel</small></header>{body}</main></body></html>'
class Handler(BaseHTTPRequestHandler):
 def send(self,code,body,typ='text/html'):
  b=body.encode();self.send_response(code);self.send_header('Content-Type',typ+'; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");self.end_headers();self.wfile.write(b)
 def redirect(self):self.send_response(303);self.send_header('Location','/');self.end_headers()
 def do_GET(self):
  u=urllib.parse.urlsplit(self.path);q=urllib.parse.parse_qs(u.query)
  try:
   with closing(conn()) as c, c:
    if u.path=='/':
     status=q.get('status',['all'])[0];allowed=['review','approved','rejected','draft_ready','all'];status=status if status in allowed else 'all'
     classification=q.get('classification',['needs_review'])[0];categories=['needs_review','eligible','low_match','excluded','all'];classification=classification if classification in categories else 'needs_review'
     counts={r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM jobs GROUP BY status')}
     class_counts={r['classification']:r['n'] for r in c.execute('SELECT classification,COUNT(*) n FROM jobs GROUP BY classification')}
     sql='SELECT id,company,title,location,url,score,status,reason,classification,classification_reason FROM jobs';args=[]
     conditions=[]
     if status!='all':conditions.append('status=?');args.append(status)
     if classification!='all':conditions.append('classification=?');args.append(classification)
     if conditions:sql+=' WHERE '+' AND '.join(conditions)
     sql+=' ORDER BY score DESC,title LIMIT 100'
     rows=c.execute(sql,args).fetchall()
     class_nav=' '.join(f'<a href="/?classification={s}&status=all">{esc(s.replace("_"," ").title())} ({class_counts.get(s,0) if s!="all" else sum(class_counts.values())})</a>' for s in categories)
     nav='<p><b>Automatic classification:</b> '+class_nav+'</p>'
     nav+='<p><b>Manual decisions:</b> '+' '.join(f'<a href="/?classification={classification}&status={s}">{esc(s.title())} ({counts.get(s,0) if s!="all" else sum(counts.values())})</a>' for s in allowed)+'</p>'
     cards=[]
     for r in rows:
      jid=esc(r['id']);url=esc(r['url']);title=esc(r['title']);buttons=''
      for action,label,cl in [('approved','Approve',''),('rejected','Reject','reject'),('review','Return to review','reset')]:
       buttons+=f'<form method="POST" action="/decision"><input type="hidden" name="id" value="{jid}"><input type="hidden" name="status" value="{action}"><button class="{cl}">{label}</button></form>'
      cards.append(f'<article><h3>{title} <span class="pill">{r["score"]}/100</span></h3><p>{esc(r["company"])} · {esc(r["location"])} · <b>{esc(r["status"])}</b></p><p><small>{esc(r["reason"])}</small></p><p><a href="{url}" target="_blank" rel="noopener noreferrer">Original job posting</a> · <a href="/job?id={urllib.parse.quote(r["id"])}">Job details / resume</a></p>{buttons}</article>')
      cards[-1]+='<p><b>Classification:</b> '+esc(r['classification'])+'</p><p><b>Review reasons:</b> '+esc(r['classification_reason'])+'</p>'
      category = r['classification'] or 'unclassified'
      status_value = r['status'] or ''

      if category == 'excluded':
          cards[-1] += '<p><b>Excluded job — do not proceed.</b></p>'
      elif category == 'needs_review':
          cards[-1] += '<p><b>Verification required before resume generation.</b></p>'
      elif category == 'eligible':
          cards[-1] += '<p><b>Passed automatic screening.</b></p>'

      if status_value == 'approved' and category != 'eligible':
          cards[-1] += (
              '<p><b>Previously approved — verification still required.</b> '
              'Approval does not confirm eligibility or authorize '
              'resume generation.</p>'
          )
     self.send(200,layout('<nav>'+nav+'</nav><p>Showing up to 100 results. Approvals only update your local review status; they never submit applications.</p>'+''.join(cards)))
    elif u.path=='/job':
     jid=q.get('id',[''])[0];r=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
     if not r:return self.send(404,layout('Job not found'))
     candidates=[p for p in RESUMES.glob('draft_*.txt') if p.is_file() and p.name==('draft_'+__import__('re').sub(r'[^A-Za-z0-9_-]','_',jid)+'.txt')]
     draft='<p>No tailored resume draft yet. The existing tailor command must be run separately.</p>'
     if candidates:draft='<h3>Tailored resume draft</h3><pre>'+esc(candidates[0].read_text()[:100000])+'</pre>'
     self.send(200,layout(f'<p><a href="/">← Back</a></p><article><h2>{esc(r["title"])}</h2><p>{esc(r["company"])} · {esc(r["location"])}</p><p>Score: {r["score"]}/100 · Status: {esc(r["status"])}</p><p>{esc(r["reason"])}</p><h3>Job description</h3><pre>{esc(r["description"])}</pre>{draft}</article>'))
    else:self.send(404,layout('Not found'))
  except Exception as e:self.send(500,layout('Database unavailable. Check Docker logs.'))
 def do_POST(self):
  if self.path!='/decision':return self.send(404,'Not found')
  if self.headers.get('Origin') not in (None,'http://localhost:8765','http://127.0.0.1:8765'):return self.send(403,'Invalid origin')
  try:
   n=int(self.headers.get('Content-Length','0'))
   if n>4096:return self.send(413,'Too large')
   d=urllib.parse.parse_qs(self.rfile.read(n).decode());jid=d.get('id',[''])[0];status=d.get('status',[''])[0]
   if status not in ('approved','rejected','review'):return self.send(400,'Invalid status')
   with closing(conn()) as c, c:
                if status == 'approved':
                    job = c.execute(
                        'SELECT classification FROM jobs WHERE id=?',
                        (jid,)
                    ).fetchone()
                    if not job or job['classification'] != 'eligible':
                        return self.send(
                            403,
                            layout(
                                '<h2>Approval blocked</h2>'
                                '<p>This job has not passed automatic screening.</p>'
                                '<p><a href="/">Back to dashboard</a></p>'
                            )
                        )
                c.execute(
                    'UPDATE jobs SET status=? WHERE id=?',
                    (status, jid)
                )
                c.commit()
   self.redirect()
  except Exception:self.send(500,'Could not update job')
if __name__=='__main__':
 if not DB.is_file():raise SystemExit(f'Database not found: {DB}')
 print(f'Dashboard listening at http://{HOST}:{PORT}',flush=True)
 ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
