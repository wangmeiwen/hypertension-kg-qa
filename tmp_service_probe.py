import time
from app.a2a.service import A2AService

def run_query(input_text, context_id='', user_id='', metadata=None, cancel_check=None, progress_callback=None):
    if input_text == 'slow':
        for i in range(20):
            if cancel_check and cancel_check():
                break
            if progress_callback:
                progress_callback({'event':'tick','detail':f'tick-{i+1}'})
            time.sleep(0.05)
        return {'output':'slow-done','output_type':'slow','confidence':0.5}
    return {'output':'ok','output_type':'echo','confidence':0.8}

svc = A2AService(run_query=run_query, events_base_path='/a2a/test/tasks')
resp = svc.handle_rpc({'jsonrpc':'2.0','id':1,'method':'message/send','params':{'message':{'role':'user','parts':[{'kind':'text','text':'slow'}]}}})
print('send', resp)
task_id = (((resp.get('result') or {}).get('task') or {}).get('id'))
for i in range(5):
    t=svc.get_task(task_id)
    print('pre', i, ((t or {}).get('status') or {}).get('state'))
    time.sleep(0.05)
print('cancel', svc.handle_rpc({'jsonrpc':'2.0','id':2,'method':'tasks/cancel','params':{'taskId':task_id}}))
for i in range(30):
    t=svc.get_task(task_id)
    print('post', i, ((t or {}).get('status') or {}).get('state'))
    if ((t or {}).get('status') or {}).get('state') in {'completed','failed','canceled'}:
        break
    time.sleep(0.1)
print('final', svc.get_task(task_id))
svc.shutdown()
