"""Forward acceptance is not message-ID proof of scheduled delivery."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def ledger():
    """Load the pure evidence accumulator without importing a runtime plugin."""
    path=Path(__file__).resolve().parents[1]/'plugin/task_delivery.py'
    spec=importlib.util.spec_from_file_location('transfer_task_delivery',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name',['qq_forward_messages','qq_paste_message'])
def test_transfers_require_explicit_delivery_and_native_output_ids(ledger,name):
    """A native accepted/null acknowledgement may not complete a scheduled send."""
    outcome=ledger.fresh()
    ledger.observe(outcome,name,{'ok':True,'accepted':True,'delivered':False,'outcome':'accepted','delivery_message_ids':[]})
    assert outcome['delivery_attempted'] is True
    assert outcome['delivery_unknown'] is True
    assert outcome['delivery_confirmed'] is False
    assert ledger.can_finish(outcome) is False
    confirmed=ledger.fresh()
    ledger.observe(confirmed,name,{'ok':True,'delivered':True,'delivery_message_ids':['12345']})
    assert confirmed['delivery_confirmed'] is True
    assert confirmed['delivery_message_ids']==['12345']
    assert ledger.can_finish(confirmed) is True


def test_partial_transfer_failure_cannot_be_hidden_by_prior_receipts(ledger):
    """Some confirmed messages do not prove an entire multi-message transfer."""
    outcome=ledger.fresh()
    ledger.observe(outcome,'qq_forward_messages',{'ok':False,'delivered':False,'error':'transfer_partial_failure','delivery_message_ids':['23456']})
    assert outcome['delivery_failed'] is True
    assert ledger.can_finish(outcome) is False


def test_copy_is_not_an_outbound_action(ledger):
    """Saving a local clipboard never claims external message delivery."""
    outcome=ledger.fresh()
    ledger.observe(outcome,'qq_copy_message',{'ok':True,'clipboard_id':'opaque'})
    assert outcome==ledger.fresh()
