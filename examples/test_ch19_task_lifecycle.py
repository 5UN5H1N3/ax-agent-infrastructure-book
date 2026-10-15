import unittest

from ch19_task_lifecycle import InterruptedDelete, Lifecycle, Task, demo, finish_resume


class ResumeDecisionTests(unittest.TestCase):
    def test_first_ready_reply_finishes_polling(self):
        task, probes = finish_resume(Task(), [False, True, False])
        self.assertEqual(task, Task('Running', True, True))
        self.assertEqual(probes, 2)

    def test_expired_window_keeps_running_not_ready(self):
        task, probes = finish_resume(Task(), [False, False])
        self.assertEqual(task, Task('Running', False, False))
        self.assertEqual(probes, 2)

    def test_sticky_readiness_skips_new_probe(self):
        task, probes = finish_resume(Task(workspace_ready=True), [False])
        self.assertTrue(task.ready)
        self.assertEqual(probes, 0)

    def test_input_task_is_unchanged(self):
        original = Task()
        finish_resume(original, [True])
        self.assertEqual(original, Task())


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.model = Lifecycle()
        self.model.create()

    def test_create_prepares_suspended_resources(self):
        self.assertEqual(self.model.task, Task())
        self.assertTrue(self.model.actor_exists)
        self.assertFalse(self.model.actor_running)
        self.assertEqual(self.model.templates, 1)

    def test_repeated_create_rejects_existing_task(self):
        with self.assertRaises(ValueError):
            self.model.create()
        self.assertEqual(self.model.templates, 1)

    def test_resume_assigns_running_actor(self):
        self.model.resume([True])
        self.assertTrue(self.model.actor_running)
        self.assertEqual(self.model.task, Task('Running', True, True))

    def test_late_readiness_requires_another_request_in_model(self):
        self.model.resume([False])
        old = self.model.task
        later_runner_reply = True
        self.assertEqual(self.model.task, old)
        self.assertFalse(self.model.task.ready)
        self.model.resume([later_runner_reply])
        self.assertTrue(self.model.task.ready)

    def test_suspend_preserves_workspace_condition(self):
        self.model.resume([True])
        self.model.suspend()
        self.assertEqual(self.model.task, Task('Suspended', True, False))
        self.assertFalse(self.model.actor_running)

    def test_second_resume_skips_poll(self):
        self.model.resume([True])
        self.model.suspend()
        self.model.resume([True])
        self.assertEqual(self.model.last_probes, 0)

    def test_failed_suspend_records_warning_and_retains_actor(self):
        self.model.resume([True])
        self.model.suspend(runtime_accepts=False)
        self.assertEqual(self.model.task.phase, 'Suspended')
        self.assertTrue(self.model.actor_running)
        self.assertEqual(self.model.warnings, ['SuspendActor failed'])

    def test_delete_removes_records_on_success(self):
        self.model.delete()
        self.assertIsNone(self.model.task)
        self.assertFalse(self.model.actor_exists)
        self.assertEqual(self.model.templates, 0)

    def test_failed_actor_delete_leaves_terminating_task(self):
        with self.assertRaises(InterruptedDelete):
            self.model.delete(actor_delete_ok=False)
        self.assertEqual(self.model.task.phase, 'Terminating')
        self.assertTrue(self.model.actor_exists)

    def test_interrupted_delete_can_be_completed(self):
        with self.assertRaises(InterruptedDelete):
            self.model.delete(interrupt_after_actor=True)
        self.assertEqual(self.model.task.phase, 'Terminating')
        self.assertFalse(self.model.actor_exists)
        self.model.delete()
        self.assertIsNone(self.model.task)

    def test_template_failure_can_leave_orphan_after_task_delete(self):
        self.model.delete(templates_delete_ok=False)
        self.assertIsNone(self.model.task)
        self.assertEqual(self.model.templates, 1)
        self.assertIn('template cleanup failed', self.model.warnings)

    def test_deleted_task_returns_not_found(self):
        self.model.delete()
        for action in [self.model.suspend, self.model.delete, lambda: self.model.resume([True])]:
            with self.subTest(action=action), self.assertRaises(LookupError):
                action()

    def test_invalid_probe_input_rejected_before_runtime_change(self):
        for replies in [[], [1], ['ready']]:
            with self.subTest(replies=replies), self.assertRaises(ValueError):
                self.model.resume(replies)
        self.assertFalse(self.model.actor_running)

    def test_demo_follows_normal_lifecycle(self):
        result = demo()
        self.assertEqual(result['create'], {'phase': 'Suspended', 'ready': False})
        self.assertEqual(result['first_resume']['probes'], 2)
        self.assertEqual(result['second_resume']['probes'], 0)
        self.assertTrue(result['suspend']['workspace_ready'])
        self.assertEqual(result['delete'], {'task_exists': False, 'actor_exists': False, 'templates': 0})


if __name__ == '__main__':
    unittest.main()
