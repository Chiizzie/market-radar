import subprocess
import unittest
from unittest.mock import patch
from outcome_tracker import git

class PushRetryTests(unittest.TestCase):
    @patch('outcome_tracker.time.sleep')
    @patch('outcome_tracker.subprocess.run')
    def test_retry_identical_commit_without_force(self, run, sleep):
        error = subprocess.CalledProcessError(1, ['git','push'], stderr='remote: Internal Server Error')
        done = subprocess.CompletedProcess([], 0)
        run.side_effect = [error, done]
        args = ['push','origin','abc:refs/heads/radar-history']
        self.assertIs(git(args), done)
        self.assertEqual(run.call_args_list[0], run.call_args_list[1])
        self.assertNotIn('--force', run.call_args.args[0])
        sleep.assert_called_once_with(2)

    @patch('outcome_tracker.time.sleep')
    @patch('outcome_tracker.subprocess.run')
    def test_retry_bounded_and_failure_propagates(self, run, sleep):
        run.side_effect = subprocess.CalledProcessError(1, ['git','push'])
        with self.assertRaises(subprocess.CalledProcessError):
            git(['push','origin','abc:refs/heads/radar-alert-live'])
        self.assertEqual(run.call_count, 3)

    @patch('outcome_tracker.time.sleep')
    @patch('outcome_tracker.subprocess.run')
    def test_permanent_rejection_and_nonpush_not_retried(self, run, sleep):
        for args, message in [(['push'], 'non-fast-forward'), (['push'], '403'), (['commit-tree'], 'error')]:
            run.reset_mock()
            run.side_effect = subprocess.CalledProcessError(1, ['git']+args, stderr=message)
            with self.assertRaises(subprocess.CalledProcessError): git(args)
            self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()
