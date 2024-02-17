import base64
import json
import os
import traceback
from collections import defaultdict
from pathlib import Path
from tempfile import mkdtemp
from typing import List, Dict, Union, Optional, Tuple
from signalBot.mailUtil import send_mail
from signalBot.util import Email, convert_epoch_timestamp_into_str, \
    run_signal_cli_command, send_message, reformat_timestamp, cmd_send_to_user_number, cmd_add_attachment, \
    cmd_send_to_group, LOGGER, flatten
from crypt import METHOD_BLOWFISH

def add_timestamp_str(msg_dict_envelope: dict) -> dict:
    msg_dict_envelope['timestamp_str'] = convert_epoch_timestamp_into_str(msg_dict_envelope['timestamp'])
    return msg_dict_envelope


def get_sender_from_uuid(sender_uuid: str) -> Optional[str]:
    address_dict = json.loads(os.getenv('SIGNAL_ADDRESS_DICT'))
    id_dict = {e['id']: e['name'] for e in address_dict if e['id'] is not None}
    if sender_uuid in id_dict:
        return id_dict[sender_uuid]
    else:
        return None


def get_sender_from_number(sender_number: str) -> Optional[str]:
    address_dict = json.loads(os.getenv('SIGNAL_ADDRESS_DICT'))
    number_dict = {e['number']: e['name'] for e in address_dict if e['number'] is not None}
    if sender_number in number_dict:
        return number_dict[sender_number]
    else:
        return None


def add_sender_str(msg_dict_envelope: dict) -> dict:
    tmp_name = None

    sender_from_number = get_sender_from_number(msg_dict_envelope['sourceNumber'])
    sender_from_uuid = get_sender_from_uuid(msg_dict_envelope['sourceUuid'])
    if sender_from_number is not None and sender_from_number != sender_from_uuid:
        send_message_admin(
            f'sender from number does not match sender from uuid: \n'
            f'{sender_from_uuid=}\n'
            f'{sender_from_number=}\n'
            f'{json.dumps(msg_dict_envelope)}')

    if get_sender_from_number(msg_dict_envelope['sourceNumber']) is not None:
        tmp_name = get_sender_from_number(msg_dict_envelope['sourceNumber'])
    elif get_sender_from_uuid(msg_dict_envelope['sourceUuid']) is not None:
        tmp_name = get_sender_from_uuid(msg_dict_envelope['sourceUuid'])

    if tmp_name is not None:
        msg_dict_envelope['source'] = tmp_name
    else:
        msg_dict_envelope['source'] = '[unknown user]'
        send_message_admin(text="unknown user /// " + json.dumps(msg_dict_envelope))
        LOGGER.error(f"Unknown: {msg_dict_envelope['source']=}  //// {msg_dict_envelope['sourceNumber']}")
        msg_dict_envelope['source'] = 'UNKNOWN'

    return msg_dict_envelope


def add_payload_data(msg_dict_envelope: dict) -> dict:
    if 'dataMessage' in msg_dict_envelope and 'attachments' in msg_dict_envelope['dataMessage']:
        attachment_path = Path(os.getenv('SIGNAL_CONFIG'), "attachments")
        assert attachment_path.exists()

        for attachment in msg_dict_envelope['dataMessage']['attachments']:
            attachment_file = Path(attachment_path, attachment['id'])
            assert attachment_file.exists()
            if os.stat(attachment_file).st_size < 1e7:
                with open(attachment_file, "rb") as att_file:
                    attachment['base64'] = base64.b64encode(att_file.read()).decode('utf-8')
                LOGGER.info(f'file {attachment_file.absolute()=}: base64 written')
            else:
                LOGGER.error(
                    f'file {attachment_file.absolute()=}: size too big: {os.stat(attachment_file).st_size=}')
                attachment['base64'] = None
    return msg_dict_envelope


def process_cli_response(response_bytes: bytes) -> Dict[str, List[Dict[str, Union[None, str, int, dict]]]]:
    results_json_list = [json.loads(e.strip()) for e in response_bytes.decode('utf-8').split('\n') if
                         e.strip() != '' and 'envelope' in e]

    results_json_dict = defaultdict(list)

    for msg_dict in results_json_list:
        if 'exception' in msg_dict:
            error_msg = f"exception found in message: {msg_dict}"
            LOGGER.error(error_msg)
            send_message_admin(error_msg)
        envelope = msg_dict['envelope']

        receipt_message = 'receiptMessage' in envelope
        data_message = 'dataMessage' in envelope
        data_message__reaction = False
        data_message__reaction__emoji = False
        data_message__quote = False
        if data_message:
            data_message__reaction = 'reaction' in envelope['dataMessage']
            data_message__quote = 'quote' in envelope['dataMessage']
        if data_message__reaction:
            data_message__reaction__emoji = 'emoji' in envelope['dataMessage']['reaction']

        pattern = tuple([int(i) for i in
                         [receipt_message, data_message, data_message__reaction, data_message__reaction__emoji,
                          data_message__quote]])

        if pattern == (1, 0, 0, 0, 0):
            continue
        elif pattern not in [(0, 1, 0, 0, 0), (0, 1, 0, 0, 1), (0, 1, 1, 1, 0)]:
            error_msg = f"unknown pattern: {pattern}; envelope: {json.dumps(envelope)}"
            LOGGER.error(error_msg)
            send_message_admin(error_msg)
            continue

        envelope = add_sender_str(envelope)
        envelope = add_timestamp_str(envelope)
        envelope = add_payload_data(envelope)
        envelope = add_quote_message(envelope)
        envelope = add_emoji_reaction(envelope)

        results_json_dict[msg_dict['account']].append(envelope)

    for msg_list in results_json_dict.values():
        msg_list.sort(key=lambda x: x['timestamp'])
    return results_json_dict


def add_quote_message(msg_dict_envelope: dict) -> dict:
    if "quote" in msg_dict_envelope['dataMessage']:
        ref_number = msg_dict_envelope['dataMessage']['quote']['authorNumber']
        ref_uuid = msg_dict_envelope['dataMessage']['quote']['authorUuid']

        ref_sender_number = get_sender_from_number(ref_number)
        ref_sender_uuid = get_sender_from_uuid(ref_uuid)

        ref_sender = ref_sender_number
        if ref_sender is None:
            ref_sender = ref_sender_uuid
        if ref_sender is None:
            ref_sender = '[unknown sender]'

        ref_text = msg_dict_envelope['dataMessage']['quote']['text']

        tmp_text = "[[ quote answer ]]\n"
        tmp_text += f"to message from sender: {ref_sender}\n\n"
        tmp_text += f"{ref_text}\n"
        tmp_text = '\n'.join([f'> {li}' for li in tmp_text.split('\n')])
        msg_dict_envelope['dataMessage']['message'] = msg_dict_envelope['dataMessage']['message'] + '\n\n' + tmp_text
    return msg_dict_envelope


def add_emoji_reaction(msg_dict_envelope: dict) -> dict:
    if "reaction" in msg_dict_envelope['dataMessage']:
        if "emoji" in msg_dict_envelope['dataMessage']['reaction']:
            ref_number = msg_dict_envelope['dataMessage']['reaction']['targetAuthorNumber']
            ref_uuid = msg_dict_envelope['dataMessage']['reaction']['targetAuthorUuid']

            ref_sender_number = get_sender_from_number(ref_number)
            ref_sender_uuid = get_sender_from_uuid(ref_uuid)

            ref_sender = ref_sender_number
            if ref_sender is None:
                ref_sender = ref_sender_uuid
            if ref_sender is None:
                ref_sender = '[unknown sender]'

            ref_timestamp = msg_dict_envelope['dataMessage']['reaction']['targetSentTimestamp']

            emoji = msg_dict_envelope['dataMessage']['reaction']['emoji']
            if 'message' not in msg_dict_envelope['dataMessage'] or msg_dict_envelope['dataMessage']['message'] is None:
                msg_dict_envelope['dataMessage']['message'] = ""
            msg_dict_envelope['dataMessage']['message'] += "[[ emoji reaction ]]\n"
            msg_dict_envelope['dataMessage']['message'] += f"to message from sender: {ref_sender}\n"
            msg_dict_envelope['dataMessage']['message'] += f"to message from date: " \
                                                           f"{reformat_timestamp(ref_timestamp)}\n"
            msg_dict_envelope['dataMessage']['message'] += f"emoji: {emoji}\n"
    return msg_dict_envelope


def receive_messages(signal_number: str, cli_exec_path: str, config_path: str, verbose: bool = False) -> dict:
    """
    receive messages
    """

    # get response (byte string) of signal-cli command "receive"
    response = run_signal_cli_command(['-a', signal_number, '-o', 'json', 'receive'], cli_exec_path, config_path,
                                      verbose)
    if response.stderr is not None:
        for address in json.loads(os.getenv('MAIL_ADMIN_ADDRESS')):
            send_mail(address, f'{response.stdout=}\n{response.stderr=}', 'signalGroupBot ERROR', None)
    LOGGER.info(f'{response.stderr=}')
    LOGGER.info(f'{response.stdout=}')
    LOGGER.info(f'{response.returncode=}')

    if response.returncode != 0:
        LOGGER.error(
            'returncode: {0}\nstdout: {1}\nstderr: {2}'.format(response.returncode, response.stdout, response.stderr))
    return dict(process_cli_response(response.stdout))


def run_signal_bot_receive():
    return receive_messages(os.getenv('SIGNAL_NUMBER'), os.getenv('SIGNAL_CLI'), os.getenv('SIGNAL_CONFIG'),
                            verbose=True)


def process_message_text(message: dict, msg: str):
    if 'dataMessage' in message:
        if 'message' in message['dataMessage']:
            if message['dataMessage']['message'] is not None:
                msg += f"{message['dataMessage']['message']}\n"
            else:
                msg += '[kein Text]\n'
    msg += f"\n===================="
    return msg


def process_attachments(message: dict, msg: str):
    signal_attachments = []
    if 'dataMessage' in message:
        if 'attachments' in message['dataMessage']:
            for signal_attachment in message['dataMessage']['attachments']:
                file_name = signal_attachment['filename']
                if file_name is None:
                    file_name = signal_attachment['id']
                base64_str = signal_attachment['base64']
                if base64_str is None:
                    msg += f'\n[Anhang {file_name} größer als 10MB, wird nicht verschickt]\n'
                else:
                    msg += f'\n[Anhang: {file_name}]\n'
                    signal_attachments.append((file_name, base64_str))
    return msg, signal_attachments


def get_group_id(message: dict) -> Optional[str]:
    try:
        group_id = message['dataMessage']['groupInfo']['groupId']
    except Exception:
        LOGGER.debug(traceback.format_exc())
        group_id = None
    return group_id


def prepare_signal_msgs_for_mail(messages: Dict[str, List[Dict[str, Union[None, str, int, dict]]]],
                                 filter_group_id: Optional[str]) -> \
        Tuple[List[Tuple[str, str, List[Tuple[str, str]]]], List[Tuple[str, str, List[Tuple[str, str]]]]]:
    result, filtered = [], []
    for message in flatten(list(messages.values())):
        msg_group_id = get_group_id(message)

        subject = f"drahtesel*innen / {message['timestamp_str']} / {message['source']}"
        msg = f"Date: {reformat_timestamp(int(message['timestamp']))}\n" \
              f"From: {message['source']}\n" \
              f"To: drahtesel*innen signal chat\n\n" \
              f"====================\n\n"

        msg = process_message_text(message, msg)
        msg, signal_attachments = process_attachments(message, msg)
        return_tuple = (subject, msg, signal_attachments)
        if msg_group_id == filter_group_id:
            result.append(return_tuple)
        else:
            filtered.append(return_tuple)
    return result, filtered


def process_signal_msgs_to_mail(messages: Dict[str, List[Dict[str, Union[None, str, int, dict]]]]) -> int:
    mail_msgs, filtered_messages = prepare_signal_msgs_for_mail(messages, os.getenv('SIGNAL_GROUP_ID'))
    return send_signal_msgs_via_mail(mail_msgs)


def send_signal_msgs_via_mail(messages: List[Tuple[str, str, List[Tuple[str, str]]]]) -> int:
    done = 0
    try:
        for subject, msg, signal_attachments in messages:
            for mail_address in json.loads(os.getenv('MAIL_ADDRESS_LIST_FORWARD_TO')):
                rc = send_mail(mail_address, msg, subject, signal_attachments)
                if rc:
                    done += 1
    except Exception:
        LOGGER.error(traceback.format_exc())
    finally:
        return done


def process_mail_to_signal_msg(mail: Email):
    tmp_str = f"Subject: {mail.subject}\nDate: {mail.timestamp}\nFrom: {mail.mail_from}\n"
    if mail.mail_cc is not None:
        tmp_str += f"Cc: {mail.mail_cc}\n"
    if mail.mail_bcc is not None:
        tmp_str += f"Bcc: {mail.mail_bcc}\n"
    tmp_str += f"To: {mail.mail_to}\n====================\n\n"
    if mail.body_list is not None:
        tmp_str += '\n\n'.join(mail.body_list) + '\n'

    if mail.attachments_list:
        tmp_str += f'Attachments:\n<'
        att_filenames = [att_filename for att_filename, _ in mail.attachments_list]
        tmp_str += '>\n<'.join(att_filenames) + '>'
    send_message_group_id(recipient_group_id=os.getenv('SIGNAL_GROUP_ID'), text=tmp_str)

    if mail.attachments_list is not None:
        for att_filename, att_payload in mail.attachments_list:
            tmp_dir = Path(mkdtemp())
            tmp_file = Path(tmp_dir, att_filename)
            try:
                tmp_file.write_bytes(att_payload)
                tmp_str = f'<{att_filename}>'
                send_message_group_id(recipient_group_id=os.getenv('SIGNAL_GROUP_ID'), attachment=tmp_file,
                                      text=tmp_str)
            finally:
                tmp_file.unlink(missing_ok=True)
                tmp_dir.rmdir()


def send_message_admin(text: str, attachment: Optional[Path] = None) -> None:
    send_message_user_number(recipient_user_number=os.getenv("SIGNAL_ADMIN_NUMBER"), text=text, attachment=attachment)


def send_message_user_number(recipient_user_number: str, text: str, attachment: Optional[Path] = None):
    send_message(cmd_send_to_user_number(text=text, recipient_user_number=recipient_user_number, base_cmd=None,
                                         attachment_cmd=cmd_add_attachment(attachment)))


def send_message_group_id(recipient_group_id: str, text: str, attachment: Optional[Path] = None):
    send_message(cmd_send_to_group(text=text, recipient_group_id=recipient_group_id, base_cmd=None,
                                   attachment_cmd=cmd_add_attachment(attachment)))


if __name__ == '__main__':
    pass
