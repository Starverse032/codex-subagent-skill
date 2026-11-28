from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import time
import warnings

from .processes import OwnedProcess
from .store import contained, read_json, write_json, owned_artifact_root, owned_file, plain_directory

RESPONSE_SCHEMA = {
    'type':'object', 'properties':{
        'summary':{'type':'string'},
        'artifacts':{'type':'array','items':{
            'type':'object', 'properties':{'path':{'type':'string'}, 'kind':{'type':'string'}},
            'required':['path','kind'], 'additionalProperties':False}}},
    'required':['summary','artifacts'], 'additionalProperties':False}

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda:file.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def probe_image(path: Path) -> dict:
    from PIL import Image
    result = {'valid':False, 'kind':'', 'width':0, 'height':0,
              'bytes':path.stat().st_size, 'validation':'decoded'}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as image:
                result.update(kind=(image.format or '').lower(), width=image.width, height=image.height)
                image.verify()
            with Image.open(path) as image:
                image.load()
            result['valid'] = result['kind'] in {'png','jpeg'} and min(result['width'],result['height']) > 0
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        pass
    return result

def validate_schema(schema: dict, value):
    import jsonschema
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    # No automatic retrieval of remote schemas from untrusted output schemas.
    from referencing import Registry
    cls(schema, registry=Registry()).validate(value)


def validate_builtin(value):
    if (not isinstance(value,dict) or set(value)!={'summary','artifacts'}
            or not isinstance(value['summary'],str) or not isinstance(value['artifacts'],list)):
        raise ValueError('expected a summary string and an artifacts array')
    for item in value['artifacts']:
        if (not isinstance(item,dict) or set(item)!={'path','kind'}
                or not all(isinstance(part,str) for part in item.values())):
            raise ValueError('each artifact must contain only a path and kind string')

def failed_verification(message):
    return {'status':'failed','errors':[message],'artifacts':[],'images':[],
            'warnings':[],'agent_output':None}

def collect_generated(source,expected,owned,index):
    """Reuse identical files; preserve conflicts using an exclusive new filename."""
    suffix=source.suffix.lower()
    stem=f'generated-{index+1:02d}'
    for attempt in range(1024):
        plain_directory(owned)
        name=stem if attempt==0 else f'{stem}-{expected[:12]}'+(f'-{attempt}' if attempt>1 else '')
        target=owned/f'{name}{suffix}'
        try:
            existing=owned_file(target,owned)
        except (ValueError,FileNotFoundError):
            existing=None
        if existing and digest(existing)==expected:
            return existing
        try:
            with target.open('xb') as dst,source.open('rb') as src:
                shutil.copyfileobj(src,dst)
        except FileExistsError:
            continue
        if digest(target)!=expected:
            raise ValueError('generation output changed while it was being copied')
        return owned_file(target,owned)
    raise ValueError('too many conflicting generation output filenames')

def verify_artifacts(req: dict, execution: dict, run: Path, *, schema):
    errors, warnings_out, records, images = [], [], [], []
    try:
        owned=owned_artifact_root(run)
        answer = json.loads(execution['final_message'])
        if req.get('custom_schema'):
            validate_schema(schema, answer)
        else:
            validate_builtin(answer)
    except Exception as exc:
        return failed_verification(f'final output validation failed: {exc}')
    declarations = answer.get('artifacts', []) if not req.get('custom_schema') else read_json(run/'artifact-declarations.json', [])
    if not isinstance(declarations, list):
        errors.append('artifact declarations must be an array')
        declarations = []
    evidence=execution.get('generation_evidence',[])
    generated_hashes={e['sha256'] for e in evidence}
    # Hashes were captured on completed generation events, before later task/check edits.
    for index, record in enumerate(evidence):
        try:
            source=Path(record['path'])
            if not source.is_file():
                continue
            if digest(source)!=record['sha256']:
                warnings_out.append(f'generation source changed after the completed tool event: {source.name}')
                continue
            if req['gen_image']:
                suffix = source.suffix.lower()
                if suffix not in {'.png','.jpg','.jpeg'}:
                    continue
                target=collect_generated(source,record['sha256'],owned,index)
                declarations.append({'path':str(target),'kind':'image'})
        except (OSError,ValueError) as exc:
            warnings_out.append(f'could not collect generation tool output: {exc}')
    seen_paths, seen_images = set(), set()
    for declaration in declarations:
        try:
            if not isinstance(declaration, dict) or not isinstance(declaration.get('path'), str):
                raise ValueError('artifact must contain a string path')
            path = Path(declaration['path'])
            path = path if path.is_absolute() else owned/path
            path=owned_file(path,owned)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            sha = digest(path)
            record = {'path':str(path), 'sha256':sha, 'bytes':path.stat().st_size,
                      'kind':declaration.get('kind','file')}
            records.append(record)
            if req.get('gen_image') and path.suffix.lower() in {'.png','.jpg','.jpeg'}:
                image = {'dst':str(path), 'src':str(path), 'sha256':sha,
                         'confidence':'high' if sha in generated_hashes else 'unverified',
                         'visual_review':'required', **probe_image(path)}
                if sha in seen_images:
                    continue
                seen_images.add(sha)
                images.append(image)
                if not image['valid']:
                    errors.append(f'image cannot be decoded: {path.name}')
                if req['gen_image'] and sha not in generated_hashes:
                    errors.append(f'image has no matching completed image-generation tool output: {path.name}')
                if req.get('image_size') and (image['width'],image['height']) != tuple(req['image_size']):
                    warnings_out.append(f'image size differs from requested composition: {path.name}')
                expected='jpeg' if req.get('image_format') in {'jpg','jpeg'} else req.get('image_format')
                if expected and image['kind']!=expected:
                    warnings_out.append(f'image format differs from requested {expected}; original bytes retained: {path.name}')
        except (OSError, ValueError) as exc:
            errors.append(f'invalid artifact {declaration!r}: {exc}')
    if req['gen_image']:
        verified = [i for i in images if i['valid'] and i['confidence']=='high']
        if len(verified) != req['image_count']:
            errors.append(f'expected {req["image_count"]} generated image(s); verified {len(verified)}')
    # Export is additive and collision-safe; never remove or replace user files.
    if req.get('image_dir'):
        dest = Path(req['image_dir'])
        dest.mkdir(parents=True, exist_ok=True)
        for index, image in enumerate(images):
            if not image['valid'] or (req['gen_image'] and image['confidence'] != 'high'):
                continue
            source = Path(image['dst'])
            name = f'{req["run_id"]}-{index+1:02d}{source.suffix.lower()}'
            target = dest/name
            try:
                with target.open('xb') as handle, source.open('rb') as src:
                    shutil.copyfileobj(src, handle)
                image['exported_to'] = str(target)
            except FileExistsError:
                warnings_out.append(f'export target exists; kept existing file: {target}')
    write_json(run/'artifact-manifest.json', records)
    return {'status':'failed' if errors else 'passed', 'errors':errors,
            'warnings':warnings_out, 'artifacts':records, 'images':images, 'agent_output':answer,
            'scope':'schema, files, image decoding and generation provenance; visual content requires host review'}

def run_checks(req: dict, run: Path, deadline: float):
    records = []
    errors = []
    for index, argv in enumerate(req['checks']):
        if (run/'cancel.request').exists():
            errors.append('verification cancelled')
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append('verification deadline exceeded')
            break
        path = run/f'check-{index+1:02d}.log'
        with path.open('wb') as log:
            child = OwnedProcess(argv, cwd=Path(req['workdir']), stdout=log, stderr=log)
            reason = None
            try:
                while child.proc.poll() is None:
                    if (run/'cancel.request').exists():
                        reason = 'cancelled'
                        break
                    if time.monotonic() >= deadline:
                        reason = 'timeout'
                        break
                    time.sleep(.05)
                code = child.proc.poll()
            finally:
                child.close()
        records.append({'index':index+1,'exit_code':code, 'log':path.name, 'stop_reason':reason,
                        'status':'failed' if reason or code!=0 else 'passed'})
        if reason or code != 0:
            errors.append(f'acceptance command {index+1} failed: {reason or code}')
            break
    records.extend({'index':index+1,'status':'not_run'} for index in range(len(records),len(req['checks'])))
    files=[]
    for raw in req['required_files']:
        path = Path(raw)
        path = path if path.is_absolute() else Path(req['workdir'])/path
        present=contained(path, Path(req['workdir'])) and path.is_file()
        files.append({'path':raw,'status':'passed' if present else 'failed'})
        if not present:
            errors.append(f'required file missing or outside workdir: {raw}')
    return records, files, errors

def verifier_main(run: Path,plan: dict):
    """All checks and potentially blocking decoding/copies run in an owned process."""
    req=plan['request']
    execution=plan['execution']
    from .backends import progress
    progress_state={'thread_id':execution.get('thread_id',''),'turn_id':execution.get('turn_id',''),
                    'last_event_at':execution.get('last_event_at'),
                    'completed_tool_count':execution.get('completed_tool_count',0),'warnings':[]}
    try:
        owned_artifact_root(run)
        progress(run,progress_state,'verifying','running caller acceptance commands')
        checks,files,errors=run_checks(req,run,plan['deadline'])
        if (run/'cancel.request').exists() or time.monotonic()>=plan['deadline']:
            verification=failed_verification('verification stopped before final artifact checks')
        elif plan['schema'] is not None:
            progress(run,progress_state,'verifying','validating final artifacts')
            verification=verify_artifacts(req,execution,run,schema=plan['schema'])
        else:
            verification={'status':'passed','errors':[],'warnings':[],'artifacts':[],
                          'images':[],'agent_output':None}
        verification['checks']=checks
        verification['required_files']=files
        verification['errors']+=errors
        if verification['errors']: verification['status']='failed'
    except Exception as exc:
        verification=failed_verification(f'verification failed: {type(exc).__name__}: {exc}')
        verification['checks']=[]
    plain_directory(run)
    verification['warnings']+=progress_state['warnings']
    write_json(run/'verification-result.json',verification)
