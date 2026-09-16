from __future__ import annotations
import hashlib, json, sqlite3, sys, tempfile, unittest
from pathlib import Path
SOURCE_ROOT=Path(__file__).resolve().parents[1]/'src'
if str(SOURCE_ROOT) not in sys.path: sys.path.insert(0,str(SOURCE_ROOT))
from runtime_sync_workshop.guardian import InvestigationGuardian
from runtime_sync_workshop.util import WorkshopError

class GuardianV2CoreTests(unittest.TestCase):
  def fixture(self,root):
    st=root/'.state'; tx=root/'tx'; ws=root/'ws'; code=root/'code'; st.mkdir(); tx.mkdir(); (ws/'.kairos').mkdir(parents=True); (ws/'docs').mkdir(); (code/'runtime').mkdir(parents=True)
    for n in 'ab': (code/'runtime'/f'{n}.cpp').write_text(f'int {n}(){{return 1;}}\n')
    mem=root/'MEMORY.md'; mem.write_text('# Rules\nMEM.LOOP\nMEM.RULE.NO_GUESS\nMEM.RULE.LIVE_AUTH\nMEM.WORKSHOP\n# Arch\nMEM.RET.GRAPH\nMEM.RET.SOURCE\nMEM.RULE.GENERAL_FIX\n')
    (ws/'.kairos'/'runtime_state.json').write_text(json.dumps({'active_goal':'G','active_milestone':'M','active_task':'T','active_criterion':'C'}))
    auth=ws/'docs'/'a.md'; auth.write_text('# A\ncontract\n'); h=hashlib.sha256(auth.read_text().encode()).hexdigest(); db=root/'db.sqlite'
    c=sqlite3.connect(db); c.executescript('''CREATE TABLE search_routing_receipts(routing_receipt_id TEXT PRIMARY KEY,status TEXT,task_id TEXT,criterion_id TEXT,receipt_json TEXT,created_at TEXT); CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY,path TEXT,authority TEXT,revision INTEGER,content_sha256 TEXT); CREATE TABLE source_inspection_receipts(inspection_id TEXT PRIMARY KEY,permit_id TEXT,task_id TEXT,criterion_id TEXT,status TEXT,match_count INTEGER,receipt_json TEXT,created_at TEXT); CREATE TABLE source_inspection_permits(permit_id TEXT PRIMARY KEY,routing_receipt_id TEXT,purpose TEXT,scope_sha256 TEXT,receipt_json TEXT); CREATE TABLE heartbeat_receipts(heartbeat_id TEXT PRIMARY KEY,receipt_json TEXT,verified INTEGER);'''); c.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)',('A','docs/a.md','operating_contract',1,h)); c.commit(); c.close()
    g=InvestigationGuardian(state_directory=st,kairos_database=db,kairos_workspace=ws,codebase_root=code,transaction_directory=tx); return g,mem,tx
  def bind_srr(self,g,gid,ticket,step,rid,status='ROUTED',graph=False):
    r={'routing_receipt_id':rid,'status':status,'task_id':'T','criterion_id':'C','query':rid,'freshness':{'checked':True,'refreshed':False},'primary':{'artifact_id':'A','section_id':'s','path':'docs/a.md'} if status=='ROUTED' else None,'guardian':{'guardian_id':gid,'state_ticket_id':ticket,'step':step}}
    if graph:r['graph_route']={'seed_total':1,'returned':1,'truncated':False,'resolved_anchor_total':1}
    c=sqlite3.connect(g.kairos_database); c.execute('INSERT INTO search_routing_receipts VALUES(?,?,?,?,?,?)',(rid,status,'T','C',json.dumps(r),'2999-01-01T00:00:00Z')); c.commit(); c.close()
  def enter_step(self,g,gid,pkg,step,summary,*,evidence=(),sources=()):
    t=g.enter_state(gid,step=step,memory_selectors=['MEM.LOOP'],package_sha256=pkg)['state_ticket_id']
    return g.advance(gid,step=step,summary=summary,state_ticket_id=t,evidence_refs=evidence,package_sha256=pkg,sources=sources)
  def prove(self,g,mem,pkg):
    gid=g.start(problem='Human observed a reproducible governed mismatch.',memory_file=mem,memory_selectors=['MEM.LOOP'],package_sha256=pkg)['guardian_id']
    hyp=json.dumps({'observation':'bad output','invariant':'contract','suspected_mechanism':'owner','expected_owner':'owner','refutation':'absent'})
    self.enter_step(g,gid,pkg,'HYPOTHESIS',hyp)
    for step,rid,graph in [('FALSIFIER_1','SRR_1',False),('FALSIFIER_2','SRR_2',True),('MAP','SRR_M',True)]:
      t=g.enter_state(gid,step=step,memory_selectors=['MEM.LOOP'],package_sha256=pkg)['state_ticket_id']; self.bind_srr(g,gid,t,step,rid,graph=graph); g.advance(gid,step=step,summary='bounded verified evidence',state_ticket_id=t,evidence_refs=[rid],package_sha256=pkg,sources=['runtime/a.cpp','runtime/b.cpp'] if step=='MAP' else [])
    t=g.enter_state(gid,step='COUNTERPROBE',memory_selectors=['MEM.LOOP'],package_sha256=pkg)['state_ticket_id']; self.bind_srr(g,gid,t,'COUNTERPROBE','SRR_N',status='NO_MATCH'); g.advance(gid,step='COUNTERPROBE',summary='no competing owner',state_ticket_id=t,evidence_refs=['SRR_N'],package_sha256=pkg)
    t=g.enter_state(gid,step='EXACT_SOURCE',memory_selectors=['MEM.LOOP'],package_sha256=pkg)['state_ticket_id']; bind={'guardian_id':gid,'state_ticket_id':t,'step':'EXACT_SOURCE'}; permit={'permit_id':'SIP_1','routing_receipt_id':'SRR_M','purpose':'implementation_verification','scope_sha256':'f'*64,'guardian':bind}; sir={'inspection_id':'SIR_1','permit_id':'SIP_1','task_id':'T','criterion_id':'C','status':'VERIFIED','scope_stable':True,'truncated':False,'exception':None,'paths':['runtime/a.cpp','runtime/b.cpp'],'matches':[{'path':'runtime/a.cpp','line':1,'patterns':['a']},{'path':'runtime/b.cpp','line':1,'patterns':['b']}],'match_count':2,'guardian':bind}; c=sqlite3.connect(g.kairos_database); c.execute('INSERT INTO source_inspection_permits VALUES(?,?,?,?,?)',('SIP_1','SRR_M','implementation_verification','f'*64,json.dumps(permit))); c.execute('INSERT INTO source_inspection_receipts VALUES(?,?,?,?,?,?,?,?)',('SIR_1','SIP_1','T','C','VERIFIED',2,json.dumps(sir),'2999-01-01T00:00:01Z')); c.commit(); c.close(); g.advance(gid,step='EXACT_SOURCE',summary='exact source verified',state_ticket_id=t,evidence_refs=['SIR_1'],package_sha256=pkg)
    proof=json.dumps({'observation':'bad output','violated_invariant':'contract rule','causal_owner':'a and b','scope_reason':'mapped owners','general_fix_reason':'general mechanism'}); self.enter_step(g,gid,pkg,'PROVE',proof,sources=['runtime/a.cpp','runtime/b.cpp']); return gid
  def test_guard_enter_required(self):
    with tempfile.TemporaryDirectory() as d:
      g,m,_=self.fixture(Path(d)); gid=g.start(problem='Human observed a stable mismatch.',memory_file=m,memory_selectors=['MEM.LOOP'],package_sha256='a'*64)['guardian_id']; hyp=json.dumps({'observation':'bad output','invariant':'rule','suspected_mechanism':'owner','expected_owner':'module','refutation':'absent'})
      with self.assertRaises(WorkshopError) as e:g.advance(gid,step='HYPOTHESIS',summary=hyp,state_ticket_id='GST_fake',evidence_refs=[],package_sha256='a'*64)
      self.assertEqual(e.exception.code,'GUARDIAN_MEMORY_GATE_MISSING')
  def test_patch_session_rolls_and_consumes(self):
    with tempfile.TemporaryDirectory() as d:
      g,m,tx=self.fixture(Path(d)); p1,p2,p3='1'*64,'2'*64,'3'*64; gid=self.prove(g,m,p1)
      for i,(src,before,after,hb) in enumerate([('runtime/a.cpp',p1,p2,'HB1'),('runtime/b.cpp',p2,p3,'HB2')],1):
        t=g.enter_state(gid,step='PATCH',memory_selectors=['MEM.WORKSHOP'],package_sha256=before)['state_ticket_id']; r=g.reserve_transaction(gid,kind='normal',request={'sources':[src],'headers':[],'dependent_tests':[]},state_ticket_id=t,package_sha256=before); txn=f'TXN_{i}'+'x'*23; g.bind_transaction(gid,reservation_id=r['reservation_id'],transaction_id=txn); root=tx/txn; (root/'baseline'/src).parent.mkdir(parents=True); (root/'work'/src).parent.mkdir(parents=True); (root/'baseline'/src).write_text('int x(){return 1;}\n'); (root/'work'/src).write_text('int x(){return 2;}\n'); st={'transaction_id':txn,'state':'PREPARED','changes':[{'source':src,'header_changed':False}],'changed_sources':[src],'test_changes':[]}; g.observe_transaction_state(txn,event='PREPARE_COMPLETED',state=st); c=sqlite3.connect(g.kairos_database); c.execute('INSERT INTO heartbeat_receipts VALUES(?,?,1)',(hb,json.dumps({'heartbeat_id':hb,'verified':True}))); c.commit(); c.close(); st.update(state='POSTCHECK_VERIFIED',postcheck_package_sha256=after,heartbeat={'heartbeat_id':hb}); g.release_transaction(txn,state=st,package_sha256=after)
      s=g.status(gid,full=True); self.assertEqual(s['rolling_package_sha256'],p3); self.assertEqual(s['patch_session']['consumed']['normal']['sources'],['runtime/a.cpp','runtime/b.cpp']); self.assertEqual(s['next_step'],'HEARTBEAT')
  def test_abort_consumes_nothing(self):
    with tempfile.TemporaryDirectory() as d:
      g,m,_=self.fixture(Path(d)); p='7'*64; gid=self.prove(g,m,p); t=g.enter_state(gid,step='PATCH',memory_selectors=['MEM.WORKSHOP'],package_sha256=p)['state_ticket_id']; r=g.reserve_transaction(gid,kind='normal',request={'sources':['runtime/a.cpp'],'headers':[],'dependent_tests':[]},state_ticket_id=t,package_sha256=p); txn='TXN_abort'+'x'*16; g.bind_transaction(gid,reservation_id=r['reservation_id'],transaction_id=txn); g.release_transaction(txn,state={'transaction_id':txn,'state':'ABORTED'},package_sha256=p); self.assertEqual(g.status(gid,full=True)['patch_session']['consumed']['normal']['sources'],[])
if __name__=='__main__': unittest.main()
