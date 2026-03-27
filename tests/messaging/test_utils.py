from dataclasses import dataclass
from typing import Generic, TypeVar

from waku.messaging import IRequest
from waku.messaging._introspection import get_response_type  # noqa: PLC2701
from waku.messaging.contracts.event import IEvent

_T = TypeVar('_T')


@dataclass(frozen=True)
class UserResponse:
    user_id: str


@dataclass(frozen=True)
class DirectIRequest(IRequest[UserResponse]):
    pass


@dataclass(frozen=True, kw_only=True)
class RequestSubclass(IRequest[UserResponse]):
    name: str


@dataclass(frozen=True, kw_only=True)
class NestedRequestSubclass(RequestSubclass):
    extra: int = 0


class NoResponseType(IRequest):
    pass


class GenericMiddle(IRequest[_T], Generic[_T]):
    pass


class UnboundRequest(GenericMiddle[_T]):
    pass


class SomeEvent(IEvent):
    pass


def test_extracts_response_from_irequest() -> None:
    result = get_response_type(DirectIRequest)
    assert result is UserResponse  # type: ignore[comparison-overlap]


def test_extracts_response_from_request_subclass() -> None:
    result = get_response_type(RequestSubclass)
    assert result is UserResponse  # type: ignore[comparison-overlap]


def test_extracts_response_from_nested_inheritance() -> None:
    result = get_response_type(NestedRequestSubclass)
    assert result is UserResponse  # type: ignore[comparison-overlap]


def test_resolves_none_type_for_bare_irequest() -> None:
    result = get_response_type(NoResponseType)
    assert result is type(None)


def test_returns_none_type_for_event() -> None:
    result = get_response_type(SomeEvent)
    assert result is type(None)


def test_returns_none_type_for_unbound_typevar_request() -> None:
    result = get_response_type(UnboundRequest)
    assert result is type(None)
