from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from ch16_dual_write_demo import RecordDB, ImmutableTask, SimulatedCrash, recovery_plan, resume_then_crash, demo


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.task={"key":"team-a/review-42","launch":"a","phase":"Suspended"}
        self.actor={"key":self.task["key"],"launch":"a","uid":"u1","state":"Running"}

    def plan(self,task=None,actor=None,**kwargs):
        return recovery_plan(self.task if task is None else task,self.actor if actor is None else actor,
                             expected_uid=kwargs.get("uid","u1"),requested_launch=kwargs.get("launch","a"))

    def test_existing_running_actor(self):self.assertEqual(self.plan(),"REVIEW_EXISTING_BEFORE_RESUME")
    def test_existing_suspended_actor(self):self.assertEqual(self.plan(actor=dict(self.actor,state="Suspended")),"REVIEW_EXISTING_BEFORE_RESUME")
    def test_uid_change(self):self.assertEqual(self.plan(actor=dict(self.actor,uid="u2")),"STOP_IDENTITY_CHANGED")
    def test_key_change(self):self.assertEqual(self.plan(actor=dict(self.actor,key="other/task")),"STOP_IDENTITY_CHANGED")
    def test_missing_provenance(self):self.assertEqual(self.plan(uid=None),"STOP_MISSING_PROVENANCE")
    def test_changed_request(self):self.assertEqual(self.plan(launch="b"),"STOP_REQUEST_CHANGED")
    def test_changed_runtime_launch(self):self.assertEqual(self.plan(actor=dict(self.actor,launch="b")),"STOP_LAUNCH_CHANGED")
    def test_crashed_actor(self):self.assertEqual(self.plan(actor=dict(self.actor,state="Crashed")),"STOP_LIFECYCLE_REVIEW")
    def test_deleting_actor(self):self.assertEqual(self.plan(actor=dict(self.actor,state="Deleting")),"STOP_LIFECYCLE_REVIEW")
    def test_transitioning_actor(self):self.assertEqual(self.plan(actor=dict(self.actor,state="Resuming")),"STOP_LIFECYCLE_REVIEW")
    def test_unknown_runtime_state(self):self.assertEqual(self.plan(actor=dict(self.actor,state="Unknown")),"STOP_LIFECYCLE_REVIEW")

    def test_missing_runtime(self):
        self.assertEqual(recovery_plan(self.task,None,expected_uid="u1",requested_launch="a"),"STOP_RUNTIME_MISSING")

    def test_orphan_requires_review(self):
        self.assertEqual(recovery_plan(None,self.actor,expected_uid="u1",requested_launch="a"),"STOP_ORPHAN_REVIEW")

    def test_neither_resource(self):
        self.assertEqual(recovery_plan(None,None,expected_uid="u1",requested_launch="a"),"NO_RESOURCES")

    def test_planner_is_read_only(self):
        before=deepcopy((self.task,self.actor));self.plan()
        self.assertEqual(before,(self.task,self.actor))

    def test_phase_alone_does_not_identify_actor(self):
        self.assertEqual(self.plan(task=dict(self.task,phase="Running"),actor=dict(self.actor,uid="u2")),"STOP_IDENTITY_CHANGED")


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory=TemporaryDirectory(prefix="ax-book-ch16-test-")
        self.paths=[Path(self.directory.name)/"tasks.sqlite",Path(self.directory.name)/"actors.sqlite"]
        self.tasks,self.actors=(RecordDB(p) for p in self.paths)
        self.key="team-a/review-42"
        self.tasks.create_task(self.key,"a")
        self.actors.put(self.key,{"key":self.key,"launch":"a","uid":"u1","state":"Suspended"})

    def tearDown(self):
        self.tasks.close();self.actors.close();self.directory.cleanup()

    def test_duplicate_create_rejected(self):
        with self.assertRaises(ImmutableTask):self.tasks.create_task(self.key,"a")
        self.assertEqual(self.tasks.count(),1)

    def test_changed_spec_cannot_update_immutable_record(self):
        with self.assertRaises(ImmutableTask):self.tasks.create_task(self.key,"b")
        self.assertEqual(self.tasks.get(self.key)["launch"],"a")

    def test_runtime_commits_before_exception(self):
        with self.assertRaises(SimulatedCrash):resume_then_crash(self.tasks,self.actors,self.key)
        self.assertEqual(self.actors.get(self.key)["state"],"Running")

    def test_local_phase_was_not_saved(self):
        with self.assertRaises(SimulatedCrash):resume_then_crash(self.tasks,self.actors,self.key)
        self.assertEqual(self.tasks.get(self.key)["phase"],"Suspended")

    def test_reopen_retains_divergence(self):
        with self.assertRaises(SimulatedCrash):resume_then_crash(self.tasks,self.actors,self.key)
        self.tasks.close();self.actors.close()
        self.tasks,self.actors=(RecordDB(p) for p in self.paths)
        self.assertEqual((self.tasks.get(self.key)["phase"],self.actors.get(self.key)["state"]),("Suspended","Running"))

    def test_no_duplicate_runtime(self):
        with self.assertRaises(SimulatedCrash):resume_then_crash(self.tasks,self.actors,self.key)
        self.assertEqual(self.actors.count(),1)

    def test_get_returns_detached_value(self):
        value=self.tasks.get(self.key);value["phase"]="Running"
        self.assertEqual(self.tasks.get(self.key)["phase"],"Suspended")

    def test_missing_key(self):self.assertIsNone(self.tasks.get("unknown"))

    def test_resume_requires_both_resources(self):
        with self.assertRaises(ValueError):resume_then_crash(self.tasks,self.actors,"unknown")

    def test_planner_does_not_repair_database(self):
        with self.assertRaises(SimulatedCrash):resume_then_crash(self.tasks,self.actors,self.key)
        recovery_plan(self.tasks.get(self.key),self.actors.get(self.key),expected_uid="u1",requested_launch="a")
        self.assertEqual(self.tasks.get(self.key)["phase"],"Suspended")

    def test_demo(self):
        result=demo()
        self.assertEqual(result["reopened"],{"task_phase":"Suspended","actor_state":"Running"})
        self.assertFalse(result["planner_mutated_records"])
        self.assertFalse(result["ax_and_substrate_contacted"])
        self.assertEqual(result["changed_uid_plan"],"STOP_IDENTITY_CHANGED")
        self.assertEqual((result["task_records"],result["actor_records"]),(1,1))


if __name__=="__main__":unittest.main()
