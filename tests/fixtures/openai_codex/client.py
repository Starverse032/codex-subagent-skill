from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import threading
import uuid

class CodexConfig:
    def __init__(self,**kwargs): self.__dict__.update(kwargs)

class Payload:
    def __init__(self,value): self.value=value
    def model_dump(self,**kwargs): return self.value

class CodexClient:
    def __init__(self,config,approval_handler):
        self.config=config
        self.stopped=threading.Event()
        self.events=[]
    def start(self): pass
    def initialize(self): pass
    def close(self): self.stopped.set()
    def request(self,method,params,*,response_model):
        key=Path(os.environ['CODEX_SUBAGENT_STATE_DIR'])
        if method=='project/create':
            pid=str(uuid.uuid5(uuid.NAMESPACE_URL,params['idempotencyKey']))
            value={'project':{'id':pid,'name':params['name']}}
            (key/f'fixture-project-{pid}.json').write_text(json.dumps(value),encoding='utf-8')
        elif method=='project/read':
            value=json.loads((key/f'fixture-project-{params["projectId"]}.json').read_text('utf-8'))
        elif method=='thread/metadata/update':
            value={'thread':{'projectId':params['projectId']}}
        else:
            raise ValueError(f'unexpected fixture RPC {method}')
        return response_model.model_validate(value)
    def thread_start(self,params):
        return SimpleNamespace(thread=SimpleNamespace(id=str(uuid.uuid4()),project_id=params.get('projectId')))
    def thread_resume(self,thread_id,params):
        return SimpleNamespace(thread=SimpleNamespace(id=thread_id))
    def turn_start(self,thread_id,input_items,params):
        prompt=input_items[0]['text']
        self.long='fixture:long' in prompt
        self.turn_id=str(uuid.uuid4())
        artifact=(prompt.split('run-owned directory: ',1)[1].split('\n',1)[0].rstrip('.')
                  if 'run-owned directory: ' in prompt else None)
        self.run=Path(artifact).parent if artifact else None
        declarations=[]
        generation=[]
        if 'fixture:artifact' in prompt:
            path=Path(artifact)/'output.txt'
            path.write_text('fixture deliverable',encoding='utf-8')
            declarations=[{'path':str(path),'kind':'file'}]
        if 'fixture:image' in prompt:
            from PIL import Image
            path=Path(artifact)/'image.png'
            Image.new('RGB',(16,16),'red').save(path)
            declarations=[{'path':str(path),'kind':'image'}]
            generation=[('item/completed',{'item':{'type':'imageGeneration','savedPath':str(path),'status':'completed'}})]
        if 'fixture:orphan' in prompt:
            child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
            (self.run/'orphan.pid').write_text(str(child.pid))
        message=(json.dumps({'summary':'FIXTURE_OK','artifacts':declarations})
                 if 'outputSchema' in params else 'FIXTURE_OK')
        if 'fixture:weaken-schema' in prompt:
            (self.run/'response-schema.json').write_text('{"type":"object"}',encoding='utf-8')
        if 'fixture:malformed' in prompt: message='not JSON'
        self.events=generation+[('item/completed',{'item':{'type':'agentMessage','phase':'final_answer','text':message}}),
                     ('turn/completed',{'turn':{'id':self.turn_id,'status':'failed' if 'fixture:failed' in prompt else 'completed'}})]
        if 'fixture:empty' in prompt: self.events=self.events[1:]
        return SimpleNamespace(turn=SimpleNamespace(id=self.turn_id))
    def register_turn_notifications(self,turn_id): pass
    def turn_interrupt(self,*_): self.stopped.set()
    def next_turn_notification(self,turn_id):
        if self.long:
            self.stopped.wait(60)
            self.long=False
            self.events=[('turn/completed',{'turn':{'id':turn_id,'status':'interrupted'}})]
        method,body=self.events.pop(0)
        return SimpleNamespace(method=method,payload=Payload(body))
