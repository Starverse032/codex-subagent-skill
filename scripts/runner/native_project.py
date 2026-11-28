"""Codex App Server project protocol, negotiated as an experimental capability."""
from pydantic import BaseModel, Field

from .dispatch import project_record


class Project(BaseModel):
    id: str
    name: str


class ProjectResponse(BaseModel):
    project: Project


class ThreadProject(BaseModel):
    project_id: str | None = Field(default=None, alias='projectId')


class ThreadProjectResponse(BaseModel):
    thread: ThreadProject


def ensure_project(client, request):
    group = request['project']
    bound = request.get('codex_project_id')
    if bound:
        response = client.request('project/read', {'projectId': bound}, response_model=ProjectResponse)
    else:
        response = client.request('project/create', {
            'name': group['title'],
            # Membership organizes sessions. Each turn gets its actual cwd and sandbox
            # separately, so creating the project need not grant folder trust.
            'roots': [],
            'metadata': {'codex-subagent.conversation': group['key']},
            'idempotencyKey': group['key'],
        }, response_model=ProjectResponse)
    if not response.project.id or response.project.name != group['title']:
        raise ValueError('Codex returned an unexpected project title or ID')
    project_record(group, response.project.id, path=request.get('state_dir'))
    return response.project.id


def verify_membership(client, thread, project_id, *, resume):
    assigned = getattr(thread, 'project_id', None)
    if assigned == project_id:
        return
    if resume and assigned is None:
        response = client.request('thread/metadata/update', {
            'threadId': thread.id, 'projectId': project_id,
        }, response_model=ThreadProjectResponse)
        if response.thread.project_id == project_id:
            return
    raise ValueError('Codex did not confirm this thread belongs to its conversation project')
