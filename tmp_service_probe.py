"""A2A 服务探针脚本。

该脚本用于本地快速验证 A2A 任务的完整生命周期：
1) 发送消息创建任务
2) 轮询任务状态
3) 发起取消请求
4) 再次轮询直到任务结束

主要用于开发联调与行为回归检查，不用于生产环境。
"""

import time
from typing import Any, Callable, Dict, Optional

from app.a2a.service import A2AService

def run_query(
    input_text: str,
    context_id: str = "",
    user_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """模拟 A2A 子任务执行逻辑。

    这是一个用于测试的查询函数，实现了两种模式：
    1) 慢任务模式：当 input_text 为 "slow" 时，循环 20 次并周期性上报进度；
    2) 普通模式：立即返回固定响应。

    慢任务模式下会在每轮开始前检查 cancel_check，若返回 True 则提前终止，
    便于验证任务取消流程是否按预期生效。

    Args:
        input_text: 用户输入文本；当值为 "slow" 时触发慢任务模拟。
        context_id: 会话上下文 ID。当前探针逻辑未使用，保留用于对齐真实接口。
        user_id: 用户 ID。当前探针逻辑未使用，保留用于对齐真实接口。
        metadata: 扩展元数据。当前探针逻辑未使用，保留用于后续调试扩展。
        cancel_check: 取消检查函数；返回 True 表示应停止当前任务执行。
        progress_callback: 进度回调函数；接收一个进度事件字典。

    Returns:
        Dict[str, Any]:
            统一响应结构，包含以下字段：
            - output: 结果文本
            - output_type: 结果类型（slow / echo）
            - confidence: 置信度（测试用固定值）
    """
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
