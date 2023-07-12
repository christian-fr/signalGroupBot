import json
import logging
import os
import traceback
from dotenv import load_dotenv
from signalBot.mailUtil import get_new_mail
from signalBot.signalBot import run_signal_bot_receive, process_signal_msgs_to_mail, process_mail_to_signal_msg
from signalBot.util import cleanup_attachments, startup_logger, LOGGER

load_dotenv()

startup_logger(LOGGER, log_level=logging.DEBUG)

if __name__ == '__main__':
    cleanup_attachments()
    msgs = run_signal_bot_receive()
    sent_mails = process_signal_msgs_to_mail(msgs)
    try:
        LOGGER.info(
            f'{sent_mails=}, {len(msgs.values())=}, {len(json.loads(os.getenv("MAIL_ADDRESS_LIST_FORWARD_TO")))=}')
        assert sent_mails == len(json.loads(os.getenv("MAIL_ADDRESS_LIST_FORWARD_TO"))) * len(msgs.values())
    except AssertionError as err:
        LOGGER.error(traceback.format_exc())

    try:
        new_mails = get_new_mail(json.loads(os.getenv('MAIL_ADDRESS_LIST_FORWARD_FROM')))
        [process_mail_to_signal_msg(new_mail) for new_mail in new_mails]
    except Exception:
        LOGGER.error(traceback.format_exc())
