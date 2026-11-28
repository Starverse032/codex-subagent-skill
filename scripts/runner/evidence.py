"""Observe changes made in the worktree while a task is running."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess


def git(exe, workdir, *args):
    result = subprocess.run([exe, '-C', str(workdir), *args], capture_output=True,
                            stdin=subprocess.DEVNULL,
                            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, timeout=8)
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', 'replace').strip())
    return result.stdout


def signature(path, root):
    if not path.exists() and not path.is_symlink():
        return 'missing'
    if path.is_symlink():
        return 'link:' + os.readlink(path)
    if path.resolve() != path or not path.is_file():
        return 'uninspected'
    path.relative_to(root)
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def dirty(exe, workdir, run):
    status=git(exe,workdir,'status','--porcelain=v1','-z','--untracked-files=all','--no-renames','--','.')
    index=git(exe,workdir,'diff','--cached','--raw','-z','--no-renames','--','.')
    items=iter(index.split(b'\x00'))
    staged={os.fsdecode(path):meta for meta,path in zip(items,items)}
    output={}
    for record in status.split(b'\x00'):
        if not record:
            continue
        name=os.fsdecode(record[3:])
        path=workdir/name
        if path==run or run in path.parents:
            continue
        output[name]=(record[:2],signature(path,workdir),staged.get(name))
    return output


def head(exe,workdir):
    try:
        return git(exe,workdir,'rev-parse','--verify','HEAD').decode().strip()
    except ValueError:
        return ''


def snapshot(workdir,run):
    exe=shutil.which('git')
    if not exe:
        return {'coverage':'git_unavailable'}
    try:
        top=Path(os.fsdecode(git(exe,workdir,'rev-parse','--show-toplevel').rstrip())).resolve()
        return {'exe':exe,'root':top,'head':head(exe,top),'dirty':dirty(exe,top,run)}
    except (OSError,ValueError,subprocess.SubprocessError):
        return {'coverage':'git_snapshot_unavailable'}


def collect_changes(workdir,run,before,execution):
    if 'exe' not in before:
        paths=sorted(set(execution.get('changed_files',[])))
        return {'changed_files':paths,'diff_stat':{'files_changed':len(paths),
                'source':'file_change_events','coverage':'partial','note':before['coverage']}}
    exe,top=before['exe'],before['root']
    after=dirty(exe,top,run)
    new_head=head(exe,top)
    changed={name for name in before['dirty'].keys() | after.keys()
             if before['dirty'].get(name)!=after.get(name)}
    if before['head']!=new_head:
        if before['head'] and new_head:
            data=git(exe,top,'diff','--name-only','-z','--no-renames',before['head'],new_head,'--')
        elif new_head:
            data=git(exe,top,'ls-tree','-r','--name-only','-z',new_head)
        else:
            data=git(exe,top,'ls-tree','-r','--name-only','-z',before['head'])
        changed.update(os.fsdecode(name) for name in data.split(b'\x00') if name)
    paths=sorted(changed)
    return {'changed_files':paths,'diff_stat':{'files_changed':len(paths),
            'source':'git_snapshots','path_base':str(top),'head_changed':before['head']!=new_head,
            'coverage':'worktree_window','attribution':'changes observed during the run'}}
