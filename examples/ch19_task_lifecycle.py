"""Deterministic teaching model of selected AX Task lifecycle decisions.

No network, Redis, AX, Substrate, real clock, locks or snapshots are used.
Probe replies and failure points are explicit inputs. This is not a client
or a replacement implementation of AX reconciliation.
"""
from dataclasses import dataclass, replace
import json


@dataclass(frozen=True)
class Task:
    phase: str = 'Suspended'
    workspace_ready: bool = False
    ready: bool = False


def finish_resume(task: Task, probe_replies: list[bool]) -> tuple[Task, int]:
    workspace_ready = task.workspace_ready
    probes = 0
    if not workspace_ready:
        for reply in probe_replies:
            probes += 1
            if reply:
                workspace_ready = True
                break
    updated = replace(task, phase='Running',
                      workspace_ready=workspace_ready, ready=workspace_ready)
    return updated, probes


class InterruptedDelete(RuntimeError):
    pass


class Lifecycle:
    def __init__(self):
        self.task = None
        self.actor_exists = False
        self.actor_running = False
        self.templates = 0
        self.last_probes = 0
        self.warnings = []

    def _require_task(self):
        if self.task is None:
            raise LookupError('Task not found')

    def create(self):
        if self.task is not None:
            raise ValueError('Task already exists and is immutable')
        self.task = Task()
        self.actor_exists = True
        self.templates = 1

    def resume(self, probe_replies):
        self._require_task()
        if not probe_replies or any(type(reply) is not bool for reply in probe_replies):
            raise ValueError('supply at least one boolean probe reply')
        self.actor_exists = True
        self.actor_running = True
        self.task, self.last_probes = finish_resume(self.task, probe_replies)

    def suspend(self, *, runtime_accepts=True):
        self._require_task()
        if runtime_accepts:
            self.actor_running = False
        else:
            self.warnings.append('SuspendActor failed')
        self.task = replace(self.task, phase='Suspended', ready=False)
        self.last_probes = 0

    def delete(self, *, actor_delete_ok=True, templates_delete_ok=True,
               interrupt_after_actor=False):
        self._require_task()
        self.task = replace(self.task, phase='Terminating')
        self.last_probes = 0
        if not actor_delete_ok:
            raise InterruptedDelete('actor deletion failed')
        self.actor_exists = False
        self.actor_running = False
        if interrupt_after_actor:
            raise InterruptedDelete('stopped after deleting actor')
        if templates_delete_ok:
            self.templates = 0
        else:
            self.warnings.append('template cleanup failed')
        self.task = None


def demo():
    lifecycle = Lifecycle()
    lifecycle.create()
    created = {'phase': lifecycle.task.phase, 'ready': lifecycle.task.ready}
    lifecycle.resume([False, True])
    first = {'phase': lifecycle.task.phase, 'ready': lifecycle.task.ready,
             'workspace_ready': lifecycle.task.workspace_ready, 'probes': lifecycle.last_probes}
    lifecycle.suspend()
    paused = {'phase': lifecycle.task.phase, 'ready': lifecycle.task.ready,
              'workspace_ready': lifecycle.task.workspace_ready}
    lifecycle.resume([True])
    second = {'phase': lifecycle.task.phase, 'ready': lifecycle.task.ready,
              'workspace_ready': lifecycle.task.workspace_ready, 'probes': lifecycle.last_probes}
    lifecycle.delete()
    deleted = {'task_exists': lifecycle.task is not None,
               'actor_exists': lifecycle.actor_exists, 'templates': lifecycle.templates}
    return {'create': created, 'first_resume': first, 'suspend': paused,
            'second_resume': second, 'delete': deleted}


if __name__ == '__main__':
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
