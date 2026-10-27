"""Cache telemetry correlates concurrent request completions using numeric indices only."""
import logging

import pytest

from plugin.model_usage import log_usage


@pytest.mark.parametrize('index',[None,0,-1,True,'7',1.5])
def test_non_request_indices_not_emitted(index,caplog):
    """Do not make malformed values look like legitimate request associations."""
    with caplog.at_level(logging.INFO):
        result=log_usage(logging.getLogger(__name__),{'usage':{'prompt_tokens':100,'cached_tokens':10}},transport='tools',request_index=index)
    assert 'request_index' not in result


def test_out_of_order_completions_keep_actual_request_index(caplog):
    """Completion order does not change the explicit per-request captured index."""
    with caplog.at_level(logging.INFO):
        second=log_usage(logging.getLogger(__name__),{'usage':{'prompt_tokens':200,'cached_tokens':150}},transport='tools',request_index=2)
        first=log_usage(logging.getLogger(__name__),{'usage':{'prompt_tokens':100,'cached_tokens':10}},transport='tools',request_index=1)
    assert (second['request_index'],first['request_index'])==(2,1)
    assert (second['cache_hit_percent'],first['cache_hit_percent'])==(75,10)
    assert 'request_index' in caplog.text
