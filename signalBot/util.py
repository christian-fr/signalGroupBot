import datetime
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, List, Optional, Any


def startup_logger(logger, log_level=logging.DEBUG):
    """
    CRITICAL: 50, ERROR: 40, WARNING: 30, INFO: 20, DEBUG: 10, NOTSET: 0
    """
    logging.basicConfig(level=log_level)
    log_file = Path(f"./log/log_{__name__}.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file)
    fh.setLevel(log_level)
    fh_format = logging.Formatter('%(name)s\t%(module)s\t%(funcName)s\t%(asctime)s\t%(lineno)d\t'
                                  '%(levelname)-8s\t%(message)s')
    fh.setFormatter(fh_format)
    logger.addHandler(fh)


@dataclass
class Email:
    subject: str
    mail_from: str
    mail_to: str
    timestamp: datetime.datetime
    mail_cc: Optional[List[str]] = None
    mail_bcc: Optional[List[str]] = None
    body_list: List[str] = None
    attachments_list: List[bytes] = None

    def __str__(self):
        return f'{self.timestamp}; {self.mail_from}, {self.subject}'


def prepare_mail_for_signal(mail_obj: Email, tmpdir: Path) -> Tuple[str, List[Path]]:
    out_str = f'Subject: {mail_obj.subject}\n' \
              f'Date: {mail_obj.timestamp.strftime("%a, %d %b %Y %H:%M:%S %z")}\n' \
              f'From: {mail_obj.mail_from}\n' \
              f'To: {mail_obj.mail_to}\n'
    if mail_obj.mail_cc is not None:
        out_str += f'Cc: {", ".join(mail_obj.mail_cc)}\n'
    if mail_obj.mail_bcc is not None:
        out_str += f'Bcc: {", ".join(mail_obj.mail_bcc)}\n'
    out_str += '\n' + '=' * 20 + '\n\n'
    out_str += '\n'.join(mail_obj.body_list)

    temp_file_list = []

    if mail_obj.attachments_list:
        out_str += '\n' + '-' * 20 + '\n\n'
        file_names = [str(a[0]) for a in mail_obj.attachments_list if a[0] is not None]
        out_str += f'Attachments: {", ".join(file_names)}\n'
        for file_name, file_data in mail_obj.attachments_list:
            if file_name is None:
                continue
            temp_path = Path(tmpdir, file_name)
            temp_path.write_bytes(file_data)
            temp_file_list.append(temp_path)
    out_str += '\n'
    out_str += '=' * 20 + '\n'
    return out_str, temp_file_list


def cleanup_attachments():
    attachment_path = Path(os.getenv('SIGNAL_CONFIG'), "attachments")
    files_list = flatten([[Path(d_p, f) for f in f_n] for d_p, _, f_n in os.walk(attachment_path)])

    for f in files_list:
        ctime_diff = datetime.datetime.now() - datetime.datetime.fromtimestamp(os.stat(f).st_ctime)
        mtime_diff = datetime.datetime.now() - datetime.datetime.fromtimestamp(os.stat(f).st_mtime)
        if ctime_diff.total_seconds() > 0 and mtime_diff.total_seconds() > 0:
            ts_diff = min(ctime_diff, mtime_diff)
            if ts_diff.days < 5:
                continue
        os.remove(f)
        LOGGER.info(f"removed file: {f.absolute()}")


def convert_epoch_timestamp_into_str(epoch_timestamp):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(epoch_timestamp) / 1000))


def run_signal_cli_command(cmd: List[str], cli_exec_path: str, config_path: str, verbose: bool = False,
                           **kwargs) -> Any:
    base_cmd = [cli_exec_path, "--config", config_path]
    if verbose:
        base_cmd.append('-v')
    full_cmd = base_cmd + cmd
    LOGGER.info(f'{" ".join(full_cmd)=}')
    return subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def cmd_send_to_group(text: str, recipient_group_id: str, base_cmd: Optional[List[str]] = None,
                      attachment_cmd: Optional[List[str]] = None) -> List[str]:
    if base_cmd is None:
        base_cmd = cmd_base_send()
    if attachment_cmd is None:
        attachment_cmd = []
    return cmd_full(base_cmd, ['-g', recipient_group_id], text, attachment_cmd, [])


def cmd_send_to_user_number(text: str, recipient_user_number: str, base_cmd: Optional[List[str]] = None,
                            attachment_cmd: Optional[List[str]] = None) -> List[str]:
    if base_cmd is None:
        base_cmd = cmd_base_send()
    if attachment_cmd is None:
        attachment_cmd = []
    return cmd_full(base_cmd, [], text, attachment_cmd, [recipient_user_number])


def cmd_full(base_cmd: List[str], group_cmd: List[str], text: str, attachment_cmd: List[str],
             user_number_cmd: List[str]) -> List[str]:
    return [*base_cmd, *group_cmd, '-m', shell_quote(text), *attachment_cmd, *user_number_cmd]


def cmd_add_attachment(attachment_path: Optional[Path]) -> List[Optional[str]]:
    if attachment_path is not None:
        return ['-a', str(attachment_path.absolute())]
    else:
        return []


def cmd_base_send(signal_number: Optional[str] = None):
    if signal_number is None:
        signal_number = os.getenv("SIGNAL_NUMBER")
        try:
            assert signal_number is not None
        except AssertionError:
            LOGGER.error("no signal_number found in env variables")
    return ['-a', signal_number, '-o', 'json', 'send']


def send_message(cmd: List[str], config_path: Optional[str] = None, cli_exec_path: Optional[str] = None) -> Any:
    if cli_exec_path is None:
        cli_exec_path = os.getenv("SIGNAL_CLI")
    if config_path is None:
        config_path = os.getenv("SIGNAL_CONFIG")
    try:
        assert cli_exec_path is not None and config_path is not None
    except AssertionError:
        LOGGER.error(f"ERROR: {cli_exec_path=}, {config_path=}")
    return run_signal_cli_command(cmd=cmd, cli_exec_path=cli_exec_path, config_path=config_path)


def shell_quote(item):
    # source: https://stackoverflow.com/questions/70814835/python3-shell-quoting-less-complicated-than-shlex-quote-output
    if not item:
        return "''"
    # Pre-escape any escape characters
    item = item.replace('\\', r'\\')
    if "'" not in item:
        # Contains no single quotes, so we can
        # single-quote the output.
        return f"'{item}'"
    else:
        # Enclose in double quotes. We must escape
        # "$" and "!", which which normally trigger
        # expansion in double-quoted strings in shells.
        # If it contains double quotes, escape them, also.
        item = item.replace(r'$', r'\$') \
            .replace(r'!', r'\!') \
            .replace(r'"', r'\"') \
            .replace(r'|', r'\|') \
            .replace(r'>', r'\>') \
            .replace(r'<', r'\<')
        return f'"{item}"'


def reformat_timestamp(ts: int) -> str:
    return datetime.datetime.fromtimestamp(float(ts) / 1000).strftime('%a, %d %b %Y %H:%M:%S %z')


LOGGER = logging.getLogger('debugger')


def flatten(ll: list) -> list:
    return [a for e in ll for a in e]
