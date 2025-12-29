from dataclasses import dataclass

import pytest

from waku.cqrs import Request, Response
from waku.cqrs.contracts import IRequest
from waku.cqrs.utils import get_request_response_type


@dataclass(frozen=True)
class UserResponse(Response):
    user_id: str


@dataclass(frozen=True)
class DirectIRequest(IRequest[UserResponse]):
    pass


@dataclass(frozen=True, kw_only=True)
class RequestSubclass(Request[UserResponse]):
    name: str


@dataclass(frozen=True, kw_only=True)
class NestedRequestSubclass(RequestSubclass):
    extra: int = 0


class NoResponseType(IRequest):
    pass


def test_extracts_response_from_irequest() -> None:
    result = get_request_response_type(DirectIRequest)
    assert result is UserResponse


def test_extracts_response_from_request_subclass() -> None:
    result = get_request_response_type(RequestSubclass)
    assert result is UserResponse


def test_extracts_response_from_nested_inheritance() -> None:
    result = get_request_response_type(NestedRequestSubclass)
    assert result is UserResponse


def test_raises_type_error_when_no_response_type() -> None:
    with pytest.raises(TypeError, match='Could not extract response type'):
        get_request_response_type(NoResponseType)
