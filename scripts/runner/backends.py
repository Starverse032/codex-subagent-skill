from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from .store import read_json, write_json
from . import VERSION

def store_executables():
    if os.name != 'nt':
        return []
    shell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = ('[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); '
              'Get-AppxPackage -Name OpenAI.Codex | Sort-Object Version -Descending | '
              'ForEach-Object { $_.InstallLocation }')
    try:
        result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', script],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    roots = result.stdout.decode('utf-8', 'replace').splitlines() if result.returncode == 0 else []
    return [Path(root.strip()) / 'app/resources/codex.exe' for root in roots if root.strip()]


def find_codex(explicit='') -> Path:
    explicit = explicit or os.environ.get('CODEX_SUBAGENT_CODEX', '')
    if explicit:
        path = Path(os.path.expandvars(explicit)).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f'Codex executable does not exist or is not executable: {path}. '
                             'Correct the fixed path or select automatic discovery with '
                             'python scripts/setup_runtime.py --local-cli')
        if os.name=='nt' and path.suffix.lower() not in {'.exe','.com'}:
            raise ValueError('pass the native codex.exe, not a batch or PowerShell shim')
        return path
    path_name = shutil.which('codex')
    candidates = []
    if path_name:
        path = Path(path_name).resolve()
        if os.name == 'nt' and path.suffix.lower() not in {'.exe', '.com'}:
            candidates.extend(path.parent.glob('node_modules/@openai/codex*/**/codex.exe'))
        else:
            candidates.append(path)
    if os.name == 'nt':
        candidates.extend(store_executables())
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            try:
                local = str(Path.home() / 'AppData/Local')
            except RuntimeError:
                # A host sandbox may omit the profile environment variables.
                local = ''
        if local:
            root = Path(local) / 'OpenAI/Codex/bin'
            candidates.append(root / 'codex.exe')
            candidates.extend(sorted(root.glob('*/codex.exe'), key=lambda p: p.stat().st_mtime, reverse=True))
    for path in candidates:
        try:
            if path.is_file() and os.access(path, os.X_OK):
                return path.resolve()
        except OSError:
            continue
    raise ValueError('Codex CLI not found; install it, expose it to this host or pass --codex-path')


def resolve_runtime(explicit, config_path) -> Path:
    if explicit:
        return find_codex(explicit)
    local = read_json(config_path, {})
    if not isinstance(local, dict):
        raise ValueError(f'invalid runtime configuration: {config_path}')
    path = local.get('codex_path', '')
    mode = local.get('mode')
    legacy_store = mode == 'existing-cli' and re.search(r'(?:^|[/\\])WindowsApps[/\\]OpenAI\.Codex_', path, re.I)
    if mode == 'fixed' and not path:
        raise ValueError(f'fixed runtime requires codex_path in {config_path}')
    return find_codex('' if mode == 'auto' or legacy_store else path)


def probe_codex(executable, cwd=None) -> str:
    try:
        result = subprocess.run([str(executable), '--version'], cwd=cwd, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f'cannot execute Codex CLI at {executable}: {exc}. Check host permissions or configure --codex-path.') from exc
    if result.returncode != 0:
        detail = result.stderr.decode('utf-8', 'replace').strip()[:500]
        raise ValueError(f'Codex CLI probe failed at {executable} (exit {result.returncode}): {detail}')
    return result.stdout.decode('utf-8', 'replace').strip()

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
    for item in req.get('config', []):
        cmd += ['-c', item]
    roots = list(req.get('add_dirs', []))
    if req.get('structured_output') and req['sandbox'] == 'workspace-write':
        roots.append(req['run_dir'])
    roots = list(dict.fromkeys(roots))
    if roots and resume and req['sandbox'] == 'workspace-write':
        cmd += ['-c', 'sandbox_workspace_write.writable_roots='+json.dumps(roots, ensure_ascii=False)]
    elif roots and not resume and req['sandbox'] == 'workspace-write':
        for path in roots:
            cmd += ['--add-dir', path]
    for path in req.get('input_images', []):
        cmd += ['-i', path]
    if req.get('ephemeral'):
        cmd += ['--ephemeral']
    if req.get('structured_output'):
        cmd += ['--output-schema', str(Path(req['run_dir'])/'response-schema.json')]
    cmd += ['--json', '-o', str(Path(req['run_dir'])/'last-message.md'), '--', prompt]
    return cmd

def empty_execution():
    return {'status': 'failed', 'thread_id': '', 'turn_id': '', 'final_message': '',
            'final_message_source': '', 'usage': {}, 'tool_calls': [],
            'generation_sources': [], 'generation_evidence': [], 'warnings': [], 'errors': [],
            'codex_returncode': None, 'changed_files': [], 'last_event_at': None,
            'completed_tool_count': 0}

def progress(run, state, phase, detail='', *, event=False):
    if event:
        state['last_event_at'] = time.time()
    try:
        write_json(run/'progress.json', {'thread_id':state['thread_id'], 'turn_id':state['turn_id'],
                   'phase':phase, 'detail':detail[:80], 'recent_action':detail[:80],
                   'completed_tool_count':state.get('completed_tool_count', 0),
                   'last_event_at':state.get('last_event_at'), 'updated_at':time.time()}, attempts=1)
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
        state['completed_tool_count'] += 1
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
            state['completed_tool_count'] += 1
            state['tool_calls'].append({
                'name': kind, 'label': str(item.get('command') or item.get('tool') or kind)[:4000],
                'exit': item.get('exitCode', item.get('exit_code')), 'status': item.get('status'),
                'output_head': str(item.get('aggregatedOutput') or item.get('aggregated_output') or '')[:2000]})
    if kind == 'filechange' and item.get('status') not in {'failed', 'in_progress'}:
        for change in item.get('changes') or ():
            if isinstance(change, dict) and isinstance(change.get('path'), str):
                if change['path'] not in state['changed_files']:
                    state['changed_files'].append(change['path'])

def save_progress(run, state, phase, action='', *, event=True):
    progress(run, state, phase, action, event=event)
    try:
        write_json(run/'backend-progress.json', state, attempts=1)
    except OSError:
        pass  # The event journal and the final result remain independent of progress snapshots.


def writer_busy(message, thread_id):
    if not thread_id:
        return False
    text = str(message).casefold()
    return bool(re.search(r'\bthread\s+[`\'\"]?'+re.escape(thread_id.casefold())+
                          r'[`\'\"]?\s+already has an active writer\b', text))


def busy_result(state, thread_id):
    state.update(status='thread_busy', thread_id=thread_id,
                 errors=[f'Thread {thread_id} already has an active writer. '
                         'Release the writing client before retrying the resume.'])
    return state


def soft_interrupt(proc):
    if proc.poll() is not None:
        return False
    if os.name != 'nt':
        proc.send_signal(signal.SIGINT)
        return True
    # Each CLI process has a hidden private console. Signal only that console;
    # the supervisor still owns and can terminate the entire job if needed.
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.FreeConsole()
    if not kernel.AttachConsole(proc.pid):
        return False
    try:
        if not kernel.SetConsoleCtrlHandler(None, True):
            return False
        return bool(kernel.GenerateConsoleCtrlEvent(0, 0))
    finally:
        kernel.FreeConsole()


def cli_attempt(req, run, command, prompt):
    state = empty_execution()
    stop_watcher = threading.Event()
    launch = {}
    if os.name == 'nt':
        info = subprocess.STARTUPINFO()
        info.dwFlags = subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        launch.update(startupinfo=info, creationflags=subprocess.CREATE_NEW_CONSOLE)
        command = [sys.executable, '-B', str(Path(__file__).with_name('processes.py')), '_console_cli', *command]
    with (run/'cli-stderr.log').open('wb') as err, (run/'events.jsonl').open('a', encoding='utf-8') as events:
        proc = subprocess.Popen(command, cwd=req['workdir'], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=err, **launch)
        def send():
            try:
                proc.stdin.write(prompt.encode('utf-8') if prompt is not None else (run/'prompt.md').read_bytes())
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        def watch():
            while not stop_watcher.wait(.1):
                if (run/'cancel.request').exists():
                    try:
                        signaled = soft_interrupt(proc)
                    except OSError:
                        signaled = False
                    status = 'sent' if signaled else 'unavailable'
                    try:
                        (run/'cli-soft-interrupt').write_text(status, encoding='utf-8')
                    except OSError:
                        pass
                    return

        sender = threading.Thread(target=send, daemon=True)
        watcher = threading.Thread(target=watch, daemon=True)
        sender.start()
        watcher.start()
        terminal = None
        failed_turn = False
        turn_observed = False
        last_publish = 0
        recent_action = ''
        try:
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
                action = kind if kind in {'threadstarted', 'turnstarted', 'turncompleted', 'turnfailed'} else ''
                if kind == 'threadstarted':
                    state['thread_id'] = event.get('thread_id', '')
                elif kind == 'turnstarted':
                    turn_observed = True
                    state['turn_id'] = event.get('turn_id', '')
                elif kind == 'itemcompleted' and isinstance(event.get('item'), dict):
                    turn_observed = True
                    consume_item(event['item'], state)
                    action = event['item'].get('type', '')
                    action = action if action in {'command_execution', 'file_change', 'mcp_tool_call',
                                                  'web_search', 'agent_message', 'image_generation',
                                                  'reasoning', 'todo_list'} else 'item_completed'
                elif kind.startswith('item'):
                    turn_observed = True
                elif kind == 'turncompleted':
                    terminal = 'completed'
                    turn_observed = True
                    state['usage'] = event.get('usage', {})
                elif kind in {'error', 'turnfailed'}:
                    if kind == 'turnfailed':
                        failed_turn = True
                        terminal = 'failed'
                    state['errors'].append(str(event.get('error') or event.get('message') or event))
                state['turn_observed'] = turn_observed
                state['turn_terminal'] = 'failed' if failed_turn else terminal
                if action or kind == 'error':
                    recent_action = action or 'error'
                    save_progress(run, state, 'running', recent_action)
                    last_publish = time.monotonic()
                else:
                    state['last_event_at'] = time.time()
                    if time.monotonic() - last_publish >= 1:
                        progress(run, state, 'running', recent_action)
                        last_publish = time.monotonic()
            state['codex_returncode'] = proc.wait()
        finally:
            stop_watcher.set()
            watcher.join(timeout=.2)
            sender.join(timeout=.2)
            if hasattr(proc.stdout, 'close'):
                proc.stdout.close()
    last = run/'last-message.md'
    last_message = last.read_text('utf-8').strip() if last.is_file() else ''
    if last_message:
        state['final_message'] = last_message
        state['final_message_source'] = 'output_last_message'
    state['thread_id'] = state['thread_id'] or req.get('resume', '')
    if state['codex_returncode'] == 0 and terminal == 'completed' and not failed_turn and state['final_message']:
        state['status'] = 'completed'
    elif state['codex_returncode'] != 0 and not turn_observed and not state['final_message']:
        with (run/'cli-stderr.log').open('rb') as err:
            err.seek(max(0, (run/'cli-stderr.log').stat().st_size - 65536))
            stderr = err.read().decode('utf-8', 'replace')
        if writer_busy('\n'.join([stderr, *state['errors']]), req.get('resume')):
            return busy_result(state, req['resume'])
        state['errors'].append(f'CLI exited before an observable turn (rc={state["codex_returncode"]}); see cli-stderr.log')
    else:
        state['errors'].append(f'CLI did not complete a turn with a final response (rc={state["codex_returncode"]}, terminal={terminal})')
    return state


def cli_backend(req: dict, run: Path, *, schema=None, prompt=None) -> dict:
    if schema is not None:
        write_json(run/'response-schema.json', schema)
    command = cli_command({**req, 'structured_output': schema is not None or req.get('structured_output')})
    write_json(run/'command.json', command[:-1] + ['<prompt supplied through stdin>'])
    started = time.monotonic()
    state = cli_attempt(req, run, command, prompt)
    delay = .25
    while state['status'] == 'thread_busy' and not (run/'cancel.request').exists():
        if time.monotonic() - started + delay >= 5:
            break
        save_progress(run, state, 'waiting_for_writer', 'thread_busy', event=False)
        time.sleep(delay)
        if (run/'cancel.request').exists():
            break
        state = cli_attempt(req, run, command, prompt)
        delay = min(1, delay * 2)
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
            roots = list(req.get('add_dirs', []))
            if schema is not None:
                roots.append(req['run_dir'])
            if roots:
                params['config'] = {'sandbox_workspace_write.writable_roots': list(dict.fromkeys(roots))}
        if req.get('resume'):
            busy_deadline = time.monotonic() + 5
            delay = .25
            while True:
                try:
                    response = client.thread_resume(req['resume'], params)
                    break
                except Exception as exc:
                    if getattr(exc, 'code', None) != -32600 or not writer_busy(exc, req['resume']):
                        raise
                    if (run/'cancel.request').exists() or time.monotonic() + delay >= busy_deadline:
                        return busy_result(state, req['resume'])
                    state['thread_id'] = req['resume']
                    save_progress(run, state, 'waiting_for_writer', 'thread_busy', event=False)
                    time.sleep(delay)
                    delay = min(1, delay * 2)
        else:
            params['ephemeral'] = req.get('ephemeral', False)
            if state.get('codex_project_id'):
                params['projectId'] = state['codex_project_id']
            response = client.thread_start(params)
        state['thread_id'] = ids['thread'] = response.thread.id
        if state.get('codex_project_id'):
            verify_membership(client, response.thread, state['codex_project_id'], resume=bool(req.get('resume')))
        save_progress(run, state, 'starting_turn', event=False)
        input_items = [{'type':'text', 'text':prompt if prompt is not None else (run/'prompt.md').read_text('utf-8'), 'text_elements':[]}]
        input_items += [{'type':'localImage', 'path':path} for path in req.get('input_images', [])]
        turn_params = {'outputSchema': schema} if schema is not None else {}
        if req.get('effort'):
            turn_params['effort'] = req['effort']
        if (run/'cancel.request').exists():
            state['status'] = 'interrupted'
            return state
        state['turn_observed'] = True  # A disconnected start may already have reached the server.
        save_progress(run, state, 'starting_turn', event=False)
        started = client.turn_start(ids['thread'], input_items, turn_params)
        state['turn_id'] = ids['turn'] = started.turn.id
        save_progress(run, state, 'running', event=False)
        watcher.start()
        client.register_turn_notifications(ids['turn'])
        notifications = queue.Queue()

        def read_notifications():
            try:
                while not done.is_set():
                    event = client.next_turn_notification(ids['turn'])
                    notifications.put(('event', event))
                    if event.method == 'turn/completed':
                        return
            except Exception as exc:
                notifications.put(('reader_error', str(exc)))

        def inspect_turn():
            try:
                response = client.thread_read(ids['thread'], include_turns=True)
                thread = response.thread.model_dump(mode='json', by_alias=True, exclude_none=True)
                turn = next((item for item in thread.get('turns', []) if item.get('id') == ids['turn']), {})
                notifications.put(('query', turn))
            except Exception as exc:
                notifications.put(('query_error', str(exc)))

        threading.Thread(target=read_notifications, daemon=True).start()
        last_received = time.monotonic()
        next_query = last_received + 30
        querying = False
        with (run/'events.jsonl').open('w', encoding='utf-8') as events:
            while True:
                try:
                    kind, event = notifications.get(timeout=.25)
                except queue.Empty:
                    if not querying and time.monotonic() >= max(next_query, last_received + 30):
                        querying = True
                        threading.Thread(target=inspect_turn, daemon=True).start()
                    continue
                if kind == 'reader_error':
                    state['warnings'].append(f'SDK notification reader stopped: {event}')
                    last_received, next_query = time.monotonic() - 30, time.monotonic()
                    continue
                if kind.startswith('query'):
                    querying = False
                    next_query = time.monotonic() + 30
                    if kind == 'query_error':
                        warning = f'SDK turn status query failed: {event}'
                        if warning not in state['warnings']:
                            state['warnings'].append(warning)
                    elif event.get('status') in {'completed', 'interrupted', 'failed'}:
                        # Recover the final text without reapplying notifications already processed.
                        for item in event.get('items', []):
                            if norm(item.get('type')) in {'agentmessage', 'assistantmessage'}:
                                consume_item(item, state)
                        state['status'] = event['status']
                        state['turn_terminal'] = event['status']
                        if event.get('error'):
                            state['errors'].append(str(event['error']))
                        state['warnings'].append('SDK terminal turn recovered by thread/read after notification silence')
                        save_progress(run, state, 'running', 'turn_reconciled', event=False)
                        break
                    save_progress(run, state, 'running', 'turn_reconciling', event=False)
                    continue
                last_received = time.monotonic()
                payload = event.payload
                body = payload.model_dump(mode='json', by_alias=True, exclude_none=True) if hasattr(payload, 'model_dump') else getattr(payload, 'params', {})
                if event.method in {'item/started', 'item/completed'} and body.get('item', {}).get('type') == 'imageGeneration':
                    body['item'].pop('result', None)  # can be very large base64 data
                events.write(json.dumps({'method':event.method, 'params':body}, ensure_ascii=False)+'\n')
                events.flush()
                if event.method == 'item/completed' and isinstance(body.get('item'), dict):
                    consume_item(body['item'], state)
                    save_progress(run, state, 'running', 'item_completed')
                elif event.method == 'thread/tokenUsage/updated':
                    raw = body.get('tokenUsage', {}).get('last', {})
                    state['usage'] = {re.sub(r'(?<!^)(?=[A-Z])','_',k).lower():v for k,v in raw.items()}
                    save_progress(run, state, 'running', 'usage_updated')
                elif event.method == 'turn/completed':
                    turn = body['turn']
                    state['status'] = turn['status']
                    state['turn_terminal'] = turn['status']
                    if turn.get('error'):
                        state['errors'].append(str(turn['error']))
                    save_progress(run, state, 'running', 'turn_completed')
                    break
                else:
                    progress(run, state, 'running', 'notification', event=True)
        if state['status'] == 'completed' and not state['final_message']:
            state['status'] = 'failed'
            state['errors'].append('turn completed without a final response')
        (run/'last-message.md').write_text(state['final_message'], encoding='utf-8')
    finally:
        done.set()
        try:
            client.close()
        except OSError as exc:
            state['warnings'].append(f'could not close SDK transport cleanly: {exc}')
    return state

def backend_main(run: Path,plan: dict):
    request=plan['request']
    try:
        func = sdk_backend if request['backend'] == 'sdk' else cli_backend
        state=func(request,run,schema=plan['schema'],prompt=plan['prompt'])
    except Exception as exc:
        state = read_json(run/'backend-progress.json') or empty_execution()
        state['status'] = ('status_unknown' if request['backend'] == 'sdk' and state.get('turn_observed')
                           and not state.get('turn_terminal') else 'failed')
        saved=read_json(run/'progress.json',{})
        state.update({k:saved[k] for k in ('thread_id','turn_id') if saved.get(k)})
        state['errors'].append(f'{type(exc).__name__}: {exc}')
    write_json(run/'backend-result.json', state)
