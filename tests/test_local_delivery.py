"""Local persistence never turns into a fabricated remote delivery commitment."""
from plugin import task_delivery as delivery


def test_recorded_local_task_can_finish_without_qq_receipt():
    """Retain successful work locally while keeping QQ delivery evidence false."""
    outcome = delivery.fresh()
    delivery.observe(outcome, 'local_record', {'transport': 'local', 'status': 'local_recorded', 'recorded': True})
    assert outcome['local_output_recorded'] is True
    assert outcome['delivery_attempted'] is False
    assert outcome['delivery_confirmed'] is False
    assert outcome['delivery_message_ids'] == []
    assert delivery.can_finish(outcome) is True


def test_local_record_failure_cannot_finish_or_be_masked_by_later_output():
    """Failure sticks, with a safe code and no automatic retry instruction."""
    outcome = delivery.fresh()
    delivery.observe(outcome, 'local_record', {'ok': False, 'error': 'local_write_failed'})
    delivery.observe(outcome, 'local_record', {'transport': 'local', 'status': 'local_recorded', 'recorded': True})
    assert delivery.can_finish(outcome) is False
    assert delivery.completion_failure(outcome) == 'task_local_output_failed'
    assert outcome['delivery_confirmed'] is False


def test_local_reply_does_not_create_remote_delivery_unknown():
    """A reviewed local reply records output separately from native transport flags."""
    outcome = delivery.fresh()
    delivery.observe(outcome, 'reply_now', {'ok': True, 'transport': 'local', 'status': 'local_recorded',
                                         'recorded': True, 'delivered': False})
    assert outcome['local_output_recorded'] is True
    assert not outcome['delivery_unknown']
    assert delivery.can_finish(outcome)
