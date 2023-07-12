import os
from unittest import TestCase
from unittest.mock import patch

from dotenv import load_dotenv


class Test(TestCase):
    def setUp(self) -> None:
        load_dotenv('context/.env_test')

    def test_prepare_signal_msgs_for_mail(self):
        with patch('signalBot.util.LOGGER', autospec=True) as mock_logger, \
                patch('signalBot.util.startup_logger', autospec=True) as mock_startup_logger, \
                patch('signalBot.signalBot.send_message_admin', autospec=True) as mock_send_message_admin:
            from signalBot.signalBot import process_cli_response
            from tests.context.json_data import cli_output_raw

            r = process_cli_response(cli_output_raw)

            from signalBot.signalBot import prepare_signal_msgs_for_mail
            prepare_signal_msgs_for_mail(r, filter_group_id=os.getenv('SIGNAL_GROUP_ID'))
        self.fail()

    def test_process_cli_response(self):
        with patch('signalBot.util.LOGGER', autospec=True) as mock_logger, \
                patch('signalBot.util.startup_logger', autospec=True) as mock_startup_logger, \
                patch('signalBot.signalBot.send_message_admin', autospec=True) as mock_send_message_admin:
            from signalBot.signalBot import process_cli_response
            from tests.context.json_data import cli_output_raw

            r = process_cli_response(cli_output_raw)
            self.fail()

    def test_send_signal_msgs_via_mail(self):
        res, fil = None, None
        with patch('signalBot.util.LOGGER', autospec=True) as mock_logger, \
                patch('signalBot.util.startup_logger', autospec=True) as mock_startup_logger, \
                patch('signalBot.signalBot.send_message_admin', autospec=True) as mock_send_message_admin:
            from signalBot.signalBot import process_cli_response
            from tests.context.json_data import cli_output_raw

            r = process_cli_response(cli_output_raw)

            from signalBot.signalBot import prepare_signal_msgs_for_mail
            res, fil = prepare_signal_msgs_for_mail(r, filter_group_id=os.getenv('SIGNAL_GROUP_ID'))
        with patch('signalBot.util.LOGGER', autospec=True) as mock_logger, \
                patch('signalBot.util.startup_logger', autospec=True) as mock_startup_logger, \
                patch('signalBot.mailUtil.SMTP_SSL', autospec=True) as mock_SMTP_SSL:
            from signalBot.signalBot import send_signal_msgs_via_mail
            send_signal_msgs_via_mail(res)
        self.fail()
