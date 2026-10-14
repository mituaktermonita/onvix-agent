import argparse
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import deploy
from daytona import DaytonaNotFoundError

IMAGE = 'ghcr.io/mituaktermonita/onvix-agent:sha-' + 'a' * 40


def config():
    result = {key: 'fixture-value' for key in deploy.REQUIRED}
    result.update(OWNER_ID='12345', TG_CHAT_ID='-10012345', TG_TOPIC_CODING='2', TG_TOPIC_WRITING='3',
                  TG_TOPIC_VIDEO='4', TELEGRAM_BOT_TOKEN='bot-one', CONTROL_BOT_TOKEN='bot-two')
    return result


class ConfigTests(unittest.TestCase):
    def test_missing_auto_keys_are_saved_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / '.env'
            file.write_text('\n'.join(f'{key}={value}' for key, value in config().items()))
            first = deploy.load_config(file)
            second = deploy.load_config(file)
            for key in deploy.AUTO:
                self.assertEqual(first[key], second[key])
                self.assertGreaterEqual(len(first[key]), 24)
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_invalid_topics_and_single_bot_rejected(self):
        for updates in [{'TG_TOPIC_VIDEO': '3'}, {'TG_TOPIC_CODING': '1'},
                        {'OWNER_ID': 'oops'}, {'CONTROL_BOT_TOKEN': 'bot-one'}]:
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as directory:
                values = config() | updates
                file = Path(directory) / '.env'
                file.write_text('\n'.join(f'{key}={value}' for key, value in values.items()))
                with self.assertRaises(ValueError):
                    deploy.load_config(file)

    def test_image_rejects_latest_and_accepts_immutable(self):
        self.assertEqual(deploy.validate_image(IMAGE), IMAGE)
        for value in ['node:20', 'ghcr.io/mituaktermonita/onvix-agent:latest', IMAGE + ';id']:
            with self.assertRaises(ValueError):
                deploy.validate_image(value)

    def test_shell_quoting_round_trip(self):
        import subprocess
        value = "a'\"$HOME;`whoami`#value"
        content = deploy.shell_environment({'SECRET': value}).decode()
        result = subprocess.check_output(['bash', '-c', content + '\nprintf %s "$SECRET"'], text=True)
        self.assertEqual(result, value)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.list.return_value = []
        self.client.volume.get.return_value = SimpleNamespace(id='vol', state='ready')
        self.args = argparse.Namespace(name='onvix-agents', image=IMAGE, volume='data')

    def test_auth_or_network_error_never_creates(self):
        self.client.get.side_effect = RuntimeError('network down')
        with self.assertRaises(RuntimeError):
            deploy.get_or_create(self.client, self.args)
        self.client.create.assert_not_called()

    def test_old_image_refused_without_stopping_or_deleting(self):
        existing = Mock(labels={})
        self.client.get.return_value = existing
        with self.assertRaisesRegex(RuntimeError, 'different/untracked'):
            deploy.get_or_create(self.client, self.args)
        existing.start.assert_not_called()
        existing.stop.assert_not_called()
        existing.delete.assert_not_called()
        self.client.create.assert_not_called()

    def test_not_found_creates_pinned_image_with_no_secret_env(self):
        self.client.get.side_effect = DaytonaNotFoundError('missing')
        deploy.get_or_create(self.client, self.args)
        params = self.client.create.call_args.args[0]
        self.assertEqual(params.image, IMAGE)
        self.assertEqual(params.os_user, 'root')
        self.assertFalse(params.env_vars)
        self.assertEqual(params.auto_delete_interval, -1)

    def test_existing_correct_image_reused(self):
        existing = Mock(labels={'onvix.image': IMAGE, 'onvix.volume': 'data'}, state='stopped', id='own', public=True)
        self.client.get.return_value = existing
        self.assertIs(deploy.get_or_create(self.client, self.args), existing)
        existing.start.assert_called_once_with(timeout=180)
        self.client.create.assert_not_called()

    def test_legacy_live_volume_writer_blocks_create(self):
        other = SimpleNamespace(id='old', name='legacy', state='started')
        self.client.list.return_value = [other]
        self.client.get.side_effect = [DaytonaNotFoundError('missing'), SimpleNamespace(volumes=[SimpleNamespace(volume_id='vol')])]
        with self.assertRaisesRegex(RuntimeError, 'active sandbox'):
            deploy.get_or_create(self.client, self.args)
        self.client.create.assert_not_called()

    def test_remote_nonzero_is_failure_and_does_not_echo_secrets(self):
        sandbox = Mock()
        sandbox.process.exec.return_value = SimpleNamespace(exit_code=1, result='a-secret-token')
        with self.assertRaises(RuntimeError) as caught:
            deploy.checked_exec(sandbox, 'safe-command')
        self.assertNotIn('a-secret-token', str(caught.exception))

    def test_unready_volume_is_bounded(self):
        self.client.get.side_effect = DaytonaNotFoundError('missing')
        self.client.volume.get.return_value = SimpleNamespace(id='vol', state='pending')
        with patch('deploy.time.monotonic', side_effect=[0, 121]):
            with self.assertRaisesRegex(RuntimeError, 'did not become ready'):
                deploy.get_or_create(self.client, self.args)
        self.client.create.assert_not_called()
