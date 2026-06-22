import pytest
import enet_gw_api_py_rs


def test_sum_as_string():
    assert enet_gw_api_py_rs.sum_as_string(1, 1) == "2"
