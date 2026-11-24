from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from .store import read_json, write_json
from . import VERSION

def find_codex(explicit='') -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f'Codex executable does not exist: {path}')
        if os.name=='nt' and path.suffix.lower() not in {'.exe','.com'}:
            raise ValueError('pass the native codex.exe, not a batch or PowerShell shim')
        return path
    names = [os.environ.get('CODEX_SUBAGENT_CODEX'), shutil.which('codex')]
    for name in names:
        if name and Path(name).is_file():
            path = Path(name).resolve()
            if os.name == 'nt' and path.suffix.lower() in {'.cmd', '.bat'}:
                # npm shims use cmd.exe and do not provide shell-free argv semantics.
                native = list(path.parent.glob('node_modules/@openai/codex*/**/codex.exe'))
                if native:
                    return native[0]
                raise ValueError('PATH resolves to a batch shim; pass --codex-path to the native codex.exe')
            return path
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local'))) / 'OpenAI/Codex/bin'
    native = sorted(root.glob('*/codex.exe'), key=lambda p: p.stat().st_mtime, reverse=True) if root.is_dir() else []
    if native:
        return native[0]
    raise ValueError('Codex CLI not found; install it or pass --codex-path')

def cli_command(req: dict, prompt='-') -> list[str]:
    exe = find_codex(req.get('codex_path', ''))
    resume = req.get('resume')
    cmd = [str(exe), 'exec'] + (['resume', resume] if resume else [])
    if not req.get('require_git_repo'):
        cmd += ['--skip-git-repo-check']
    if req['approval'] == 'approve-for-me':
        if resume:
            # Global options belong before exec, including in resume mode.
            cmd = [str(exe), '--approve-for-me', *cmd[1:]]
        else:
            cmd += ['--approve-for-me']
    else:
        cmd += ['-c', f'approval_policy="{req["approval"]}"']
        cmd += (['-c', f'sandbox_mode="{req["sandbox"]}"'] if resume
                else ['-s', req['sandbox']])
    if not resume:
        cmd += ['-C', req['workdir']]
    if req.get('danger'):
        cmd += ['--dangerously-bypass-approvals-and-sandbox']
    if req.get('model'):
        cmd += ['-m', req['model']]
    if req.get('effort'):
        cmd += ['-c', f'model_reasoning_effort="{req["effort"]}"']
    if resume and req['sandbox']=='workspace-write':
        cmd += ['-c','sandbox_workspace_write.writable_roots='+json.dumps([req['run_dir']])]
    for item in req.get('config', []):
        cmd += ['-c', item]
    if not resume and req['sandbox'] == 'workspace-write':
        cmd += ['--add-dir', req['run_dir']]
    for path in req.get('input_images', []):
        cmd += ['-i', path]
    if req.get('ephemeral'):
        cmd += ['--ephemeral']
    cmd += ['--output-schema', str(Path(req['run_dir'])/'response-schema.json'),
            '--json', '-o', str(Path(req['run_dir'])/'last-message.md'), '--', prompt]
    return cmd

def empty_execution():
    return {'status': 'failed', 'thread_id': '', 'turn_id': '', 'final_message': '',
            'final_message_source': '', 'usage': {}, 'tool_calls': [],
            'generation_sources': [], 'generation_evidence': [], 'warnings': [], 'errors': [], 'codex_returncode': None}

def progress(run, state, phase, detail=''):
    try:
        write_json(run/'progress.json', {'thread_id':state['thread_id'], 'turn_id':state['turn_id'],
                   'phase':phase, 'detail':detail[:500], 'updated_at':time.time()},attempts=1)
    except OSError as exc:
        warning=f'could not publish optional progress snapshot: {exc}'
        if warning not in state['warnings']: state['warnings'].append(warning)

def norm(value):
    return re.sub('[^a-z0-9]', '', str(value).lower())

def consume_item(item: dict, state: dict):
    kind = norm(item.get('type', ''))
    if kind in {'agentmessage', 'assistantmessage'}:
        text = item.get('text', '')
        phase = item.get('phase')
        if text and (phase in {None, 'final_answer'}):
            state['final_message'] = text
            state['final_message_source'] = 'events'
    elif kind in {'imagegeneration', 'imagegenerationcall'}:
        path = item.get('savedPath') or item.get('saved_path')
        if path and item.get('status') in {'completed', 'succeeded', 'success'} and not item.get('failure'):
            state['generation_sources'].append(str(path))
            try:
                sha=hashlib.sha256()
                with Path(path).open('rb') as source:
                    for block in iter(lambda:source.read(1024*1024),b''): sha.update(block)
                state['generation_evidence'].append({'path':str(path),'sha256':sha.hexdigest()})
            except OSError as exc:
                state['warnings'].append(f'could not snapshot completed generation output: {exc}')
    else:
        known = {'commandexecution', 'mcptoolcall', 'functioncall', 'customtoolcall',
                 'filechange', 'websearch', 'localtoolcall'}
        if kind in known:
            state['tool_calls'].append({
                'name': kind, 'label': str(item.get('command') or item.get('tool') or kind)[:4000],
                'exit': item.get('exitCode', item.get('exit_code')), 'status': item.get('status'),
                'output_head': str(item.get('aggregatedOutput') or item.get('aggregated_output') or '')[:2000]})

def cli_backend(req: dict, run: Path, *, schema=None, prompt=None) -> dict:
    state = empty_execution()
    if schema is not None: write_json(run/'response-schema.json',schema)
    command = cli_command(req)
    write_json(run/'command.json', command[:-1] + ['<prompt supplied through stdin>'])
    with (run/'stderr.log').open('wb') as err, (run/'events.jsonl').open('w', encoding='utf-8') as events:
        proc = subprocess.Popen(command, cwd=req['workdir'], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=err)
        def send():
            try:
                proc.stdin.write(prompt.encode('utf-8') if prompt is not None else (run/'prompt.md').read_bytes())
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        sender = threading.Thread(target=send, daemon=True)
        sender.start()
        terminal = None
        failed_turn = False
        for raw in proc.stdout:
            line = raw.decode('utf-8', 'replace')
            events.write(line)
            events.flush()
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    continue
            except ValueError:
                continue
            kind = norm(event.get('type', ''))
            if kind == 'threadstarted':
                state['thread_id'] = event.get('thread_id', '')
                progress(run,state,'running')
            elif kind == 'itemcompleted' and isinstance(event.get('item'), dict):
                consume_item(event['item'], state)
                progress(run,state,'running',str(event['item'].get('command') or event['item'].get('type','')))
            elif kind == 'turncompleted':
                terminal = 'completed'
                state['usage'] = event.get('usage', {})
            elif kind == 'turnfailed':
                failed_turn = True
                terminal = 'failed'
                state['errors'].append(str(event.get('error') or event.get('message') or event))
        state['codex_returncode'] = proc.wait()
        sender.join(timeout=1)
    last = run/'last-message.md'
    if last.is_file() and last.read_text('utf-8').strip():
        state['final_message'] = last.read_text('utf-8').strip()
        state['final_message_source'] = 'output_last_message'
    state['thread_id'] = state['thread_id'] or req.get('resume', '')
    if state['codex_returncode'] == 0 and terminal == 'completed' and not failed_turn and state['final_message']:
        state['status'] = 'completed'
    else:
        state['errors'].append(f'CLI did not complete a turn with a final response (rc={state["codex_returncode"]}, terminal={terminal})')
    return state

def sdk_backend(req: dict, run: Path, *, schema=None, prompt=None) -> dict:
    from openai_codex.client import CodexClient, CodexConfig
    state = empty_execution()
    config = CodexConfig(codex_bin=req.get('codex_path') or None, cwd=req['workdir'],
                         config_overrides=tuple(req.get('config', [])),
                         client_name='codex_subagent', client_title='Codex Subagent', client_version=VERSION)
    # Never inherit the SDK client's permissive default approval handler.
    def deny_approval(method, params):
        state['warnings'].append(f'denied unexpected client approval request: {method}')
        return {'decision': 'decline'}
    client = CodexClient(config=config, approval_handler=deny_approval)
    done = threading.Event()
    ids = {}
    def interrupt_watcher():
        while not done.wait(.1):
            if (run/'cancel.request').exists() and ids.get('turn'):
                try:
                    client.turn_interrupt(ids['thread'], ids['turn'])
                except Exception:
                    pass
                return
    watcher = threading.Thread(target=interrupt_watcher, daemon=True)
    try:
        client.start()
        client.initialize()
        if req.get('project'):
            from .native_project import ensure_project, verify_membership
            state['codex_project_id'] = ensure_project(client, req)
        params = {'cwd': req['workdir'], 'sandbox': req['sandbox'],
                  'approvalPolicy': 'on-request' if req['approval'] == 'approve-for-me' else 'never'}
        if req['approval'] == 'approve-for-me':
            params['approvalsReviewer'] = 'auto_review'
        if req.get('model'):
            params['model'] = req['model']
        if req['sandbox'] == 'workspace-write':
            params['config'] = {'sandbox_workspace_write.writable_roots': [req['run_dir']]}
        if req.get('resume'):
            response = client.thread_resume(req['resume'], params)
        else:
            params['ephemeral'] = req.get('ephemeral', False)
            if state.get('codex_project_id'):
                params['projectId'] = state['codex_project_id']
            response = client.thread_start(params)
        state['thread_id'] = ids['thread'] = response.thread.id
        if state.get('codex_project_id'):
            verify_membership(client, response.thread, state['codex_project_id'], resume=bool(req.get('resume')))
        progress(run,state,'starting_turn')
        input_items = [{'type':'text', 'text':prompt if prompt is not None else (run/'prompt.md').read_text('utf-8'), 'text_elements':[]}]
        input_items += [{'type':'localImage', 'path':path} for path in req.get('input_images', [])]
        turn_params = {'outputSchema': schema if schema is not None else read_json(run/'response-schema.json')}
        if req.get('effort'):
            turn_params['effort'] = req['effort']
        started = client.turn_start(ids['thread'], input_items, turn_params)
        state['turn_id'] = ids['turn'] = started.turn.id
        progress(run,state,'running')
        watcher.start()
        client.register_turn_notifications(ids['turn'])
        with (run/'events.jsonl').open('w', encoding='utf-8') as events:
            while True:
                event = client.next_turn_notification(ids['turn'])
                payload = event.payload
                body = payload.model_dump(mode='json', by_alias=True, exclude_none=True) if hasattr(payload, 'model_dump') else getattr(payload, 'params', {})
                if event.method in {'item/started', 'item/completed'} and body.get('item', {}).get('type') == 'imageGeneration':
                    body['item'].pop('result', None)  # can be very large base64 data
                events.write(json.dumps({'method':event.method, 'params':body}, ensure_ascii=False)+'\n')
                events.flush()
                if event.method == 'item/completed' and isinstance(body.get('item'), dict):
                    consume_item(body['item'], state)
                    progress(run,state,'running',str(body['item'].get('command') or body['item'].get('type','')))
                elif event.method == 'thread/tokenUsage/updated':
                    raw = body.get('tokenUsage', {}).get('last', {})
                    state['usage'] = {re.sub(r'(?<!^)(?=[A-Z])','_',k).lower():v for k,v in raw.items()}
                elif event.method == 'turn/completed':
                    turn = body['turn']
                    state['status'] = turn['status']
                    if turn.get('error'):
                        state['errors'].append(str(turn['error']))
                    break
        if state['status'] == 'completed' and not state['final_message']:
            state['status'] = 'failed'
            state['errors'].append('turn completed without a final response')
        (run/'last-message.md').write_text(state['final_message'], encoding='utf-8')
    finally:
        done.set()
        client.close()
    return state

def backend_main(run: Path,plan: dict):
    request=plan['request']
    try:
        func = sdk_backend if request['backend'] == 'sdk' else cli_backend
        state=func(request,run,schema=plan['schema'],prompt=plan['prompt'])
    except Exception as exc:
        state = empty_execution()
        saved=read_json(run/'progress.json',{})
        state.update({k:saved.get(k,'') for k in ('thread_id','turn_id')})
        state['errors'].append(f'{type(exc).__name__}: {exc}')
    write_json(run/'backend-result.json', state)
