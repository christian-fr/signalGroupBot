import datetime
import email
import logging
import os
import traceback
from collections import defaultdict
from email import message
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from imaplib import IMAP4_SSL
from mimetypes import guess_type
from pathlib import Path
from smtplib import SMTP_SSL
from typing import List, Optional, Tuple, Dict

from dotenv import load_dotenv

from signalBot.util import startup_logger, Email

load_dotenv()

LOGGER = logging.getLogger('debugger')
startup_logger(LOGGER, log_level=logging.DEBUG)


def process_content(part: email.message.Message, file_name: Optional[str]) -> Tuple[List[str], List[Tuple[str, str]]]:
    body_list = []
    attachments_list = []
    content_type = part.get_content_type()
    raw_types = ["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/gif",
                 "image/jpeg", "image/jpg", "image/png", "application/pdf",
                 "application/vnd.oasis.opendocument.text"]
    text_types = ["text/plain", "text/markdown"]
    ignore_types = ["text/html"]
    multipart_types = ["multipart/mixed", "multipart/alternative", "multipart/related"]
    if content_type in raw_types + text_types + ignore_types:
        raw_data = part.get_payload(decode=True)
        if content_type in text_types and file_name is None:
            body_list.append(part.get_payload())
        elif content_type in raw_types or (content_type in text_types and file_name is not None):
            attachments_list.append((file_name, raw_data))
        elif content_type in ignore_types:
            pass
        else:
            LOGGER.error(f'unknown content type (01): {content_type}')
    elif content_type in multipart_types:
        pass
    else:
        LOGGER.error(f'unknown content type (02): {content_type}')
    return body_list, attachments_list


def process_multipart(msg: email.message.Message) -> Tuple[List[str], List[Tuple[str, bytes]]]:
    body_list = []
    attachments_list = []
    for part in msg.walk():
        content_disposition = str(part.get("Content-Disposition"))
        file_name = None
        if content_disposition is not None:
            content_disposition = [c.strip() for c in content_disposition.split(';')]
            if any([s.startswith('filename="') for s in content_disposition]):
                tmp_list = None
                try:
                    tmp_list = [s for s in content_disposition if s.startswith('filename="')]
                    assert len(tmp_list) == 1
                    file_name = tmp_list[0][10:-1]
                except AssertionError as err:
                    LOGGER.error(tmp_list)
        part_body_list, part_attachments_list = process_content(part, file_name)
        body_list += part_body_list
        attachments_list += part_attachments_list
    return body_list, attachments_list


def get_unread_mail_uids(imap_ssl: IMAP4_SSL, mail_address_list: List[str], mailbox: Optional[str] = 'INBOX'):
    rc, capabilities = imap_ssl.login(os.getenv('MAIL_USER'), os.getenv('MAIL_PASS'))
    rc, _ = imap_ssl.select(readonly=False, mailbox=mailbox)
    found_mail_uid = {
        k: sorted(list(imap_ssl.uid('search', None, 'FROM', f'"{k}"', '(UNSEEN)')[1][0].split(b' ')), reverse=True)
        for k in mail_address_list}
    found_mail_uid = {k: v for k, v in found_mail_uid.items() if v != [b'']}
    return found_mail_uid


def get_mail_per_uid(imap_ssl: IMAP4_SSL, mail_uid_dict: Dict[str, List[bytes]]) -> Dict[str, List[bytes]]:
    mail_raw_dict = defaultdict(list)
    for mail_address, mail_uid_list in mail_uid_dict.items():
        for uid in mail_uid_list:
            typ, data = imap_ssl.uid('fetch', uid, '(RFC822)')
            mail_raw_dict[mail_address].append(data)
    return mail_raw_dict


def process_subject(msg: message):
    subject = None
    try:
        subject = email.header.decode_header(msg['Subject'])[0][0]
        if type(subject) == bytes:
            subject = subject.decode('utf-8')
    except Exception as err:
        LOGGER.error(f'{err=}')
        LOGGER.error(f'{traceback.format_exc()=}')
    return subject


def process_address_headers(msg: message):
    addr_fields = ['From', 'To', 'Cc', 'Bcc']
    addr_dict = {k: msg[k] for k in addr_fields if k in msg.keys()}
    sender = addr_dict['From']
    recipient = addr_dict['To']
    cc_list = None
    if 'Cc' in addr_dict.keys():
        cc_list = [m.strip() for m in addr_dict['Cc'].split(',')]
    bcc_list = None
    if 'Bcc' in addr_dict.keys():
        bcc_list = [m.strip() for m in addr_dict['Bcc'].split(',')]
    return sender, recipient, cc_list, bcc_list


def process_message(msg: message) -> Email:
    attachments_list = None
    subject = process_subject(msg)

    date = msg['Date']

    sender, recipient, cc_list, bcc_list = process_address_headers(msg)

    timestamp = datetime.datetime.strptime(date, '%a, %d %b %Y %H:%M:%S %z')
    mail_obj = Email(subject, sender, recipient, timestamp)
    mail_obj.mail_cc = cc_list
    mail_obj.mail_bcc = bcc_list

    if msg.is_multipart():
        body_list, attachments_list = process_multipart(msg)
        pass
    else:
        body_list = [msg.get_payload(decode=True).decode()]

    mail_obj.body_list = body_list
    mail_obj.attachments_list = attachments_list
    return mail_obj


def get_new_mail(mail_address_list: List[str], dump_raw_mails: bool = False) -> List[Email]:
    host = os.getenv('MAIL_IMAP_SERVER')
    port = int(os.getenv('MAIL_IMAP_PORT'))
    mailbox = os.getenv('MAIL_IMAP_MAILBOX')
    results_list = []
    with IMAP4_SSL(host=host, port=port) as M:
        try:
            address_uid_dict = get_unread_mail_uids(M, mail_address_list, mailbox)
            raw_mails_dict = get_mail_per_uid(M, address_uid_dict)
            for sender, msg_list in raw_mails_dict.items():
                if dump_raw_mails:
                    base_path = Path('./tests/context')
                    base_path.mkdir(exist_ok=True, parents=True)
                    [dump_mail_to_file(email.message_from_bytes(msg[0][1]),
                                       Path(base_path, f'mail{str(i).zfill(3)}.raw')) for i, msg in enumerate(msg_list)]
                [results_list.append(process_message(email.message_from_bytes(msg[0][1]))) for msg in msg_list]
        except Exception as err:
            LOGGER.error(traceback.print_exc())
    return results_list


def send_mail(to_address: str, body: str, subject: str, attachments: Optional[List[Tuple[str, str]]] = None) -> bool:
    if attachments is None:
        attachments = []

    msg = MIMEMultipart()
    user = os.getenv('MAIL_USER')
    msg['From'] = user
    msg['To'] = to_address
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = subject

    msg.attach(MIMEText(body))

    [msg.attach(prepare_attachment(f_n, p_l)) for f_n, p_l in attachments]
    smtp_ssl_send(msg=msg, to_address=to_address)
    return True


def smtp_ssl_send(msg: MIMEMultipart, to_address: str):
    host = os.getenv('MAIL_SMTP_SERVER')
    port = int(os.getenv('MAIL_SMTP_PORT'))
    user = os.getenv('MAIL_USER')
    password = os.getenv('MAIL_PASS')

    with SMTP_SSL(host=host, port=port) as server:
        server.set_debuglevel(1)
        server.login(user=user, password=password)
        server.sendmail(user, to_address, msg.as_string())
        server.quit()


def prepare_attachment(att_file_name: str, att_payload: str) -> MIMEBase:
    part = MIMEBase(*guess_type(att_file_name))
    part.set_payload(att_payload)
    part.add_header('Content-Transfer-Encoding', 'base64')
    part['Content-Disposition'] = f'attachment; filename="{att_file_name}"'
    return part


def dump_mail_to_file(mail_obj: email.message.Message, file_path: Path) -> None:
    file_path.write_bytes(mail_obj.as_bytes())


if __name__ == '__main__':
    pass
